from __future__ import annotations

import mimetypes
import hashlib
import asyncio
from contextlib import suppress
import re
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import db, generation, cloud_sync
from .attachment_followups import recover_attachment_problem
from .quizzes import build_quiz, save_attempt, COUNTS
from .previews import presentation_pdf, presentation_page_geometry, PreviewError
from .conversation import recent_context, referenced_sources
from .model_context import context_info, resolve_context, history_budget, source_reserve, estimate_tokens
from .progress import source_activity
from .summaries import summary_batches
from .config import (
    MAX_UPLOAD_BYTES,
    STATIC_DIR,
    SUPPORTED_SUFFIXES,
    UPLOAD_DIR,
    QUESTION_IMAGE_DIR,
    ensure_directories,
)
from .llm import analyze_question, answer_question, make_summary, model_configured, select_evidence, cited_sources
from .parsers import UnsupportedDocument, parse_document
from .retrieval import BM25Index, excerpt, focus_source, page_sources, summary_documents, named_question_documents, exercise_sources, is_exercise_question, is_formula_inventory
from .question_images import MAX_IMAGE_BYTES, normalize_image, local_ocr, read_question
from .document_images import ensure_image_text, ensure_presentation_formulas
from urllib.parse import quote

mimetypes.add_type("text/javascript", ".mjs")

_index_version = 0


def _generation_failure(exchange_id: str, notebook_id: str, exc: Exception) -> str:
    """Retain the failing stage and never turn an empty exception into an empty error."""
    exchange = db.get_exchange(notebook_id, exchange_id)
    progress = (exchange or {}).get('result', {}).get('progress', {})
    if isinstance(exc, HTTPException):
        detail = str(exc.detail)
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        detail = '请求模型接口超时，请稍后重试。已完成的图片识别结果会保留。'
    else:
        detail = str(exc).strip() or f'处理请求时发生异常（{type(exc).__name__}），请重试。'
    steps = progress.get('steps', [])
    if steps:
        detail = f"{steps[-1]['label']}时失败：{detail}"
    db.finish_pending_exchange(exchange_id, {'detail': detail, 'error_type': type(exc).__name__,
                                            'failed_progress': progress}, error=True)
    return detail


def _invalidate_index() -> None:
    global _index_version
    _index_version += 1
    _build_index.cache_clear()


@lru_cache(maxsize=4)
def _build_index(version: int) -> BM25Index:
    del version
    return BM25Index(db.all_chunks())


def _index() -> BM25Index:
    return _build_index(_index_version)


async def _ensure_source_images(documents: list[dict[str, Any]], settings: dict[str, Any], exchange_id: str | None = None,
                                *, formulas: bool = False) -> list[dict[str, Any]]:
    unread = []
    for index, document in enumerate(documents, 1):
        if exchange_id:
            db.set_generation_progress(exchange_id, 'sources', '检查资料',
                                       f"正在检查《{document['name']}》（{index}/{len(documents)}），读取文字及图片页识别结果。",
                                       activity=source_activity([document]))
        def on_page(page: int) -> None:
            detail = '公式及符号条件' if formulas and document['kind'] == 'pptx' else '图片文字'
            db.set_generation_progress(exchange_id, 'sources', '识别课件公式' if detail.startswith('公式') else '识别课件图片页',
                                       f"正在识别《{document['name']}》第 {page} 页的{detail}。",
                                       activity=source_activity([{'document_id': document['id'], 'document_name': document['name'], 'page': page}]))
        report = await ensure_image_text(document, settings, on_page=on_page) if exchange_id and document['kind'] == 'pdf' else await ensure_image_text(document, settings)
        if formulas and document['kind'] == 'pptx':
            report = await ensure_presentation_formulas(document, settings, on_page=on_page if exchange_id else None)
        if report['added_pages']:
            _invalidate_index()
        if report['unread_pages']:
            unread.append({'document_name': document['name'], 'pages': report['unread_pages']})
    return unread


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories()
    db.init_db()
    sync_task = asyncio.create_task(cloud_sync.auto_sync(_invalidate_index))
    try:
        yield
    finally:
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task


app = FastAPI(
    title="智学笔记 API",
    version="0.3.2",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    reply_to_id: str | None = Field(default=None, max_length=100)
    question: str = Field(default="", max_length=4000)
    image_ids: list[str] = Field(default_factory=list, max_length=10)
    image_text: str | None = Field(default=None, max_length=20000)
    document_ids: list[str] | None = None
    notebook_id: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=12)


class SummaryRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    document_ids: list[str] | None = None
    notebook_id: str | None = None
    focus: str = Field(default="", max_length=300)


class QuizRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    notebook_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    count: Literal['less', 'standard', 'more'] = 'standard'
    difficulty: Literal['easy', 'medium', 'hard'] = 'medium'
    language: Literal['zh-CN', 'zh-TW', 'en'] = 'zh-CN'
    topic: str = Field(default='', max_length=1000)


class QuizAnswerRequest(BaseModel):
    question_id: str = Field(max_length=100)
    option: int = Field(ge=0, le=3, strict=True)


class SettingsRequest(BaseModel):
    base_url: str | None = Field(default=None, max_length=1000)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=128, le=8192)
    context_window: int | None = Field(default=None, ge=0, le=10000000)
    top_k: int | None = Field(default=None, ge=1, le=12)
    vision_model: str | None = Field(default=None, max_length=300)
    vision_base_url: str | None = Field(default=None, max_length=1000)
    vision_api_key: str | None = Field(default=None, max_length=2000)


class NotebookRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MoveDocumentRequest(BaseModel):
    notebook_id: str


class NoteCitation(BaseModel):
    index: int = Field(ge=1, le=100)
    document_id: str = Field(max_length=100)
    document_name: str = Field(max_length=500)
    document_kind: str = Field(default="", max_length=20)
    page: int | None = None
    chunk_id: str = Field(default="", max_length=100)
    text: str = Field(default="", max_length=30000)


class NoteRequest(BaseModel):
    notebook_id: str
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)
    citations: list[NoteCitation] = Field(default_factory=list, max_length=200)


class NoteEditRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)


def _notebook(notebook_id: str) -> dict[str, Any]:
    notebook = db.get_notebook(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="笔记本不存在。")
    return notebook


def _scope(notebook_id: str | None, document_ids: list[str] | None) -> list[dict[str, Any]]:
    if notebook_id is not None:
        _notebook(notebook_id)
    documents = db.list_documents(notebook_id)
    if document_ids is None:
        return documents
    wanted = set(document_ids)
    if not wanted.issubset({doc["id"] for doc in documents}):
        raise HTTPException(status_code=400, detail="选择的来源不在当前笔记本中或已被删除。")
    return [doc for doc in documents if doc["id"] in wanted]


def _name(value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="名称或内容不能为空。")
    return value


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    result = {**settings, "api_key": "", "vision_api_key": ""}
    result["api_key_set"] = bool(str(settings.get("api_key", "")).strip())
    result["model_configured"] = model_configured(settings)
    result["vision_api_key_set"] = bool(str(settings.get("vision_api_key", "")).strip())
    result['model_context'] = context_info(settings)
    return result


def _contexts_for_results(results: list[Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        chunk = result.chunk
        contexts.append(
            {
                "index": index,
                "chunk_id": chunk["id"],
                "document_id": chunk["document_id"],
                "document_name": chunk["document_name"],
                "document_kind": chunk["document_kind"],
                "page": chunk.get("page"),
                "text": chunk["text"],
                "lines": chunk.get("lines") or [],
                "score": round(float(result.score), 4),
                "image_recognition": sorted({line['image_recognition'] for line in chunk.get('lines', []) if line.get('image_recognition')}),
            }
        )
    return contexts


def _summary_contexts(
    document_ids: list[str] | None,
) -> list[dict[str, Any]]:
    wanted = set(document_ids) if document_ids is not None else None
    chunks = page_sources([
        chunk
        for chunk in db.all_chunks()
        if wanted is None or chunk["document_id"] in wanted
    ])
    if not chunks:
        return []
    chunks.sort(key=lambda item: (item["document_name"], item.get("page") or 0, item["ordinal"]))
    contexts: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        contexts.append(
            {
                "index": index,
                "chunk_id": chunk["id"],
                "document_id": chunk["document_id"],
                "document_name": chunk["document_name"],
                "document_kind": chunk["document_kind"],
                "page": chunk.get("page"),
                "text": chunk["text"],
                "lines": chunk.get("lines") or [],
                "score": None,
                "image_recognition": sorted({line['image_recognition'] for line in chunk.get('lines', []) if line.get('image_recognition')}),
            }
        )
    return contexts


class SyncConfigRequest(BaseModel):
    enabled: bool


class SyncResolveRequest(BaseModel):
    key: str = Field(max_length=300)
    version: str = Field(pattern=r'^[a-f0-9]{64}$')
    keep_both: bool = False


@app.get('/api/sync')
def sync_status() -> dict[str, Any]:
    return cloud_sync.status()


@app.put('/api/sync')
def configure_sync(payload: SyncConfigRequest) -> dict[str, Any]:
    try:
        return cloud_sync.configure(payload.enabled)
    except cloud_sync.SyncError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/sync/run')
def run_sync() -> dict[str, Any]:
    try:
        result = cloud_sync.sync()
        _invalidate_index()
        return result
    except cloud_sync.SyncError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post('/api/sync/resolve')
def resolve_sync(payload: SyncResolveRequest) -> dict[str, Any]:
    try:
        result = cloud_sync.resolve(payload.key, payload.version, payload.keep_both)
        _invalidate_index()
        return result
    except cloud_sync.SyncError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = db.get_settings()
    return {
        "ok": True,
        "documents": db.document_count(),
        "model_configured": model_configured(settings),
    }


@app.get("/api/notebooks")
def notebooks() -> dict[str, Any]:
    return {"notebooks": db.list_notebooks()}


@app.post("/api/notebooks", status_code=201)
def create_notebook(payload: NotebookRequest) -> dict[str, Any]:
    return {"notebook": db.create_notebook(uuid4().hex, _name(payload.name))}


@app.patch("/api/notebooks/{notebook_id}")
def rename_notebook(notebook_id: str, payload: NotebookRequest) -> dict[str, Any]:
    _notebook(notebook_id)
    db.rename_notebook(notebook_id, _name(payload.name))
    return {"notebook": _notebook(notebook_id)}


@app.get("/api/documents")
def documents(notebook_id: str | None = None) -> dict[str, Any]:
    return {"documents": _scope(notebook_id, None)}


@app.delete("/api/notebooks/{notebook_id}")
def delete_notebook(notebook_id: str) -> dict[str, bool]:
    _notebook(notebook_id)
    if notebook_id == "default":
        raise HTTPException(status_code=409, detail="默认笔记本可重命名，但不能删除。")
    images = db.notebook_images(notebook_id)
    if not db.delete_empty_notebook(notebook_id):
        raise HTTPException(status_code=409, detail="请先移出资料、删除笔记并清空对话，再删除空笔记本。")
    for image in images:
        Path(image["path"]).unlink(missing_ok=True)
    return {"deleted": True}


def _public_image(image: dict[str, Any]) -> dict[str, Any]:
    return {"id": image["id"], "name": image["name"], "ocr_text": image["ocr_text"],
            "kind": 'image' if Path(image['path']).suffix.lower() == '.png' else 'document',
            "url": f"/api/question-images/{image['id']}/file"}


@app.post("/api/question-images", status_code=201)
async def upload_question_image(file: UploadFile = File(...), notebook_id: str = "default") -> dict[str, Any]:
    _notebook(notebook_id)
    image_id = uuid4().hex
    path = QUESTION_IMAGE_DIR / f"{image_id}.png"
    try:
        content = await file.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="每张题目图片最多 10 MB。")
        await run_in_threadpool(normalize_image, content, path)
        text = await run_in_threadpool(local_ocr, path)
        image = {"id": image_id, "notebook_id": notebook_id, "name": Path(file.filename or "题目图片").name[:200],
                 "path": str(path), "ocr_text": text}
        db.insert_question_image(image)
        return {"image": _public_image(image)}
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.post('/api/question-attachments', status_code=201)
async def upload_question_document(file: UploadFile = File(...), notebook_id: str = 'default') -> dict[str, Any]:
    _notebook(notebook_id)
    name = Path(file.filename or '').name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail='附件支持 PDF、PPTX、DOCX、Markdown 和 TXT。')
    identity = uuid4().hex
    target = QUESTION_IMAGE_DIR / f'{identity}{suffix}'
    try:
        size = 0
        with target.open('wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail='附件超过 120 MB 限制。')
                output.write(chunk)
        if not size:
            raise ValueError('附件为空。')
        chunks, _ = await run_in_threadpool(parse_document, target, name)
        parts = []
        if suffix == '.pdf':
            import tempfile
            import pymupdf
            with pymupdf.open(target) as pdf:
                if len(pdf) > 30:
                    raise ValueError('问题附件最多 30 页，请拆分题目文件；完整课件可从侧栏添加来源。')
                for number, page in enumerate(pdf, 1):
                    text = page.get_text().strip()
                    if not text or page.get_images(full=True):
                        with tempfile.TemporaryDirectory(dir=QUESTION_IMAGE_DIR) as work:
                            image_path = Path(work) / 'page.png'
                            scale = min(2, 2000 / max(page.rect.width, page.rect.height))
                            await run_in_threadpool(lambda: page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(image_path))
                            ocr = await run_in_threadpool(local_ocr, image_path)
                            try:
                                text, _ = await read_question([{'path': str(image_path), 'ocr_text': ocr}], db.get_settings())
                            except ValueError as exc:
                                raise ValueError(f'附件第 {number} 页未识别成功：{exc}') from exc
                    parts.append(f'第 {number} 页：\n{text}')
                    if sum(map(len, parts)) > 19500:
                        raise ValueError('附件文字超过 19500 字，请拆分文件后提问。')
        else:
            parts = [chunk['text'] for chunk in chunks]
        text = '\n\n'.join(parts).strip()
        if not text:
            raise ValueError('附件未提取到文字，请检查文件或上传题目截图。')
        if len(text) > 19500:
            raise ValueError('附件文字超过 19500 字，请拆分文件后提问。')
        # Reuse the existing notebook-scoped attachment store and history/undo
        # lifecycle. No document/chunk rows are created in the source library.
        attachment = {'id': identity, 'notebook_id': notebook_id, 'name': name[:200], 'path': str(target), 'ocr_text': text}
        db.insert_question_image(attachment)
        return {'image': _public_image(attachment)}
    except (ValueError, RuntimeError, UnsupportedDocument) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.get("/api/question-images/{image_id}/file")
def question_image_file(image_id: str) -> FileResponse:
    image = db.get_question_image(image_id)
    if not image or not Path(image["path"]).is_file():
        raise HTTPException(status_code=404, detail="题目图片不存在。")
    if Path(image['path']).suffix.lower() == '.png':
        return FileResponse(image['path'], media_type='image/png')
    return FileResponse(image['path'], filename=image['name'], content_disposition_type='inline')


@app.delete("/api/question-images/{image_id}")
def remove_question_image(image_id: str) -> dict[str, bool]:
    image = db.get_question_image(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="题目图片不存在。")
    linked = any(any(item["id"] == image_id for item in exchange["images"])
                 for exchange in db.list_exchanges(image["notebook_id"], include_deleted=True))
    if linked:
        return {"deleted": False, "retained_in_history": True}
    db.delete_question_image(image_id)
    Path(image["path"]).unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), notebook_id: str = "default") -> dict[str, Any]:
    _notebook(notebook_id)
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持该文件格式，支持：{supported}",
        )

    document_id = uuid4().hex
    target = UPLOAD_DIR / f"{document_id}{suffix}"
    size = 0
    imported = False
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件超过 120 MB 限制。")
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="文件为空。")

        chunks, page_count = await run_in_threadpool(
            parse_document,
            target,
            original_name,
        )
        document = {
            "id": document_id,
            "name": original_name,
            "kind": suffix.lstrip("."),
            "path": str(target),
            "size_bytes": size,
            "page_count": page_count,
            "notebook_id": notebook_id,
        }
        db.insert_document(document, chunks)
        unread = await _ensure_source_images([document], db.get_settings())
        document = db.get_document(document_id)
        if not document or not document['chunk_count']:
            raise HTTPException(status_code=400, detail="未能从资料中识别出文字。请配置图片识别模型、安装中文 OCR，或检查图片清晰度。")
        _invalidate_index()
        imported = True
        return {"document": document, "unread_image_pages": unread}
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except (UnsupportedDocument, RuntimeError, ValueError) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"解析失败：{exc}") from exc
    finally:
        if not imported:
            db.delete_document(document_id)
        await file.close()


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str) -> dict[str, bool]:
    document = db.delete_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="资料不存在。")
    Path(document["path"]).unlink(missing_ok=True)
    _invalidate_index()
    return {"deleted": True}


@app.get("/api/documents/{document_id}/file")
def document_file(document_id: str) -> FileResponse:
    document = db.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="资料不存在。")
    path = Path(document["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原始文件已丢失。")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=document["name"],
        content_disposition_type="inline",
    )


@app.get('/api/documents/{document_id}/preview')
def document_preview(document_id: str) -> FileResponse:
    document = db.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail='资料不存在。')
    if document['kind'] == 'pdf':
        return document_file(document_id)
    if document['kind'] != 'pptx':
        raise HTTPException(status_code=400, detail='此格式暂不支持原页预览。')
    try:
        path = presentation_pdf(document)
    except PreviewError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return FileResponse(path, media_type='application/pdf', content_disposition_type='inline',
                        filename=Path(document['name']).stem + '.pdf')


@app.get('/api/documents/{document_id}/preview-lines')
def document_preview_lines(document_id: str, page: int = Query(ge=1)) -> dict[str, Any]:
    document = db.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail='资料不存在。')
    if document['kind'] != 'pptx':
        raise HTTPException(status_code=400, detail='此接口只用于课件预览标注。')
    if page > document['page_count']:
        raise HTTPException(status_code=400, detail='课件页码超出范围。')
    try:
        return presentation_page_geometry(document, page)
    except PreviewError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.patch("/api/documents/{document_id}")
def move_document(document_id: str, payload: MoveDocumentRequest) -> dict[str, Any]:
    _notebook(payload.notebook_id)
    if not db.get_document(document_id):
        raise HTTPException(status_code=404, detail="资料不存在。")
    db.move_document(document_id, payload.notebook_id)
    return {"document": db.get_document(document_id)}


@app.get("/api/documents/{document_id}/chunks")
def document_chunks(document_id: str) -> dict[str, Any]:
    if not db.get_document(document_id):
        raise HTTPException(status_code=404, detail="资料不存在。")
    results = [chunk for chunk in db.all_chunks() if chunk["document_id"] == document_id]
    return {"chunks": [{**chunk, "index": i} for i, chunk in enumerate(results, 1)]}


@app.get("/api/sources/search")
def search_sources(q: str = Query(min_length=1, max_length=500),
                   notebook_id: str = "default", document_id: str | None = None) -> dict[str, Any]:
    docs = _scope(notebook_id, [document_id] if document_id else None)
    ids = {doc["id"] for doc in docs}
    results = _index().search(q, top_k=20, document_ids=ids) if ids else []
    contexts = [focus_source(context, q) for context in _contexts_for_results(results)]
    for context in contexts:
        context["snippet"] = excerpt(context["text"], q, limit=190)
    return {"results": contexts,
            "documents": [doc for doc in docs if q.casefold() in doc["name"].casefold()]}


@app.get("/api/notes")
def notes(notebook_id: str = "default") -> dict[str, Any]:
    _notebook(notebook_id)
    return {"notes": db.list_notes(notebook_id)}


@app.post("/api/notes", status_code=201)
def create_note(payload: NoteRequest) -> dict[str, Any]:
    _notebook(payload.notebook_id)
    _scope(payload.notebook_id, [c.document_id for c in payload.citations])
    note = db.save_note(uuid4().hex, payload.notebook_id, _name(payload.title),
                        _name(payload.content), [c.model_dump() for c in payload.citations])
    return {"note": note}


@app.patch("/api/notes/{note_id}")
def edit_note(note_id: str, payload: NoteEditRequest) -> dict[str, Any]:
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在。")
    return {"note": db.save_note(note_id, note["notebook_id"], _name(payload.title),
                                 _name(payload.content), note["citations"])}


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str) -> dict[str, bool]:
    if not db.get_note(note_id):
        raise HTTPException(status_code=404, detail="笔记不存在。")
    db.delete_note(note_id)
    return {"deleted": True}


def _note_markdown(note: dict[str, Any]) -> str:
    title = note["title"].replace("\n", " ")
    parts = [f"# {title}", note["content"]]
    if note["citations"]:
        parts.append("## 参考来源")
        for c in note["citations"]:
            page = f"，第 {c['page']} 页" if c.get("page") else ""
            name = c["document_name"].replace("\n", " ")
            parts.append(f"[{c['index']}] {name}{page}\n\n> " + c["text"].replace("\n", "\n> "))
    return "\n\n".join(parts) + "\n"


def _markdown_response(content: str, filename: str) -> Response:
    return Response(content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})


@app.get("/api/notes/{note_id}/export")
def export_note(note_id: str) -> Response:
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在。")
    return _markdown_response(_note_markdown(note), note["title"][:80] + ".md")


@app.get("/api/notebooks/{notebook_id}/export")
def export_notebook(notebook_id: str) -> Response:
    notebook = _notebook(notebook_id)
    content = "\n\n---\n\n".join(_note_markdown(note) for note in db.list_notes(notebook_id))
    return _markdown_response(content or f"# {notebook['name']}\n\n尚无笔记。\n", notebook["name"] + ".md")


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return _public_settings(db.get_settings())


@app.put("/api/settings")
def update_settings(payload: SettingsRequest) -> dict[str, Any]:
    current = db.get_settings()
    values = payload.model_dump(exclude_none=True)
    if any(key in values and values[key] != current.get(key) for key in ('model', 'base_url')) and 'context_window' not in values:
        values['context_window'] = 0
    vision_provider_changed = "vision_base_url" in values and str(values["vision_base_url"]).strip().rstrip("/") != str(current.get("vision_base_url", "")).rstrip("/")
    if vision_provider_changed and not str(values.get("vision_api_key", "")).strip():
        values["vision_api_key"] = ""
    for url_key in ("base_url", "vision_base_url"):
        if url_key not in values:
            continue
        values[url_key] = str(values[url_key]).strip()
        if values[url_key] and not values[url_key].startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="接口地址需要以 http:// 或 https:// 开头。",
            )
    for model_key in ("model", "vision_model"):
        if model_key in values:
            values[model_key] = str(values[model_key]).strip()
    for key in ("api_key", "vision_api_key"):
        if key not in values:
            continue
        api_key = str(values[key]).strip()
        if not api_key and current.get(key) and not (key == "vision_api_key" and vision_provider_changed):
            values.pop(key)
        else:
            values[key] = api_key
    merged = db.update_settings(values)
    return _public_settings(merged)


@app.get('/api/model-context')
async def detect_model_context() -> dict[str, Any]:
    return await resolve_context(db.get_settings(), force=True)


@app.post("/api/ask")
async def ask(payload: AskRequest) -> dict[str, Any]:
    payload = payload.model_copy(update={'notebook_id': payload.notebook_id or 'default'})
    _notebook(payload.notebook_id)
    exchange_id = payload.request_id or uuid4().hex
    return await generation.run(payload.notebook_id, exchange_id, lambda: _ask(payload, exchange_id))


async def _ask(payload: AskRequest, exchange_id: str) -> dict[str, Any]:
    settings = db.get_settings()
    model_context = await resolve_context(settings)
    top_k = payload.top_k or int(settings.get("top_k", 6))
    selected_documents = _scope(payload.notebook_id, payload.document_ids)
    if not payload.question.strip() and not payload.image_ids:
        raise HTTPException(status_code=400, detail="请输入问题或上传问题附件。")
    images = []
    for image_id in dict.fromkeys(payload.image_ids):
        image = db.get_question_image(image_id)
        if not image or image["notebook_id"] != (payload.notebook_id or "default"):
            raise HTTPException(status_code=400, detail="问题附件不在当前笔记本中或已被删除。")
        images.append(image)
    if sum(Path(item['path']).suffix.lower() == '.png' for item in images) > 3:
        raise HTTPException(status_code=400, detail='每次最多上传 3 张题目图片。')
    document_ids = {doc["id"] for doc in selected_documents}
    reply_to = {}
    parent = None
    if payload.reply_to_id:
        parent = db.get_exchange(payload.notebook_id, payload.reply_to_id)
        if not parent or parent['status'] != 'complete' or not parent['result'].get('answer'):
            raise HTTPException(status_code=400, detail='引用的回答不在当前笔记本中、已删除或尚未完成，请重新选择。')
        reply_to = {'id': parent['id'], 'question': parent['question'], 'answer': parent['result']['answer'],
                    'image_question_text': parent['result'].get('image_question_text', ''),
                    'citation_indices': [c['index'] for c in parent['result'].get('citations', [])]}
    started = time.perf_counter()

    document_names = [document["name"] for document in selected_documents]
    question = payload.question.strip() or "请解答附件中的题目，并结合选中的课程资料。"
    previous_exchanges = [parent] if parent else db.recent_completed_exchanges(payload.notebook_id)
    evidence_reserve = source_reserve(selected_documents) + (80000 if images else 0)
    current_input = question + (payload.image_text or '') + ''.join(item['ocr_text'] for item in images)
    if not parent:
        # Resolve old citation numbers and attachment follow-ups only inside the
        # same model-bounded history that ordinary conversation can actually use.
        _, retained_turns = recent_context(previous_exchanges, history_budget(
            model_context, settings, current_input, evidence_reserve))
        previous_exchanges = previous_exchanges[-retained_turns:] if retained_turns else []
    references = referenced_sources(question, previous_exchanges, document_ids)
    reference_hint = '\n'.join(f"历史引用 {c['index']} → 《{c['document_name'][:150]}》" +
                               (f"第 {c['page']} 页" if c.get('page') else '对应文字片段') for c in references)
    try:
        attachment_followup = recover_attachment_problem(question, previous_exchanges, payload.notebook_id) if not images else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    attachment_text = attachment_followup['text'] if attachment_followup else ''
    budget = history_budget(model_context, settings, question + reference_hint + attachment_text +
                            (payload.image_text or '') + ''.join(item['ocr_text'] for item in images) +
                            str(reply_to.get('answer', '')), evidence_reserve)
    history, context_turns = recent_context(previous_exchanges, budget) if not reply_to else ('', 0)
    if attachment_followup and not reply_to:
        # The exact original exercise is sufficient context. Do not carry an
        # unrelated or incorrect old solution into this targeted follow-up.
        history, context_turns = '', 0
    retained_attachments = images
    if attachment_followup:
        identities = dict.fromkeys(item['attachment_id'] for item in attachment_followup['references'])
        retained_attachments = [db.get_question_image(identity) for identity in identities]
    db.start_exchange(exchange_id, payload.notebook_id or "default", question,
                      [_public_image(item) for item in retained_attachments if item], reply_to)

    try:
        db.set_generation_progress(exchange_id, 'question', '读取问题附件' if images else '分析问题',
                                   '正在读取本次附件中的题干与已知条件。' if images else '正在理解问题和近期对话。')
        image_text, image_mode = await read_question(images, settings, payload.image_text) if images else ("", "")
        if attachment_text:
            db.set_generation_progress(exchange_id, 'question', '重读附件题干',
                                       '正在按指定题号读取最近问题的原始附件，核对完整条件。')
            image_text, image_mode = attachment_text, 'attachment_followup'
        effective_question = question
        if history:
            effective_question = (f"本次问题：{question}\n\n近期对话（按时间先后，仅用于理解指代；旧回答可能有错，"
                                  f"不是上传资料依据，历史引用编号不能用于本次引用）：\n{history}\n\n"
                                  "请以本次问题为准；用户换话题时不要强行沿用旧主题。资料依据必须重新从本次提供的资料片段中取得。")
        if reply_to:
            indices = set(reply_to['citation_indices'])
            previous = re.sub(r'\[(\d+)\]', lambda m: f'【历史引用{m[1]}】' if int(m[1]) in indices else m[0], reply_to['answer'])
            effective_question = (f"本次追问：{question}\n\n用户明确引用的上一条问答（仅作为上下文，历史引用编号不能用于本次资料引用）："
                                  f"\n原问题：{reply_to['question']}\n原题目图片识别内容：{reply_to['image_question_text']}\n"
                                  f"原回答：\n{previous}\n\n请针对本次追问解释；资料依据必须重新从本次提供的资料片段中取得。")
        if image_text:
            effective_question += "\n\n用户本次问题的附件内容（用于理解题目，不属于课程来源，不要为附件编造来源引用；不确定处请指出）：\n" + image_text
        if attachment_followup:
            effective_question += '\n请以以上原始题干为准，逐项核对条件；历史回答可能解错题，不得据旧解答反推或补造题目。'
        if reference_hint:
            effective_question += '\n\n用户所指的历史引用定位（编号不是页码，本次依据会重新读取原文）：\n' + reference_hint
        db.set_generation_progress(exchange_id, 'analysis', '规划检索', '正在判断是否需要资料，并提炼检索关键词。')
        analysis = await analyze_question(
            effective_question,
            document_names,
            settings,
        )
        formula_inventory = is_formula_inventory(question)
        if formula_inventory:
            analysis.update(intent='summary', needs_retrieval=True)
        if attachment_followup or any(Path(item['path']).suffix.lower() != '.png' for item in images):
            # A question document is supplied in full as the current question,
            # not a request to summarize every selected course source.
            analysis['intent'] = 'question'
        named_scope = named_question_documents(question, selected_documents) if not references else None
        if named_scope is not None and document_ids:
            if not named_scope:
                raise HTTPException(status_code=400, detail='你指定的章节或课件无法在当前勾选来源中定位，请勾选对应资料，或使用 L01 等明确名称。')
            selected_documents = named_scope
            document_ids = {doc['id'] for doc in selected_documents}
            document_names = [doc['name'] for doc in selected_documents]
            analysis['needs_retrieval'] = True
            if is_exercise_question(question):
                analysis['intent'] = 'question'
        if (images or reply_to or references) and document_ids:
            analysis["needs_retrieval"] = True
        if analysis["intent"] == "summary":
            analysis["needs_retrieval"] = True
        contexts: list[dict[str, Any]] = []
        unread = []
        if analysis["needs_retrieval"] and document_ids:
            db.set_generation_progress(exchange_id, 'sources', '检查资料', f'正在检查已选的 {len(selected_documents)} 份资料及图片页识别结果。')
            if analysis["intent"] == "summary":
                selected_documents = summary_documents(question, selected_documents)
                if not selected_documents:
                    raise HTTPException(status_code=400, detail="你提到的课件不在当前勾选的来源中，请先勾选该课件。")
                document_names = [document["name"] for document in selected_documents]
                if formula_inventory:
                    unread = await _ensure_source_images(selected_documents, settings, exchange_id, formulas=True)
                else:
                    unread = await _ensure_source_images(selected_documents, settings, exchange_id)
                db.set_generation_progress(exchange_id, 'retrieval', '读取课件内容', f'正在整理 {len(selected_documents)} 份课件的完整页面。')
                contexts = _summary_contexts([document["id"] for document in selected_documents])
            elif references:
                referenced_documents = [doc for doc in selected_documents if doc['id'] in {c['document_id'] for c in references}]
                unread = await _ensure_source_images(referenced_documents, settings, exchange_id)
                pages = {(c['document_id'], c['page']) for c in references if c.get('page') is not None}
                chunk_ids = {c['chunk_id'] for c in references if c.get('page') is None}
                contexts = [c for c in _summary_contexts([doc['id'] for doc in referenced_documents])
                            if (c['document_id'], c.get('page')) in pages or c['chunk_id'] in chunk_ids]
                contexts = [{**c, 'index': index} for index, c in enumerate(contexts, 1)]
                db.set_generation_progress(exchange_id, 'retrieval', '读取所指原文',
                                           '已将历史引用编号定位到真实页面，正在读取对应题干与资料。',
                                           activity=source_activity(contexts))
            else:
                unread = await _ensure_source_images(selected_documents, settings, exchange_id)
                problems = exercise_sources(question, _summary_contexts([doc['id'] for doc in selected_documents])) if named_scope and is_exercise_question(question) else []
                if problems:
                    contexts = [{**context, 'index': index} for index, context in enumerate(problems, 1)]
                    db.set_generation_progress(exchange_id, 'retrieval', '读取习题',
                                               f'在指定课件中找到 {len(contexts)} 个带题目或例题标记的页面，正在读取完整题干。',
                                               activity=source_activity(contexts))
                else:
                    combined_query = " ".join(dict.fromkeys([analysis["search_query"], *analysis["keywords"]]))
                    if images:
                        # Diagram identifiers are task conditions, not retrieval concepts.
                        combined_query = re.sub(r"\b[A-Za-z]\d+\b", " ", combined_query)
                    retrieval_query = combined_query
                    db.set_generation_progress(exchange_id, 'retrieval', '检索资料',
                                               f'正在 {len(selected_documents)} 份勾选来源中查找相关内容。',
                                               activity=source_activity(selected_documents, combined_query))
                    results = _index().search(
                        combined_query,
                        top_k=min(20, top_k * 3),
                        document_ids=document_ids,
                    )
                    if not results and combined_query != effective_question:
                        retrieval_query = ''  # Do not expose the full conversational prompt as search keywords.
                        db.set_generation_progress(exchange_id, 'retrieval', '补充检索',
                                                   '关键词未命中，正在结合本次问题与上下文补充查找。',
                                                   activity=source_activity(selected_documents))
                        results = _index().search(
                            effective_question,
                            top_k=min(20, top_k * 3),
                            document_ids=document_ids,
                        )
                    candidates = _contexts_for_results(results)
                    db.set_generation_progress(exchange_id, 'evidence', '核对候选原文',
                                               f'找到 {len(candidates)} 个候选片段，正在核对下列资料中的原文。' if candidates else '未找到匹配片段，正在检查是否有可用依据。',
                                               activity=source_activity(candidates, retrieval_query))
                    contexts = await select_evidence(effective_question, candidates, settings, top_k)
        retrieval_hits = len(contexts)
        coverage = _summary_scope(contexts, selected_documents) if analysis["intent"] == "summary" else None
        if coverage is not None:
            coverage['unread_image_pages'] = unread
        if unread:
            effective_question += f"\n资料解析状态：{unread}。这些页面含图片但识别失败，不是未提供页面，不要猜测图片内容。"
        db.set_generation_progress(exchange_id, 'answer', '生成回答',
                                   f'已选出 {len(contexts)} 处依据，正在结合下列资料组织回答。' if contexts else '正在生成回答；本次没有可引用的资料依据。',
                                   activity=source_activity(contexts))
        if analysis["intent"] == "summary":
            answer = await _generate_summary(contexts, settings, effective_question, exchange_id, selected_documents,
                                             formula_inventory=formula_inventory)
            if formula_inventory and unread:
                answer += '\n\n## 未能读取的公式页\n\n' + '\n'.join(
                    f"- 《{item['document_name']}》第 {', '.join(map(str, item['pages']))} 页：图片或公式对象尚未成功识别，以上清单不保证完整。"
                    for item in unread)
        else:
            answer = await answer_question(effective_question, contexts, settings,
                                           document_names, analysis["needs_retrieval"])
        answer, contexts = cited_sources(answer, contexts)
        if coverage is not None:
            coverage.update(summary_coverage_data(coverage, contexts))
    except Exception as exc:
        detail = _generation_failure(exchange_id, payload.notebook_id or 'default', exc)
        if isinstance(exc, HTTPException):
            exc.headers = {**(exc.headers or {}), "X-Chat-Id": exchange_id}
            raise
        raise HTTPException(status_code=502, detail=detail, headers={"X-Chat-Id": exchange_id}) from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    result = {
        "chat_id": exchange_id,
        "answer": answer,
        "citations": contexts,
        "model": settings.get("model") or None,
        "mode": "model" if model_configured(settings) else "extractive",
        "source_mode": "knowledge_base" if contexts else "general_knowledge",
        "intent": analysis["intent"],
        "output_mode": "formulas" if formula_inventory else analysis["intent"],
        "search_query": analysis["search_query"],
        "retrieval_hits": retrieval_hits,
        "summary_coverage": coverage,
        "elapsed_ms": elapsed_ms,
        "image_question_text": image_text,
        "image_recognition_mode": image_mode,
        "question_attachment_refs": attachment_followup['references'] if attachment_followup else [],
        "context_turns": context_turns,
        "context_window": model_context['context_window'],
        "context_window_source": model_context['source'],
        "history_tokens_estimated": estimate_tokens(history),
    }
    if not db.finish_pending_exchange(exchange_id, result):
        raise generation.cancelled_error(exchange_id)
    return result


def summary_coverage(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documents": list(dict.fromkeys(c["document_name"] for c in contexts)),
            "pages": len({(c["document_id"], c["page"]) for c in contexts if c["page"] is not None}),
            "sources": len(contexts)}


def summary_coverage_data(coverage: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    sources = coverage.get('selected_sources', [{'name': name} for name in coverage['documents']])
    return {'cited_documents': list(dict.fromkeys(c['document_name'] for c in citations)),
            'cited_document_ids': list(dict.fromkeys(c['document_id'] for c in citations)),
            'document_details': [{**source,
                                  'read_pages': coverage.get('pages_by_document', {}).get(source.get('id')),
                                  'read_sources': coverage.get('sources_by_document', {}).get(source.get('id')),
                                  'cited_pages': len({c.get('page') for c in citations if c['document_id'] == source.get('id') and c.get('page') is not None}),
                                  'citations': sum(c['document_id'] == source.get('id') for c in citations)} for source in sources]}


def _summary_scope(contexts: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = summary_coverage(contexts)
    coverage['selected_documents'] = [doc['name'] for doc in documents]
    coverage['selected_sources'] = [{'id': doc['id'], 'name': doc['name']} for doc in documents]
    coverage['read_document_ids'] = list(dict.fromkeys(c['document_id'] for c in contexts))
    coverage['pages_by_document'] = {doc['id']: len({c['page'] for c in contexts if c['document_id'] == doc['id'] and c['page'] is not None}) for doc in documents}
    coverage['sources_by_document'] = {doc['id']: sum(c['document_id'] == doc['id'] for c in contexts) for doc in documents}
    return coverage


async def _generate_summary(contexts: list[dict[str, Any]], settings: dict[str, Any], question: str,
                            exchange_id: str, documents: list[dict[str, Any]], *, formula_inventory: bool = False) -> str:
    # Formula transcription is much denser than ordinary prose; keep each model
    # request smaller without sampling or dropping any pages.
    batches = summary_batches(contexts, character_limit=8000) if formula_inventory else summary_batches(contexts)
    answers = []
    for index, batch in enumerate(batches, 1):
        name = batch[0]['document_name']
        db.set_generation_progress(exchange_id, 'answer', '逐份整理资料',
                                   f'正在整理《{name}》，第 {index}/{len(batches)} 批，本批 {len(batch)} 个资料片段。',
                                   activity=source_activity(batch))
        focus = '只列实际公式、符号含义和适用条件，不生成复习提纲，' if formula_inventory else '保留本部分独有的概念、公式、算法、题型与例题，'
        requirement = (question + f'\n本次仅整理《{name}》的所给页面。' + focus +
                       '覆盖开头、中间和末尾；引用只能使用本次页面的编号，不要写其他课件的内容。')
        if formula_inventory:
            answer = await make_summary(batch, settings, question=requirement, formula_inventory=True)
        else:
            answer = await make_summary(batch, settings, question=requirement)
        answer, used = cited_sources(answer, batch, renumber=False)
        if model_configured(settings) and not used:
            raise RuntimeError(f'《{name}》的整理没有提供有效资料引用，无法确认已覆盖该来源，请重新生成。')
        if len(batches) > 1:
            answer = f"# {' '.join(name.split())}（第 {index}/{len(batches)} 批）\n\n{answer}"
        answers.append(answer)
    if not batches:
        return '给定资料中未找到可整理的内容。'
    missing = [doc['name'] for doc in documents if doc['id'] not in {c['document_id'] for c in contexts}]
    if missing:
        answers.append('# 未能整理的来源\n\n' + '\n'.join(f'- 《{name}》：尚无可用文字，请检查解析或图片识别。' for name in missing))
    return '\n\n'.join(answers)


@app.post("/api/summary")
async def summary(payload: SummaryRequest) -> dict[str, Any]:
    payload = payload.model_copy(update={'notebook_id': payload.notebook_id or 'default'})
    _notebook(payload.notebook_id)
    exchange_id = payload.request_id or uuid4().hex
    return await generation.run(payload.notebook_id, exchange_id, lambda: _summary(payload, exchange_id))


async def _summary(payload: SummaryRequest, exchange_id: str) -> dict[str, Any]:
    settings = db.get_settings()
    selected = _scope(payload.notebook_id, payload.document_ids)
    selected = summary_documents(payload.focus or "", selected)
    if not selected:
        raise HTTPException(status_code=400, detail="未找到可整理的指定章节或来源，请先勾选对应资料，或使用 L01 等明确名称。")
    question = "根据当前选中的来源生成一份复习提纲。"
    if payload.focus:
        question += f"复习重点：{payload.focus}"
    db.start_exchange(exchange_id, payload.notebook_id or "default", question)
    try:
        db.set_generation_progress(exchange_id, 'sources', '检查资料', f'正在检查 {len(selected)} 份资料及图片页识别结果。')
        unread = await _ensure_source_images(selected, settings, exchange_id)
        db.set_generation_progress(exchange_id, 'retrieval', '读取课件内容', '正在汇总选中课件的完整页面。')
        contexts = _summary_contexts([doc["id"] for doc in selected])
        if not contexts:
            raise HTTPException(status_code=400, detail="资料尚未解析出可整理的文字，请检查图片识别状态。")
        coverage = _summary_scope(contexts, selected)
        coverage['unread_image_pages'] = unread
        if unread:
            question += f"\n资料解析状态：{unread}。这些页含图片但识别失败，不是未提供页面，不要猜测图片内容。"
        db.set_generation_progress(exchange_id, 'answer', '生成复习提纲', f'已读取 {len(contexts)} 个资料片段，正在整理下列课件的提纲与引用。',
                                   activity=source_activity(contexts))
        answer = await _generate_summary(contexts, settings, question, exchange_id, selected)
        answer, contexts = cited_sources(answer, contexts)
        coverage.update(summary_coverage_data(coverage, contexts))
    except Exception as exc:
        detail = _generation_failure(exchange_id, payload.notebook_id or 'default', exc)
        if isinstance(exc, HTTPException):
            exc.headers = {**(exc.headers or {}), "X-Chat-Id": exchange_id}
            raise
        raise HTTPException(status_code=502, detail=detail, headers={"X-Chat-Id": exchange_id}) from exc
    result = {
        "chat_id": exchange_id,
        "answer": answer,
        "citations": contexts,
        "mode": "model" if model_configured(settings) else "extractive",
        "summary_coverage": coverage,
    }
    if not db.finish_pending_exchange(exchange_id, result):
        raise generation.cancelled_error(exchange_id)
    return result


@app.post('/api/quiz')
async def create_quiz(payload: QuizRequest) -> dict[str, Any]:
    payload = payload.model_copy(update={'notebook_id': payload.notebook_id or 'default'})
    _notebook(payload.notebook_id)
    exchange_id = payload.request_id or uuid4().hex
    return await generation.run(payload.notebook_id, exchange_id, lambda: _quiz(payload, exchange_id))


async def _quiz(payload: QuizRequest, exchange_id: str) -> dict[str, Any]:
    settings = db.get_settings()
    selected = summary_documents(payload.topic, _scope(payload.notebook_id, payload.document_ids))
    if not selected:
        raise HTTPException(status_code=400, detail='请勾选测验来源；指定章节必须包含在勾选来源中。')
    if not model_configured(settings):
        raise HTTPException(status_code=400, detail='请先配置模型，再生成交互式测验。')
    question = f'根据勾选来源生成 {COUNTS[payload.count]} 道交互式测验。'
    if payload.topic:
        question += f'主题：{payload.topic}'
    db.start_exchange(exchange_id, payload.notebook_id, question)
    try:
        unread = await _ensure_source_images(selected, settings, exchange_id)
        contexts = _summary_contexts([d['id'] for d in selected])
        if not contexts:
            raise HTTPException(status_code=400, detail='资料尚无可用于出题的文字，请先检查解析或图片识别。')
        async def on_batch(batch: list[dict], index: int, total: int) -> None:
            db.set_generation_progress(exchange_id, 'quiz', '生成测验',
                f"正在依据《{batch[0]['document_name']}》出题，第 {index}/{total} 批，读取本批 {len(batch)} 个片段。",
                activity=source_activity(batch))
        quiz = await build_quiz(contexts, settings, count=payload.count, difficulty=payload.difficulty,
                               language=payload.language, topic=payload.topic, on_batch=on_batch)
        citations = quiz.pop('citations')
        warnings = []
        if len(quiz['questions']) < COUNTS[payload.count]:
            warnings.append(f"依据可用资料生成了 {len(quiz['questions'])} 题，少于请求的 {COUNTS[payload.count]} 题。")
        if unread:
            warnings.extend(f"《{d['document_name']}》第 {', '.join(map(str, d['pages']))} 页图片未识别，未据此出题。" for d in unread)
        covered = {c['document_id'] for c in citations}
        warnings.extend(f"《{d['name']}》未出现在本次题目的引用中。" for d in selected if d['id'] not in covered)
        result = {'chat_id': exchange_id, 'answer': f"已生成 {len(quiz['questions'])} 道测验，选择答案后查看解析。",
                  'quiz': quiz, 'citations': citations, 'warnings': warnings, 'mode': 'model',
                  'source_mode': 'knowledge_base', 'intent': 'quiz'}
    except Exception as exc:
        detail = _generation_failure(exchange_id, payload.notebook_id, exc)
        if isinstance(exc, HTTPException):
            exc.headers = {**(exc.headers or {}), 'X-Chat-Id': exchange_id}
            raise
        raise HTTPException(status_code=502, detail=detail, headers={'X-Chat-Id': exchange_id}) from exc
    if not db.finish_pending_exchange(exchange_id, result):
        raise generation.cancelled_error(exchange_id)
    return result


@app.post('/api/notebooks/{notebook_id}/quiz/{exchange_id}/answer')
def answer_quiz(notebook_id: str, exchange_id: str, payload: QuizAnswerRequest) -> dict[str, Any]:
    _notebook(notebook_id)
    try:
        return save_attempt(notebook_id, exchange_id, payload.question_id, payload.option)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/notebooks/{notebook_id}/quiz/{exchange_id}')
def get_quiz(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    exchange = db.get_exchange(notebook_id, exchange_id)
    if not exchange or exchange['status'] != 'complete' or not exchange['result'].get('quiz'):
        raise HTTPException(status_code=404, detail='测验不存在、已删除或不属于当前笔记本。')
    return {**exchange['result'], 'chat_id': exchange_id, 'title': exchange['question'],
            'created_at': exchange['created_at']}


@app.post('/api/notebooks/{notebook_id}/quiz/{exchange_id}/reset')
def reset_quiz(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    try:
        return save_attempt(notebook_id, exchange_id, reset=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/api/notebooks/{notebook_id}/chat/{exchange_id}/cancel')
async def cancel_generation(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    return generation.cancel(notebook_id, exchange_id)


@app.get("/api/notebooks/{notebook_id}/chat")
def chat_history(notebook_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    return {"exchanges": db.list_exchanges(notebook_id), "deleted_count": db.deleted_exchange_count(notebook_id)}


@app.get('/api/notebooks/{notebook_id}/chat/{exchange_id}/status')
def generation_status(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    exchange = db.get_exchange(notebook_id, exchange_id)
    if not exchange:
        raise HTTPException(status_code=404, detail='请求不存在或不属于当前笔记本。')
    return {'status': exchange['status'], 'progress': exchange['result'].get('progress'),
            'created_at': exchange['created_at']}


@app.delete("/api/notebooks/{notebook_id}/chat")
def clear_chat(notebook_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    image_ids = {image["id"] for exchange in db.list_exchanges(notebook_id, include_deleted=True) for image in exchange["images"]}
    db.clear_exchanges(notebook_id, preserve_quizzes=True)
    retained_images = {image['id'] for exchange in db.list_exchanges(notebook_id, include_deleted=True) for image in exchange['images']}
    image_ids -= retained_images
    for image_id in image_ids:
        image = db.get_question_image(image_id)
        if image:
            db.delete_question_image(image_id)
            Path(image["path"]).unlink(missing_ok=True)
    return {"deleted": True, "deleted_image_ids": list(image_ids)}


@app.post("/api/notebooks/{notebook_id}/chat/undo")
def undo_chat_delete(notebook_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    exchange_id = db.restore_exchange(notebook_id)
    if exchange_id is None:
        raise HTTPException(status_code=404, detail="没有可以撤销的删除记录。")
    return {"restored": True, "chat_id": exchange_id, "deleted_count": db.deleted_exchange_count(notebook_id)}


@app.delete("/api/notebooks/{notebook_id}/chat/{exchange_id}")
def delete_chat_exchange(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    _notebook(notebook_id)
    try:
        deleted = db.delete_exchange(notebook_id, exchange_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if deleted is None:
        raise HTTPException(status_code=404, detail="这条对话不存在或不属于当前笔记本。")
    for image in deleted["images"]:
        Path(image["path"]).unlink(missing_ok=True)
    return {"deleted": True, "deleted_image_ids": [image["id"] for image in deleted["images"]],
            "deleted_count": db.deleted_exchange_count(notebook_id)}


class FrontendStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if not path.startswith('vendor/') and Path(path).suffix in {'.js', '.mjs', '.css', '.html'}:
            response.headers['Cache-Control'] = 'no-cache'
        return response


app.mount("/static", FrontendStaticFiles(directory=STATIC_DIR), name="static")


def _frontend_page(filename: str) -> HTMLResponse:
    # A changed entry URL also bypasses module caches created by older releases.
    # Include imported modules so any first-party frontend update changes it.
    digest = hashlib.sha256()
    for asset in sorted(STATIC_DIR.iterdir()):
        if asset.is_file() and asset.suffix in {'.js', '.mjs', '.css'}:
            digest.update(asset.name.encode())
            digest.update(asset.read_bytes())
    version = digest.hexdigest()[:16]
    html = (STATIC_DIR / filename).read_text(encoding='utf-8')
    for asset in ('app.js', 'quiz-page.js', 'styles.css'):
        html = html.replace(f'"/static/{asset}"', f'"/static/{asset}?v={version}"')
    return HTMLResponse(html, headers={'Cache-Control': 'no-cache'})


@app.get("/")
def index_page() -> HTMLResponse:
    return _frontend_page('index.html')


@app.get('/quiz/{exchange_id}')
def quiz_page(exchange_id: str, notebook_id: str = 'default') -> HTMLResponse:
    get_quiz(notebook_id, exchange_id)
    return _frontend_page('quiz.html')
