"""Two independent Macs simulated with isolated databases and one folder transport."""
import gzip
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import cloud_sync as sync, db


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cloud = self.root / 'cloud'
        self.cloud.mkdir()
        self.cloud_patch = patch.object(sync, 'ICLOUD_ROOT', self.cloud)
        self.cloud_patch.start()
        for name in ('a', 'b'):
            path = self.root / name
            path.mkdir()
            with self.device(name):
                db.init_db()
                sync.configure(True)
        with self.device('a'):
            db.save_note('note', 'default', '复习', '原始版本', [])
            sync.sync()
        with self.device('b'):
            sync.sync()
        self.converge()

    def tearDown(self):
        self.cloud_patch.stop()
        self.temp.cleanup()

    @contextmanager
    def device(self, name):
        with patch.object(db, 'DB_PATH', self.root / name / 'app.db'):
            yield

    def converge(self):
        for name in ('a', 'b', 'a', 'b'):
            with self.device(name):
                sync.sync()

    def test_quiz_attempts_sync_without_a_new_database_schema(self):
        from app.quizzes import save_attempt
        with self.device('a'):
            db.start_exchange('quiz-test', 'default', '交互测验')
            db.finish_exchange('quiz-test', {'answer': '测验', 'citations': [], 'quiz': {
                'questions': [{'id': 'q1', 'answer': 0}], 'answers': {}, 'attempt': 1}})
            sync.sync()
        with self.device('b'):
            sync.sync()
            save_attempt('default', 'quiz-test', 'q1', 2)
            sync.sync()
        self.converge()
        with self.device('a'):
            self.assertEqual(db.get_exchange('default', 'quiz-test')['result']['quiz']['answers'], {'q1': 2})
            save_attempt('default', 'quiz-test', reset=True)
            sync.sync()
        self.converge()
        with self.device('b'):
            quiz = db.get_exchange('default', 'quiz-test')['result']['quiz']
            self.assertEqual(quiz['answers'], {})
            self.assertEqual(quiz['attempt'], 2)

    def test_independent_offline_changes_merge_and_never_export_settings(self):
        for name in ('a', 'b'):
            with self.device(name):
                db.update_settings({'api_key': 'secret-' + name, 'vision_api_key': 'vision-' + name})
                db.create_notebook(name, '课程' + name)
                db.save_note(name, name, '新笔记', name, [])
                db.start_exchange('chat-' + name, name, '问题' + name)
                db.finish_exchange('chat-' + name, {'answer': '回答' + name, 'citations': []})
                sync.sync()
        self.converge()
        for name in ('a', 'b'):
            with self.device(name):
                self.assertEqual(db.get_settings()['api_key'], 'secret-' + name)
                self.assertEqual(len(db.list_notebooks()), 3)
                self.assertEqual(db.get_note('b')['content'], 'b')
                self.assertEqual(db.list_exchanges('a')[0]['result']['answer'], '回答a')
        for path in (self.cloud / sync.FOLDER_NAME / 'manifests').glob('*'):
            text = gzip.decompress(path.read_bytes()).decode()
            self.assertNotIn('secret-', text)
            self.assertNotIn('vision-', text)
            self.assertNotIn(str(self.root / 'a'), text)
            self.assertNotIn('cloud_sync_state', text)

    def test_new_mac_adopts_existing_default_notebook_without_placeholder_conflict(self):
        with self.device('a'):
            db.rename_notebook('default', '计算机网络')
            sync.sync()
        (self.root / 'new-mac').mkdir()
        with self.device('new-mac'):
            db.init_db()
            sync.configure(True)
            sync.sync()
            self.assertEqual(db.get_notebook('default')['name'], '计算机网络')
            self.assertEqual(sync.status()['conflicts'], [])

    def test_concurrent_note_edits_keep_both_and_resolution_converges(self):
        for name in ('a', 'b'):
            with self.device(name):
                db.save_note('note', 'default', '复习', '离线修改-' + name, [])
        self.converge()
        for name in ('a', 'b'):
            with self.device(name):
                conflict = sync.status()['conflicts'][0]
                self.assertEqual({c['preview'] for c in conflict['choices']}, {'离线修改-a', '离线修改-b'})
        with self.device('a'):
            sync.resolve(conflict['key'], conflict['choices'][0]['version'], True)
        self.converge()
        for name in ('a', 'b'):
            with self.device(name):
                self.assertEqual(sync.status()['conflicts'], [])
                self.assertEqual({n['content'] for n in db.list_notes('default')}, {'离线修改-a', '离线修改-b'})

    def test_deletion_propagates_and_stale_manifests_do_not_resurrect(self):
        with self.device('a'):
            db.delete_note('note')
            sync.sync()
        self.converge()
        with self.device('b'):
            self.assertIsNone(db.get_note('note'))
        self.converge()
        with self.device('a'):
            self.assertIsNone(db.get_note('note'))

    def test_concurrent_delete_and_edit_retains_edit_with_explicit_choice(self):
        with self.device('a'):
            db.delete_note('note')
        with self.device('b'):
            db.save_note('note', 'default', '复习', '保留的修改', [])
        self.converge()
        with self.device('a'):
            self.assertEqual(db.get_note('note')['content'], '保留的修改')
            conflict = sync.status()['conflicts'][0]
            deleted = next(c for c in conflict['choices'] if c['deleted'])
            sync.resolve(conflict['key'], deleted['version'])
        self.converge()
        with self.device('b'):
            self.assertIsNone(db.get_note('note'))
            self.assertEqual(sync.status()['conflicts'], [])

    def add_document(self):
        path = self.root / 'a' / 'uploads' / 'local.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('来源原文', encoding='utf8')
        with db.connection() as conn:
            conn.execute("INSERT INTO documents VALUES ('doc','课程.md','md',?,12,1,1,'2026','default')", (str(path),))
            conn.execute("INSERT INTO chunks VALUES ('chunk','doc',0,1,'来源原文','[]')")
        return path

    def test_attachments_rebased_verified_and_available_after_other_mac_offline(self):
        with self.device('a'):
            original = self.add_document()
            image = self.root / 'a' / 'question.png'
            image.write_bytes(b'fake-test-image')
            db.insert_question_image({'id': 'image', 'notebook_id': 'default', 'name': '题目.png', 'path': str(image), 'ocr_text': '题目'})
            sync.sync()
        with self.device('b'):
            sync.sync()
            imported = Path(db.get_document('doc')['path'])
            imported_image = Path(db.get_question_image('image')['path'])
            self.assertTrue(imported.is_relative_to(self.root / 'b'))
            self.assertTrue(imported_image.is_relative_to(self.root / 'b'))
            original.unlink()
            image.unlink()
            self.assertEqual(imported.read_text(), '来源原文')
            self.assertEqual(imported_image.read_bytes(), b'fake-test-image')
            self.assertEqual(db.all_chunks()[0]['text'], '来源原文')

    def test_missing_cloud_blob_and_corrupt_manifest_never_change_database(self):
        with self.device('a'):
            self.add_document()
            sync.sync()
        blob = next((self.cloud / sync.FOLDER_NAME / 'blobs').glob('*'))
        content = blob.read_bytes()
        blob.unlink()
        with self.device('b'):
            with self.assertRaisesRegex(sync.SyncError, '等待 iCloud'):
                sync.sync()
            self.assertIsNone(db.get_document('doc'))
            self.assertEqual(db.get_note('note')['content'], '原始版本')
            blob.write_bytes(content)
            sync.sync()
            self.assertIsNotNone(db.get_document('doc'))
            bad = self.cloud / sync.FOLDER_NAME / 'manifests' / 'broken.json.gz'
            bad.write_bytes(b'broken')
            with self.assertRaises(sync.SyncError):
                sync.sync()
            self.assertIsNotNone(db.get_document('doc'))

    def test_pending_job_defers_sync_and_retains_running_state(self):
        with self.device('a'):
            db.start_exchange('pending', 'default', '正在生成')
            with self.assertRaisesRegex(sync.SyncError, '正在生成'):
                sync.sync()
            self.assertEqual(db.get_exchange('default', 'pending')['status'], 'pending')
            db.finish_exchange('pending', {'answer': '已完成'})
            sync.sync()
        with self.device('b'):
            sync.sync()
            self.assertEqual(db.get_exchange('default', 'pending')['result']['answer'], '已完成')

    def test_notebook_delete_vs_concurrent_child_keeps_parent(self):
        with self.device('a'):
            db.create_notebook('course', '课程')
        self.converge()
        with self.device('a'):
            self.assertTrue(db.delete_empty_notebook('course'))
        with self.device('b'):
            db.save_note('new', 'course', '新笔记', '不可丢失', [])
        self.converge()
        for name in ('a', 'b'):
            with self.device(name):
                self.assertIsNotNone(db.get_notebook('course'))
                self.assertEqual(db.get_note('new')['content'], '不可丢失')
                self.assertEqual(sync.status()['conflicts'][0]['kind'], 'notebooks')

    def test_soft_delete_and_undo_sync_and_backups_exist(self):
        with self.device('a'):
            db.start_exchange('chat', 'default', '问答')
            db.finish_exchange('chat', {'answer': '内容'})
        self.converge()
        with self.device('a'):
            db.delete_exchange('default', 'chat')
        self.converge()
        with self.device('b'):
            self.assertEqual(db.list_exchanges('default'), [])
            self.assertEqual(db.restore_exchange('default'), 'chat')
        self.converge()
        with self.device('a'):
            self.assertEqual(len(db.list_exchanges('default')), 1)
        backups = list((self.root / 'a' / 'backups').glob('*.db'))
        self.assertTrue(backups)
        with sqlite3.connect(backups[0]) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_disable_leaves_local_and_cloud_data_and_reenable_is_idempotent(self):
        with self.device('a'):
            before = len(list((self.cloud / sync.FOLDER_NAME / 'manifests').glob('*')))
            sync.configure(False)
            self.assertFalse(sync.status()['enabled'])
            with self.assertRaisesRegex(sync.SyncError, '开启'):
                sync.sync()
            self.assertIsNotNone(db.get_note('note'))
            sync.configure(True)
            sync.sync()
            sync.sync()
            self.assertEqual(len(list((self.cloud / sync.FOLDER_NAME / 'manifests').glob('*'))), before)

    def test_remote_path_traversal_is_rejected(self):
        with self.device('a'):
            self.add_document()
            sync.sync()
        folder = self.cloud / sync.FOLDER_NAME / 'manifests'
        package = next(json.loads(gzip.decompress(path.read_bytes())) for path in folder.glob('*')
                       if 'documents/doc' in json.loads(gzip.decompress(path.read_bytes()))['records'])
        package['records']['documents/doc'][0]['row']['path'] = '../../app.db'
        (folder / 'evil.json.gz').write_bytes(gzip.compress(json.dumps(package).encode()))
        with self.device('b'):
            with self.assertRaisesRegex(sync.SyncError, '路径无效'):
                sync.sync()
            self.assertIsNone(db.get_document('doc'))

    def test_imported_old_chat_does_not_displace_new_context(self):
        with self.device('a'):
            db.start_exchange('old', 'default', '旧问题')
            db.finish_exchange('old', {'answer': '旧回答'})
            with db.connection() as conn:
                conn.execute("UPDATE chat_exchanges SET created_at='2026-01-01' WHERE id='old'")
        with self.device('b'):
            db.start_exchange('new', 'default', '新问题')
            db.finish_exchange('new', {'answer': '新回答'})
        self.converge()
        with self.device('b'):
            self.assertEqual([item['id'] for item in db.list_exchanges('default')], ['old', 'new'])
            self.assertEqual(db.recent_completed_exchanges('default', 1)[0]['question'], '新问题')

    def test_two_devices_deleting_different_chats_retain_only_five_undo_records(self):
        with self.device('a'):
            for i in range(8):
                db.start_exchange(str(i), 'default', str(i))
                db.finish_exchange(str(i), {'answer': str(i)})
        self.converge()
        for name, ids in [('a', range(4)), ('b', range(4, 8))]:
            with self.device(name):
                for identity in ids:
                    db.delete_exchange('default', str(identity))
        self.converge()
        for name in ('a', 'b'):
            with self.device(name):
                self.assertEqual(db.list_exchanges('default'), [])
                self.assertEqual(db.deleted_exchange_count('default'), 5)

    def test_failure_to_publish_retries_without_losing_local_edits(self):
        with self.device('a'):
            db.save_note('note', 'default', '复习', '新版本', [])
            with patch.object(sync, '_atomic', side_effect=OSError('disk full')):
                with self.assertRaises(sync.SyncError):
                    sync.sync()
            self.assertEqual(db.get_note('note')['content'], '新版本')
            sync.sync()
        with self.device('b'):
            sync.sync()
            self.assertEqual(db.get_note('note')['content'], '新版本')
