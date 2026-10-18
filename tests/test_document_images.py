import asyncio
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pymupdf
from PIL import Image
import test_workspace
from app import db, main
from app.document_images import ensure_image_text, ocr_lines


class DocumentImageTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_summary_is_saved_before_waiting_for_image_recognition(self):
        notebook = self.notebook('图片识别等待恢复')
        document = self.upload(notebook, '资料.md', '网络协议规定通信规则。')
        async def recognize(source, settings):
            self.assertEqual(source['id'], document)
            history = db.list_exchanges(notebook)
            self.assertEqual(history[0]['status'], 'pending')
            return {'added_pages': [], 'unread_pages': []}
        with patch.object(main, 'ensure_image_text', side_effect=recognize):
            response = self.client.post('/api/summary', json={'notebook_id': notebook, 'document_ids': [document]})
        self.assertEqual(response.status_code, 200, response.text)

    def image_pdf(self, mixed=False):
        image = io.BytesIO()
        Image.new('RGB', (1000, 300), 'white').save(image, format='PNG')
        with pymupdf.open() as pdf:
            if mixed:
                page = pdf.new_page(width=600, height=220)
                page.insert_text((20, 30), 'This is native course text about network protocols and source addresses.')
            page = pdf.new_page(width=600, height=220)
            page.insert_image(pymupdf.Rect(20, 20, 580, 200), stream=image.getvalue())
            return pdf.tobytes()

    def test_image_only_pdf_import_is_indexed_with_page_coordinates_and_cached(self):
        notebook = self.notebook('图片课件')
        db.update_settings({'vision_model': 'test-vision'})
        content = self.image_pdf()
        recognizer = AsyncMock(return_value=('CRC 题目和原有解答：余数为 10。', 'vision'))
        with patch('app.document_images.read_question', recognizer):
            response = self.client.post('/api/documents', params={'notebook_id': notebook},
                files={'file': ('图片课件.pdf', content, 'application/pdf')})
            self.assertEqual(response.status_code, 200, response.text)
            document = response.json()['document']
            report = asyncio.run(ensure_image_text(document, db.get_settings()))
        self.assertEqual(report['added_pages'], [])
        self.assertEqual(recognizer.await_count, 1)
        self.assertIn('原有解答', recognizer.call_args.kwargs['instruction'])
        self.assertEqual(Path(document['path']).read_bytes(), content)
        chunks = [c for c in db.all_chunks() if c['document_id'] == document['id']]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['page'], 1)
        self.assertEqual(chunks[0]['lines'][0]['image_recognition'], 'vision')
        self.assertEqual([round(v) for v in chunks[0]['lines'][0]['bbox']], [20, 26, 580, 194])
        found = self.client.get('/api/sources/search', params={'notebook_id': notebook, 'q': 'CRC'}).json()['results']
        self.assertTrue(found)
        self.assertEqual(found[0]['page'], 1)
        self.assertEqual(found[0]['image_recognition'], ['vision'])
        other = self.notebook('其他课程')
        self.assertEqual(self.client.get('/api/sources/search', params={'notebook_id': other, 'q': 'CRC'}).json()['results'], [])

    def test_image_page_progress_names_page_before_recognition_and_skips_cached_pages(self):
        document = {'id': 'page-progress', 'name': '课件.pdf', 'kind': 'pdf', 'path': str(Path(self.temp.name) / '课件.pdf')}
        Path(document['path']).write_bytes(self.image_pdf())
        pages = []
        async def recognize(*args, **kwargs):
            self.assertEqual(pages, [1])
            return '图片题目与解答', 'vision'
        with patch('app.document_images.db.document_chunk_ids', return_value=[]), \
             patch('app.document_images.db.append_image_chunk', return_value=True), \
             patch('app.document_images.read_question', side_effect=recognize):
            report = asyncio.run(ensure_image_text(document, {'vision_model': 'mock'}, on_page=pages.append))
        self.assertEqual(report['added_pages'], [1])
        pages.clear()
        with patch('app.document_images.db.document_chunk_ids', return_value=['image-page-progress-1']), \
             patch('app.document_images.read_question', AsyncMock()) as recognizer:
            asyncio.run(ensure_image_text(document, {'vision_model': 'mock'}, on_page=pages.append))
        self.assertEqual(pages, [])
        recognizer.assert_not_called()

    def test_unread_image_page_is_reported_and_failed_empty_import_leaves_no_record(self):
        notebook = self.notebook('未识别图片')
        db.update_settings({'vision_model': 'test-vision'})
        with patch('app.document_images.read_question', AsyncMock(side_effect=RuntimeError('识别服务失败'))), \
             patch('app.document_images.ocr_lines', return_value=[]):
            response = self.client.post('/api/documents', params={'notebook_id': notebook},
                files={'file': ('混合课件.pdf', self.image_pdf(mixed=True), 'application/pdf')})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['unread_image_pages'][0]['pages'], [2])
            failed = self.client.post('/api/documents', params={'notebook_id': notebook},
                files={'file': ('全图片.pdf', self.image_pdf(), 'application/pdf')})
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(len(db.list_documents(notebook)), 1)
        self.assertEqual(len(list(main.UPLOAD_DIR.iterdir())), 1)

    def test_local_ocr_word_boxes_are_scaled_back_to_pdf_coordinates(self):
        tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
               '5\t1\t1\t1\t1\t1\t20\t10\t80\t20\t95\tCRC\n'
               '5\t1\t1\t1\t1\t2\t110\t10\t90\t20\t96\t11001\n')
        replies = [SimpleNamespace(stdout='List of languages:\nchi_sim\neng\n'), SimpleNamespace(stdout=tsv.encode())]
        with patch('app.document_images.shutil.which', return_value='/fixture/tesseract'), \
             patch('app.document_images.subprocess.run', side_effect=replies):
            lines = ocr_lines(b'png', 400, 200, 28, [10, 20, 210, 120])
        self.assertEqual(lines[0]['text'], 'CRC 11001')
        self.assertEqual(lines[0]['page'], 28)
        self.assertEqual(lines[0]['bbox'], [20, 25, 110, 35])
        self.assertEqual(lines[0]['image_recognition'], 'ocr')
