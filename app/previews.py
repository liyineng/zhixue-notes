"""Local PPTX-to-PDF preview cache. Originals and retrieval chunks stay unchanged."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf

from . import db

_CONVERSION = threading.Lock()


class PreviewError(ValueError):
    pass


def office_executable() -> str | None:
    for name in ('soffice', 'libreoffice'):
        found = shutil.which(name)
        if found:
            return found
    candidates = [Path('/Applications/LibreOffice.app/Contents/MacOS/soffice'),
                  Path.home() / 'Applications/LibreOffice.app/Contents/MacOS/soffice']
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / 'LibreOffice/program/soffice.exe')
    return next((str(path) for path in candidates if path.is_file()), None)


def _valid(path: Path, pages: int) -> bool:
    if not path.is_file():
        return False
    try:
        with pymupdf.open(path) as pdf:
            return pdf.is_pdf and pdf.page_count > 0 and (not pages or pdf.page_count == pages)
    except (RuntimeError, ValueError):
        return False


def conversion_environment(work: Path) -> dict:
    env = dict(os.environ)
    if sys.platform != 'darwin' or env.get('FONTCONFIG_FILE'):
        return env
    # Some headless Mac builds ship an outdated fontconfig that omits Apple's
    # AssetsV2 fonts and silently substitutes blank Chinese glyphs.
    for candidate in ('/opt/homebrew/etc/fonts/fonts.conf', '/usr/local/etc/fonts/fonts.conf', '/etc/fonts/fonts.conf'):
        path = Path(candidate)
        if path.is_file():
            env['FONTCONFIG_FILE'] = str(path)
            env['FONTCONFIG_PATH'] = str(path.parent)
            return env
    directories = [Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home() / 'Library/Fonts']
    for base in ('/System/Library/AssetsV2', '/System/Library/Assets'):
        directories.extend(Path(base).glob('com_apple_MobileAsset_Font*'))
    config = work / 'fonts.conf'
    config.write_text('<?xml version="1.0"?><fontconfig>' + ''.join(
        '<dir>' + escape(str(path)) + '</dir>' for path in directories if path.is_dir()
    ) + '<cachedir>' + escape(str(work.parent / 'font-cache')) + '</cachedir></fontconfig>', encoding='utf-8')
    env['FONTCONFIG_FILE'] = str(config)
    env['FONTCONFIG_PATH'] = str(work)
    return env


def presentation_pdf(document: dict) -> Path:
    source = Path(document['path'])
    if not source.is_file():
        raise PreviewError('原始课件已丢失，无法生成预览。')
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    cache = db.DB_PATH.parent / 'previews'
    target = cache / f'{digest}-v2.pdf'
    pages = document.get('page_count', 0)
    with _CONVERSION:
        if _valid(target, pages):
            return target
        office = office_executable()
        if not office:
            raise PreviewError('PPT 原页预览需要安装 LibreOffice，安装后重新打开课件即可。当前仍可查看提取的文字，或用右上角按钮下载原文件。')
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='convert-', dir=cache) as work:
            work = Path(work)
            # Isolated profile avoids colliding with a user's open LibreOffice.
            command = [office, '-env:UserInstallation=' + (work / 'profile').as_uri(),
                       '--headless', '--convert-to', 'pdf:impress_pdf_Export',
                       '--outdir', str(work), str(source.resolve())]
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=120, env=conversion_environment(work),
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired as error:
                raise PreviewError('课件预览转换超时，请稍后重试，或先用右上角按钮下载原文件。') from error
            except (OSError, subprocess.CalledProcessError) as error:
                raise PreviewError('课件预览转换失败，请确认文件能在 LibreOffice 中正常打开。提取文字与原文件仍可查看。') from error
            converted = work / (source.stem + '.pdf')
            if not _valid(converted, pages):
                raise PreviewError('转换后的课件页数不完整，未使用此预览。请下载原文件查看，或另存为 PDF 后上传。')
            os.replace(converted, target)
    return target
