"""Supplement image-based PDF content without replacing original files or text chunks."""
from __future__ import annotations

import asyncio
import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pymupdf
import httpx
from starlette.concurrency import run_in_threadpool

from . import db
from .question_images import read_question

_locks: dict[str, asyncio.Lock] = {}


def image_regions(path: Path) -> list[dict[str, Any]]:
    candidates = []
    with pymupdf.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            blocks = page.get_text('dict')['blocks']
            native = [(pymupdf.Rect(line['bbox']), ''.join(s['text'] for s in line['spans']))
                      for b in blocks if b['type'] == 0 for line in b.get('lines', [])]
            if sum(len(text.strip()) for _, text in native) >= 40:
                continue  # This pass supplements image-only pages, not every illustration.
            images = []
            for b in blocks:
                if b['type'] != 1:
                    continue
                rect = pymupdf.Rect(b['bbox'])
                if rect.width < 100 or rect.height < 36 or rect.get_area() < page.rect.get_area() * .015:
                    continue  # Exclude logos and thin decorative headers/footers.
                overlay = sum(len(text.strip()) for box, text in native if rect.contains(box))
                if overlay < 60:  # An existing usable OCR text layer needs no second copy.
                    images.append(list(rect))
            if images:
                candidates.append({'page': number, 'regions': images,
                                   'image_only': sum(len(text.strip()) for _, text in native) < 40})
    return candidates


def render_region(path: Path, page: int, bbox: list[float]) -> tuple[bytes, int, int]:
    with pymupdf.open(path) as pdf:
        rect = pymupdf.Rect(bbox)
        scale = min(2.5, 2400 / max(rect.width, rect.height))
        pix = pdf[page - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
        return pix.tobytes('png'), pix.width, pix.height


def ocr_lines(png: bytes, width: int, height: int, page: int, bbox: list[float]) -> list[dict[str, Any]]:
    executable = shutil.which('tesseract')
    if not executable:
        return []
    try:
        available = subprocess.run([executable, '--list-langs'], capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()
        languages = '+'.join(lang for lang in ('chi_sim', 'eng') if lang in available)
        if not languages:
            return []
        result = subprocess.run([executable, 'stdin', 'stdout', '-l', languages, '--psm', '3', 'tsv'],
                                input=png, capture_output=True, timeout=45, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    groups: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for word in csv.DictReader(io.StringIO(result.stdout.decode('utf-8', errors='replace')), delimiter='\t'):
        if word.get('level') == '5' and word.get('text', '').strip():
            groups.setdefault(tuple(word[k] for k in ('block_num', 'par_num', 'line_num')), []).append(word)
    lines = []
    sx, sy = (bbox[2] - bbox[0]) / width, (bbox[3] - bbox[1]) / height
    for words in groups.values():
        text = ' '.join(word['text'].strip() for word in words)
        text = re.sub(r'(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])', '', text)
        x1 = min(int(w['left']) for w in words); y1 = min(int(w['top']) for w in words)
        x2 = max(int(w['left']) + int(w['width']) for w in words)
        y2 = max(int(w['top']) + int(w['height']) for w in words)
        lines.append({'page': page, 'text': text, 'bbox': [bbox[0] + x1 * sx, bbox[1] + y1 * sy,
                      bbox[0] + x2 * sx, bbox[1] + y2 * sy], 'image_recognition': 'ocr'})
    return lines


async def ensure_image_text(document: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if document['kind'] != 'pdf':
        return {'added_pages': [], 'unread_pages': []}
    async with _locks.setdefault(document['id'], asyncio.Lock()):
        candidates = await run_in_threadpool(image_regions, Path(document['path']))
        existing = db.document_chunk_ids(document['id'])
        added, unread = [], []
        for candidate in candidates:
            number = candidate['page']
            identity = f"image-{document['id']}-{number}"
            if identity in existing:
                continue
            regions = candidate['regions']
            lines = []
            if candidate['image_only'] and settings.get('vision_model'):
                # Render the combined question and diagram so their relationship is retained.
                rect = pymupdf.Rect(regions[0])
                for region in regions[1:]: rect |= pymupdf.Rect(region)
                bbox = list(rect)
                png, _, _ = await run_in_threadpool(render_region, Path(document['path']), number, bbox)
                try:
                    with tempfile.TemporaryDirectory(prefix='zhixue-page-') as temp:
                        image = Path(temp) / 'page.png'; image.write_bytes(png)
                        text, _ = await read_question([{'path': str(image), 'ocr_text': ''}], settings,
                            instruction='逐字识别这张课件页面的内容，保留题目、已知条件、选项和原有解答。公式转为 LaTeX，'
                            '图表描述数值与结构关系。保留二进制串、上下标和单位；看不清处标为[看不清]，'
                            '不得补猜、改写或额外解题。')
                    lines = [{'page': number, 'text': text, 'bbox': bbox, 'image_recognition': 'vision'}]
                except (RuntimeError, ValueError, OSError, httpx.HTTPError):
                    pass  # Fall back to local OCR; an unread page is reported explicitly below.
            if not lines:
                for bbox in regions:
                    png, width, height = await run_in_threadpool(render_region, Path(document['path']), number, bbox)
                    lines.extend(await run_in_threadpool(ocr_lines, png, width, height, number, bbox))
            if not lines:
                unread.append(number)
                continue
            if db.append_image_chunk(document['id'], identity, number, lines):
                added.append(number)
        return {'added_pages': added, 'unread_pages': unread}
