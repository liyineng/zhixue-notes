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
COMMON = ['app', '.gitignore', 'requirements.txt', 'launch.py']
PLATFORMS = {'macos': ['run.sh', 'start.command'], 'windows': ['start.bat']}


def build(version, ref, output, platform):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    revision = subprocess.check_output(['git', 'rev-parse', ref + '^{commit}'], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(['git', 'show', revision + ':app/main.py'], cwd=ROOT, text=True)
    if f'version="{version}"' not in source:
        raise ValueError('Release version must match app/main.py in the selected commit')
    include = COMMON + PLATFORMS[platform]
    archive = subprocess.check_output(['git', 'archive', '--format=tar', revision, *include], cwd=ROOT)
    guide = subprocess.check_output(['git', 'show', revision + ':distribution/README-' + platform + '.md'], cwd=ROOT)
    timestamp = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', revision], cwd=ROOT, text=True))
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'zhixue-notes-v{version}-{platform}.zip'
    prefix = f'zhixue-notes-v{version}-{platform}/'
    date = datetime.fromtimestamp(timestamp, timezone.utc).timetuple()[:6]
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for member in tree:
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or path.parts[0] not in include or '__pycache__' in path.parts
                    or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pyc', '.pdf', '.pptx', '.docx'}
                    or path.name.startswith('.env')):
                raise ValueError('Unsafe release entry: ' + member.name)
            info = zipfile.ZipInfo(prefix + member.name, date_time=date)
            info.create_system = 3
            info.external_attr = (0o100000 | member.mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            payload = tree.extractfile(member).read()
            if path.suffix == '.bat':
                payload = payload.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            package.writestr(info, payload)
            count += 1
        info = zipfile.ZipInfo(prefix + 'README.md', date_time=date)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        package.writestr(info, guide)
        count += 1
    with target.open('rb') as source_file:
        digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
    checksum = output / 'SHA256SUMS.txt'
    previous = checksum.read_text(encoding='utf-8').splitlines() if checksum.exists() else []
    lines = [line for line in previous if line.strip() and line.split()[-1].startswith(f'zhixue-notes-v{version}-') and line.split()[-1] != target.name]
    lines.append(f'{digest}  {target.name}')
    checksum.write_text('\n'.join(sorted(lines)) + '\n', encoding='utf-8')
    print(f'{target}\nCommit: {revision}\nFiles: {count}\nSHA256: {digest}')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--platform', choices=['macos', 'windows', 'all'], default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/releases')
    args = parser.parse_args()
    for platform in PLATFORMS if args.platform == 'all' else [args.platform]:
        build(args.version, args.ref, args.output, platform)
