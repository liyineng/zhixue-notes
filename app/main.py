from __future__ import annotations

import mimetypes
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import db
from .config import (
    MAX_UPLOAD_BYTES,
    STATIC_DIR,
    SUPPORTED_SUFFIXES,
    UPLOAD_DIR,
    ensure_directories,
)
from .llm import analyze_question, answer_question, make_summary, model_configured
from .parsers import UnsupportedDocument, parse_document
from .retrieval import BM25Index, excerpt
from urllib.parse import quote

mimetypes.add_type("text/javascript", ".mjs")

_index_version = 0


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories()
    db.init_db()
    yield


app = FastAPI(
    title="智学笔记 API",
    version="0.2.0",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] | None = None
    notebook_id: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=12)


class SummaryRequest(BaseModel):
    document_ids: list[str] | None = None
    notebook_id: str | None = None
    focus: str = Field(default="", max_length=300)


class SettingsRequest(BaseModel):
    base_url: str | None = Field(default=None, max_length=1000)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=128, le=8192)
    top_k: int | None = Field(default=None, ge=1, le=12)


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
    citations: list[NoteCitation] = Field(default_factory=list, max_length=20)


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
    result = {**settings, "api_key": ""}
    result["api_key_set"] = bool(str(settings.get("api_key", "")).strip())
    result["model_configured"] = model_configured(settings)
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
            }
        )
    return contexts


def _summary_contexts(
    document_ids: list[str] | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    wanted = set(document_ids) if document_ids is not None else None
    chunks = [
        chunk
        for chunk in db.all_chunks()
        if wanted is None or chunk["document_id"] in wanted
    ]
    if not chunks:
        return []
    chunks.sort(key=lambda item: (item["document_id"], item["ordinal"]))
    if len(chunks) <= limit:
        selected = chunks
    else:
        selected = [
            chunks[round(index * (len(chunks) - 1) / (limit - 1))]
            for index in range(limit)
        ]
    contexts: list[dict[str, Any]] = []
    for index, chunk in enumerate(selected, start=1):
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
            }
        )
    return contexts


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
    if not db.delete_empty_notebook(notebook_id):
        raise HTTPException(status_code=409, detail="请先移出资料并删除笔记，再删除空笔记本。")
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
        if not chunks:
            raise HTTPException(
                status_code=400,
                detail="没有从文件中解析出可检索文本。扫描版 PDF 需要先做 OCR。",
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
        _invalidate_index()
        document["chunk_count"] = len(chunks)
        return {"document": document}
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
    contexts = _contexts_for_results(results)
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
    if "base_url" in values:
        values["base_url"] = str(values["base_url"]).strip()
        if values["base_url"] and not values["base_url"].startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="接口地址需要以 http:// 或 https:// 开头。",
            )
    if "model" in values:
        values["model"] = str(values["model"]).strip()
    if "api_key" in values:
        api_key = str(values["api_key"]).strip()
        if not api_key and current.get("api_key"):
            values.pop("api_key")
        else:
            values["api_key"] = api_key
    merged = db.update_settings(values)
    return _public_settings(merged)


@app.post("/api/ask")
async def ask(payload: AskRequest) -> dict[str, Any]:
    settings = db.get_settings()
    top_k = payload.top_k or int(settings.get("top_k", 6))
    selected_documents = _scope(payload.notebook_id, payload.document_ids)
    document_ids = {doc["id"] for doc in selected_documents}
    started = time.perf_counter()

    document_names = [document["name"] for document in selected_documents]

    try:
        analysis = await analyze_question(
            payload.question,
            document_names,
            settings,
        )
        contexts: list[dict[str, Any]] = []
        if analysis["needs_retrieval"] and document_ids:
            if analysis["intent"] == "summary":
                contexts = _summary_contexts(
                    list(document_ids),
                    limit=min(12, max(top_k, 8)),
                )
            else:
                combined_query = " ".join(
                    dict.fromkeys(
                        [
                            analysis["search_query"],
                            *analysis["keywords"],
                            payload.question,
                        ]
                    )
                )
                results = _index().search(
                    combined_query,
                    top_k=top_k,
                    document_ids=document_ids,
                )
                if not results and combined_query != payload.question:
                    results = _index().search(
                        payload.question,
                        top_k=top_k,
                        document_ids=document_ids,
                    )
                contexts = _contexts_for_results(results)

        answer = await answer_question(
            payload.question,
            contexts,
            settings,
            document_names,
            analysis["needs_retrieval"],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return {
        "answer": answer,
        "citations": contexts,
        "model": settings.get("model") or None,
        "mode": "model" if model_configured(settings) else "extractive",
        "source_mode": "knowledge_base" if contexts else "general_knowledge",
        "intent": analysis["intent"],
        "search_query": analysis["search_query"],
        "retrieval_hits": len(contexts),
        "elapsed_ms": elapsed_ms,
    }


@app.post("/api/summary")
async def summary(payload: SummaryRequest) -> dict[str, Any]:
    settings = db.get_settings()
    selected = _scope(payload.notebook_id, payload.document_ids)
    contexts = _summary_contexts([doc["id"] for doc in selected])
    if not contexts:
        raise HTTPException(status_code=400, detail="请先上传资料。")
    if payload.focus:
        focus = payload.focus.strip()
        contexts = [
            {
                **context,
                "text": f"复习重点：{focus}\n{context['text']}",
            }
            for context in contexts
        ]
    try:
        answer = await make_summary(contexts, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "answer": answer,
        "citations": contexts,
        "mode": "model" if model_configured(settings) else "extractive",
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
