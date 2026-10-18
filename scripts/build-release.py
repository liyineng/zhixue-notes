#!/usr/bin/env python3
"""Build a data-free runtime ZIP exclusively from committed Git objects."""
import argparse
import hashlib
import io
import re
import subprocess
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ['app', '.gitignore', 'README.md', 'requirements.txt', 'run.sh']


def build(version, ref, output):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    revision = subprocess.check_output(['git', 'rev-parse', ref + '^{commit}'], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(['git', 'show', revision + ':app/main.py'], cwd=ROOT, text=True)
    if f'version="{version}"' not in source:
        raise ValueError('Release version must match app/main.py in the selected commit')
    archive = subprocess.check_output(['git', 'archive', '--format=tar', revision, *INCLUDE], cwd=ROOT)
    timestamp = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', revision], cwd=ROOT, text=True))
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'zhixue-notes-v{version}.zip'
    prefix = f'zhixue-notes-v{version}/'
    date = datetime.fromtimestamp(timestamp, timezone.utc).timetuple()[:6]
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for member in tree:
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or path.parts[0] not in INCLUDE or '__pycache__' in path.parts
                    or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pyc', '.pdf', '.pptx', '.docx'}
                    or path.name.startswith('.env')):
                raise ValueError('Unsafe release entry: ' + member.name)
            info = zipfile.ZipInfo(prefix + member.name, date_time=date)
            info.create_system = 3
            info.external_attr = (0o100000 | member.mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, tree.extractfile(member).read())
            count += 1
    with target.open('rb') as source_file:
        digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {target.name}\n', encoding='utf-8')
    print(f'{target}\nCommit: {revision}\nFiles: {count}\nSHA256: {digest}')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/releases')
    args = parser.parse_args()
    build(args.version, args.ref, args.output)
