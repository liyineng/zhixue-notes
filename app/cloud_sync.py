"""iCloud folder transport; vector-clock registers, never a shared live database.

Each device publishes an atomic immutable manifest after its content-addressed files.
Concurrent register versions survive until explicitly resolved. Local state and app
rows commit together, so a crash cannot turn an imported edit into a new deletion.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import db

ICLOUD_ROOT = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs'
FOLDER_NAME = '智学笔记同步'
TABLES = {
    'notebooks': 'id name created_at',
    'documents': 'id name kind path size_bytes page_count chunk_count created_at notebook_id',
    'chunks': 'id document_id ordinal page text lines_json',
    'question_images': 'id notebook_id name path ocr_text created_at',
    'notes': 'id notebook_id title content citations_json created_at updated_at',
    'chat_exchanges': 'id notebook_id question result_json status created_at images_json deleted_at reply_to_json',
}
TABLES = {key: value.split() for key, value in TABLES.items()}
BLOB = re.compile(r'^[a-f0-9]{64}\.(?:pdf|md|markdown|txt|docx|pptx|png|jpg|jpeg|webp)$')
DEVICE = re.compile(r'^[a-f0-9]{32}$')
MAX_MANIFEST = 256 * 1024 * 1024
LOCK = threading.Lock()
_file_hashes: dict = {}


class SyncError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _file_hash(path):
    stat = path.stat()
    key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if key not in _file_hashes:
        with path.open('rb') as source:
            _file_hashes[key] = hashlib.file_digest(source, 'sha256').hexdigest()
    return _file_hashes[key]


def _backup(label):
    folder = db.DB_PATH.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{label}-{datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")}.db'
    with closing(sqlite3.connect(db.DB_PATH)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    os.chmod(target, 0o600)
    return target.name


def _ensure_state():
    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_sync_state'").fetchone()
    if not exists:
        _backup('before-cloud-sync')
        with db.connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS cloud_sync_state (id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL)')
            conn.execute('INSERT OR IGNORE INTO cloud_sync_state VALUES (1, ?)', (_json({
                'device': uuid4().hex, 'enabled': False, 'counter': 0, 'records': {}, 'applied': {},
            }),))


def _load(conn):
    row = conn.execute('SELECT state_json FROM cloud_sync_state WHERE id=1').fetchone()
    return json.loads(row[0])


def _save(conn, state):
    conn.execute('UPDATE cloud_sync_state SET state_json=? WHERE id=1', (_json(state),))


def _root():
    if not ICLOUD_ROOT.is_dir():
        raise SyncError('未找到 iCloud Drive，请先在 macOS 系统设置中开启 iCloud Drive 的“同步此 Mac”。')
    return ICLOUD_ROOT / FOLDER_NAME


def _dominates(left, right):
    return all(left.get(device, 0) >= count for device, count in right.items()) and left != right


def _merge(*groups):
    unique = {_hash(version): version for group in groups for version in group}
    versions = list(unique.values())
    # Same clocks with differing payloads indicate corrupt state, not a legitimate edit.
    clocks = {}
    for version in versions:
        identity = _json(version['clock'])
        if identity in clocks and clocks[identity] != version['row']:
            raise SyncError('同步版本时钟冲突，已保留本机数据，请检查同步文件。')
        clocks[identity] = version['row']
    return sorted([version for version in versions if not any(
        _dominates(other['clock'], version['clock']) for other in versions
    )], key=_hash)


def _winner(versions):
    # A concurrent edit beats a deletion until the user explicitly decides.
    return max(versions, key=lambda version: (version['row'] is not None, _hash(version)))['row']


def _next(state, versions, row):
    clock = {}
    for version in versions:
        for device, count in version['clock'].items():
            clock[device] = max(clock.get(device, 0), count)
    state['counter'] = max(state['counter'], clock.get(state['device'], 0)) + 1
    clock[state['device']] = state['counter']
    return [{'clock': clock, 'row': row}]


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temp.open('wb') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _copy_blob(source, target, digest):
    if target.exists() and _file_hash(target) == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name('.' + target.name + '.' + uuid4().hex)
    try:
        shutil.copyfile(source, temp)
        if _file_hash(temp) != digest:
            raise SyncError('附件校验失败，请等待 iCloud 下载完成后重试。')
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _local_rows(conn, root):
    rows = {}
    for table, columns in TABLES.items():
        for record in conn.execute(f'SELECT {",".join(columns)} FROM {table}'):
            row = dict(record)
            if table in ('documents', 'question_images'):
                path = Path(row['path'])
                if not path.is_file():
                    raise SyncError(f'本机附件缺失：{row["name"]}。已暂停同步，避免传播不完整数据。')
                blob = _file_hash(path) + path.suffix.lower()
                if not BLOB.fullmatch(blob):
                    raise SyncError('附件格式不支持同步。')
                _copy_blob(path, root / 'blobs' / blob, blob[:64])
                row['path'] = blob
            # Fresh installations share the same logical default notebook.
            if table == 'notebooks' and row['id'] == 'default':
                row['created_at'] = '1970-01-01T00:00:00+00:00'
            rows[f'{table}/{row["id"]}'] = row
    return rows


def _validate(package):
    if not isinstance(package, dict) or package.get('format') != 1 or not DEVICE.fullmatch(str(package.get('device', ''))):
        raise SyncError('无法读取同步文件版本；请确认两台设备都使用新版智学笔记。')
    records = package.get('records')
    if not isinstance(records, dict):
        raise SyncError('同步文件缺少修改记录。')
    for key, versions in records.items():
        table, _, identity = key.partition('/')
        if table not in TABLES or not identity or not isinstance(versions, list) or not versions:
            raise SyncError('同步文件包含无效记录。')
        for version in versions:
            clock, row = version.get('clock'), version.get('row')
            if not isinstance(clock, dict) or not clock or any(
                not DEVICE.fullmatch(device) or type(count) is not int or count < 1 for device, count in clock.items()
            ):
                raise SyncError('同步文件包含无效版本。')
            if row is not None:
                if not isinstance(row, dict) or set(row) != set(TABLES[table]) or row['id'] != identity:
                    raise SyncError('同步字段不兼容，请先更新两台设备的应用。')
                if table in ('documents', 'question_images') and not BLOB.fullmatch(str(row['path'])):
                    raise SyncError('同步附件路径无效。')
                for column in TABLES[table]:
                    if column.endswith('_json'):
                        json.loads(row[column])
                if table == 'chat_exchanges' and row['status'] not in ('complete', 'error'):
                    raise SyncError('同步文件包含未完成的回答。')
    return records


def _read_remote(root):
    records = {}
    history = {}
    devices = set()
    # All immutable manifests are merged. Older versions are eliminated by clocks;
    # delayed iCloud delivery cannot roll back a newer edit or resurrect a deletion.
    for path in sorted((root / 'manifests').glob('*.json.gz')):
        try:
            with gzip.open(path, 'rb') as source:
                data = source.read(MAX_MANIFEST + 1)
            if len(data) > MAX_MANIFEST:
                raise SyncError('同步文件过大，已暂停同步。')
            package = json.loads(data)
            imported = _validate(package)
            for versions in imported.values():
                for version in versions:
                    row = version['row']
                    if row and 'path' in row:
                        blob = root / 'blobs' / row['path']
                        if not blob.is_file() or _file_hash(blob) != row['path'][:64]:
                            raise SyncError('等待 iCloud 下载完整附件；本机数据保持原样，稍后自动重试。')
            for key, versions in imported.items():
                history.setdefault(key, []).extend(v for v in versions if v['row'] is not None)
                records[key] = _merge(records.get(key, []), versions)
            devices.add(package['device'])
        except SyncError:
            raise
        except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise SyncError('同步文件尚未完整下载或已损坏；本机数据保持原样，稍后自动重试。') from error
    return records, devices, history


def _materialized(records):
    return {key: _winner(versions) for key, versions in records.items() if _winner(versions) is not None}


def _protect_parents(records, history):
    """An edit concurrent with parent deletion retains the parent as a conflict."""
    for _ in range(2):  # chunk -> document -> notebook
        rows = _materialized(records)
        for key, row in list(rows.items()):
            table = key.split('/')[0]
            parent = ('documents/' + row['document_id']) if table == 'chunks' else (
                'notebooks/' + row['notebook_id'] if 'notebook_id' in row else None)
            if not parent or parent in rows:
                continue
            candidates = history.get(parent, [])
            if not candidates:
                raise SyncError('同步内容缺少所属笔记本或资料，已保留本机数据，请检查其他设备的同步状态。')
            old = max(_merge(candidates), key=_hash)
            clock = dict(old['clock'])
            for version in records[key]:
                for device, count in version['clock'].items():
                    clock[device] = max(clock.get(device, 0), count)
            repaired = _merge(records.get(parent, []), [{'clock': clock, 'row': old['row']}])
            if _winner(repaired) is None:
                raise SyncError('同步内容的所属笔记本已删除，请先处理另一台设备的删除冲突。')
            records[parent] = repaired


def _apply(conn, current, desired, root):
    changed = {key for key in current.keys() | desired.keys() if current.get(key) != desired.get(key)}
    # Defer FK validation until every row is updated. No REPLACE (would cascade).
    conn.execute('PRAGMA defer_foreign_keys=ON')
    for table in reversed(TABLES):
        for key in changed:
            if key.startswith(table + '/') and key not in desired:
                conn.execute(f'DELETE FROM {table} WHERE id=?', (key.split('/', 1)[1],))
    for table, columns in TABLES.items():
        items = [(key, desired[key]) for key in changed if key.startswith(table + '/') and key in desired]
        for key, portable in sorted(items, key=lambda item: (item[1].get('created_at', ''), item[0])):
            row = dict(portable)
            if table in ('documents', 'question_images'):
                folder = 'uploads' if table == 'documents' else 'question-images'
                target = db.DB_PATH.parent / folder / ('sync-' + row['path'])
                # Keep an existing attachment path if it already holds these bytes.
                local = conn.execute(f'SELECT path FROM {table} WHERE id=?', (row['id'],)).fetchone()
                if local and Path(local[0]).is_file() and _file_hash(Path(local[0])) == row['path'][:64]:
                    target = Path(local[0])
                else:
                    _copy_blob(root / 'blobs' / row['path'], target, row['path'][:64])
                row['path'] = str(target)
            updates = ','.join(f'{column}=excluded.{column}' for column in columns if column != 'id')
            conn.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)}) '
                         f'ON CONFLICT(id) DO UPDATE SET {updates}', tuple(row[column] for column in columns))
    return len(changed)


def _trim_deleted(state, records):
    rows = _materialized(records)
    notebooks = {row['notebook_id'] for key, row in rows.items() if key.startswith('chat_exchanges/')}
    expired_images = set()
    for notebook in notebooks:
        deleted = [(key, row) for key, row in rows.items() if key.startswith('chat_exchanges/')
                   and row['notebook_id'] == notebook and row['deleted_at']]
        for key, row in sorted(deleted, key=lambda item: (item[1]['deleted_at'], item[0]), reverse=True)[5:]:
            expired_images.update(image['id'] for image in json.loads(row['images_json']))
            records[key] = _next(state, records[key], None)
    retained = {image['id'] for key, row in _materialized(records).items() if key.startswith('chat_exchanges/')
                for image in json.loads(row['images_json'])}
    for identity in expired_images - retained:
        key = 'question_images/' + identity
        if key in records:
            records[key] = _next(state, records[key], None)


def status():
    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_sync_state'").fetchone()
        state = _load(conn) if exists else {}
    conflicts = []
    for key, versions in state.get('records', {}).items():
        if len({_hash(version['row']) for version in versions}) <= 1:
            continue
        table = key.split('/')[0]
        choices = []
        for version in versions:
            row = version['row']
            preview = '删除此内容' if row is None else str(row.get('content', row.get('name', row.get('question', row.get('text', '')))))
            if row and table == 'chat_exchanges':
                result = json.loads(row['result_json'])
                preview = ('已删除（可撤销）\n' if row['deleted_at'] else '') + row['question'] + '\n' + str(result.get('answer', result.get('detail', '')))
            if row and table == 'documents':
                preview += '\n所属笔记本：' + row['notebook_id']
            choices.append({'version': _hash(version), 'deleted': row is None,
                            'title': row.get('title', row.get('name', '')) if row else '删除版本',
                            'preview': preview[:2000]})
        conflicts.append({'key': key, 'kind': table, 'choices': choices,
                          'can_keep_both': table == 'notes' and sum(v['row'] is not None for v in versions) > 1})
    return {'enabled': state.get('enabled', False), 'available': ICLOUD_ROOT.is_dir(),
            'folder': str(ICLOUD_ROOT / FOLDER_NAME), 'last_sync': state.get('last_sync'),
            'last_error': state.get('last_error', ''), 'devices': state.get('devices', 0),
            'changes': state.get('changes', 0), 'conflicts': conflicts,
            'revision': state.get('revision', ''), 'running': LOCK.locked()}


def configure(enabled):
    with LOCK:
        if enabled:
            _root()
        _ensure_state()
        with db.connection() as conn:
            state = _load(conn)
            state['enabled'] = enabled
            state['last_error'] = ''
            _save(conn, state)
    return status()


def sync():
    if not LOCK.acquire(blocking=False):
        raise SyncError('正在同步，请稍后查看结果。')
    try:
        _ensure_state()
        with db.connection() as conn:
            state = _load(conn)
            if not state['enabled']:
                raise SyncError('请先开启 iCloud 同步。')
        root = _root()
        remote, devices, history = _read_remote(root)
        # Online SQLite backup includes WAL and is never sent to iCloud.
        incoming = any(_merge(state['records'].get(key, []), versions) != state['records'].get(key, [])
                       for key, versions in remote.items())
        backup = _backup('before-sync-merge') if incoming else None
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute("SELECT 1 FROM chat_exchanges WHERE status='pending' LIMIT 1").fetchone():
                raise SyncError('有回答正在生成，本次同步暂缓，完成后自动重试。')
            state = _load(conn)
            current = _local_rows(conn, root)
            records = state['records']
            for key, versions in records.items():
                history.setdefault(key, []).extend(v for v in versions if v['row'] is not None)
            for key in current.keys() | state['applied'].keys():
                row = current.get(key)
                # A fresh installation's untouched placeholder is not a user edit.
                if (key == 'notebooks/default' and key not in state['applied'] and row
                        and row['name'] == '我的课程' and key in remote):
                    continue
                if _hash(row) != state['applied'].get(key, _hash(None)):
                    records[key] = _next(state, records.get(key, []), row)
            for key, versions in remote.items():
                records[key] = _merge(records.get(key, []), versions)
            _protect_parents(records, history)
            _trim_deleted(state, records)
            desired = _materialized(records)
            changed = _apply(conn, current, desired, root)
            state['applied'] = {key: _hash(_winner(versions)) for key, versions in records.items()}
            state['last_sync'] = datetime.now(UTC).isoformat()
            state['last_error'] = ''
            state['devices'] = len(devices | {state['device']})
            state['changes'] = changed
            if backup:
                state['backup'] = backup
            package = {'format': 1, 'device': state['device'], 'records': records}
            digest = _hash(package)
            path = root / 'manifests' / f'{state["device"]}-{digest}.json.gz'
            state['revision'] = _hash(desired)
            if conn.execute('PRAGMA foreign_key_check').fetchone():
                raise SyncError('同步内容的引用关系不完整，已保留本机数据。')
            _save(conn, state)
        # Publish only after the local transaction commits. On publication failure,
        # the next run republishes the same durable clocks, without inventing edits.
        if not path.exists():
            _atomic(path, gzip.compress(_json(package).encode()))
        result = status()
        result['running'] = False
        return result
    except (SyncError, OSError, sqlite3.Error) as error:
        with db.connection() as conn:
            state = _load(conn)
            state['last_error'] = str(error) if isinstance(error, SyncError) else '无法访问同步目录或写入数据，请检查磁盘空间和 iCloud 状态。'
            _save(conn, state)
        raise SyncError(state['last_error']) from error
    finally:
        LOCK.release()


def resolve(key, version, keep_both=False):
    with LOCK:
        _ensure_state()
        _backup('before-sync-conflict')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            state = _load(conn)
            if not state['enabled']:
                raise SyncError('请先开启 iCloud 同步。')
            if conn.execute("SELECT 1 FROM chat_exchanges WHERE status='pending' LIMIT 1").fetchone():
                raise SyncError('有回答正在生成，请完成后再处理同步冲突。')
            versions = state['records'].get(key, [])
            chosen = next((item for item in versions if _hash(item) == version), None)
            if not chosen or len(versions) < 2:
                raise SyncError('冲突版本已变化，请重新打开同步面板。')
            # Fold any edits made since the last sync before deciding a conflict.
            root = _root()
            current = _local_rows(conn, root)
            if _hash(current.get(key)) != state['applied'].get(key):
                raise SyncError('本机内容已修改，请先同步再处理冲突。')
            if keep_both:
                if not key.startswith('notes/'):
                    raise SyncError('只有笔记支持同时保留为多份。')
                for item in versions:
                    if item is chosen or item['row'] is None:
                        continue
                    row = dict(item['row'])
                    row['id'] = uuid4().hex
                    row['title'] += '（冲突副本）'
                    columns = TABLES['notes']
                    conn.execute(f'INSERT INTO notes ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                                 tuple(row[column] for column in columns))
            state['records'][key] = _next(state, versions, chosen['row'])
            desired = dict(current)
            if chosen['row'] is None:
                table, identity = key.split('/', 1)
                if table == 'notebooks' and any(row.get('notebook_id') == identity for row in desired.values()):
                    raise SyncError('此笔记本仍有资料、笔记或问答。请先移出或删除其中的内容，再选择删除版本。')
                if table == 'documents':
                    for child, row in list(desired.items()):
                        if child.startswith('chunks/') and row['document_id'] == identity:
                            state['records'][child] = _next(state, state['records'][child], None)
                            state['applied'][child] = _hash(None)
                            desired.pop(child)
                desired.pop(key, None)
            else:
                desired[key] = chosen['row']
            _apply(conn, current, desired, root)
            state['applied'][key] = _hash(chosen['row'])
            _save(conn, state)
    return sync()


async def auto_sync(on_change):
    while True:
        await asyncio.sleep(60)
        try:
            if (await asyncio.to_thread(status))['enabled']:
                result = await asyncio.to_thread(sync)
                if result['changes']:
                    on_change()
        except (SyncError, OSError, sqlite3.Error):
            # Error is retained in local state and shown in the sync panel.
            pass
