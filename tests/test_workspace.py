"""Notebook isolation and persistent notes, with no model or personal data calls."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db, main


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", root / "test.db")
        self.upload_patch = patch.object(main, "UPLOAD_DIR", root / "uploads")
        self.db_patch.start()
        self.upload_patch.start()
        main.UPLOAD_DIR.mkdir()
        main._invalidate_index()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.upload_patch.stop()
        self.db_patch.stop()
        main._invalidate_index()
        self.temp.cleanup()

    def notebook(self, name):
        response = self.client.post("/api/notebooks", json={"name": name})
        self.assertEqual(response.status_code, 201)
        return response.json()["notebook"]["id"]

    def upload(self, notebook_id, filename, content):
        response = self.client.post("/api/documents", params={"notebook_id": notebook_id},
                                    files={"file": (filename, content.encode(), "text/plain")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["document"]["id"]

    def test_notebook_scope_empty_selection_and_move(self):
        first = self.notebook("网络课程")
        second = self.notebook("信号课程")
        document_id = self.upload(first, "网络.md", "网络分层是一种体系结构设计方法。网络协议规定通信规则。")
        self.assertEqual(self.client.delete(f"/api/notebooks/{first}").status_code, 409)
        self.upload(second, "信号.md", "信号采样与频谱分析用于处理连续信号。")
        self.assertEqual(len(self.client.get("/api/documents", params={"notebook_id": first}).json()["documents"]), 1)
        self.assertTrue(self.client.get("/api/sources/search", params={"notebook_id": first, "q": "网络"}).json()["results"])
        self.assertEqual(self.client.get("/api/sources/search", params={"notebook_id": second, "q": "网络"}).json()["results"], [])
        empty = self.notebook("空笔记本")
        for payload in [{"notebook_id": empty}, {"notebook_id": first, "document_ids": []}]:
            result = self.client.post("/api/ask", json={**payload, "question": "网络分层是什么？"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["citations"], [])
            self.assertEqual(self.client.post("/api/summary", json=payload).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/notebooks/{empty}").status_code, 200)
        self.assertEqual(self.client.delete("/api/notebooks/default").status_code, 409)
        self.assertEqual(self.client.post("/api/ask", json={"question": "网络", "notebook_id": second,
                         "document_ids": [document_id]}).status_code, 400)
        moved = self.client.patch(f"/api/documents/{document_id}", json={"notebook_id": second})
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(self.client.get("/api/documents", params={"notebook_id": first}).json()["documents"], [])
        self.assertEqual(len(self.client.get("/api/documents", params={"notebook_id": second}).json()["documents"]), 2)

    def test_notes_edit_export_snapshot_survives_source_deletion(self):
        notebook_id = self.notebook("复习")
        document_id = self.upload(notebook_id, "采样.md", "采样定理要求采样频率满足条件。")
        citation = self.client.get("/api/sources/search", params={"notebook_id": notebook_id, "q": "采样"}).json()["results"][0]
        saved = self.client.post("/api/notes", json={"notebook_id": notebook_id, "title": "采样笔记",
                                "content": "依据课程资料 [1]", "citations": [citation]})
        self.assertEqual(saved.status_code, 201, saved.text)
        note_id = saved.json()["note"]["id"]
        db.init_db()  # Reopening and rerunning migrations preserves both notes and categories.
        self.assertEqual(len(self.client.get("/api/notes", params={"notebook_id": notebook_id}).json()["notes"]), 1)
        self.assertEqual(self.client.get("/api/notes", params={"notebook_id": "default"}).json()["notes"], [])
        self.client.patch(f"/api/notes/{note_id}", json={"title": "复习笔记", "content": "修改后的内容 [1]"})
        self.client.delete(f"/api/documents/{document_id}")
        exported = self.client.get(f"/api/notes/{note_id}/export")
        self.assertEqual(exported.status_code, 200)
        for expected in ["# 复习笔记", "修改后的内容 [1]", "参考来源", "采样.md", "采样定理"]:
            self.assertIn(expected, exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertIn("复习笔记", self.client.get(f"/api/notebooks/{notebook_id}/export").text)
        self.client.delete(f"/api/notes/{note_id}")
        self.assertEqual(self.client.get(f"/api/notes/{note_id}/export").status_code, 404)

    def test_validation_and_missing_notebooks(self):
        self.assertEqual(self.client.post("/api/notebooks", json={"name": "   "}).status_code, 400)
        self.assertEqual(self.client.get("/api/documents?notebook_id=missing").status_code, 404)
        self.assertEqual(self.client.post("/api/notes", json={"notebook_id": "default", "title": "x", "content": "  "}).status_code, 400)
        self.assertEqual(self.client.patch("/api/notebooks/default", json={"name": "新名称"}).status_code, 200)
        self.assertEqual(self.client.get("/api/notebooks").json()["notebooks"][0]["name"], "新名称")


class MigrationTests(unittest.TestCase):
    def test_legacy_documents_assigned_once_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("""CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    kind TEXT NOT NULL, path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0, chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL)""")
                conn.execute("INSERT INTO documents VALUES ('old', '旧资料', 'md', '/tmp/old', 10, 0, 0, '2026')")
                conn.commit()
            with patch.object(db, "DB_PATH", path):
                db.init_db()
                db.init_db()
                self.assertEqual(db.list_documents("default")[0]["id"], "old")
                self.assertEqual(db.list_notebooks()[0]["document_count"], 1)


if __name__ == "__main__":
    unittest.main()
