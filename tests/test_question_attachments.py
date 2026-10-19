"""Question documents are local attachments, never notebook retrieval sources."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pymupdf
from fastapi.testclient import TestClient
from app import db, main
from app.question_images import read_question
import asyncio


class QuestionAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(db, 'DB_PATH', root / 'test.db'),
                        patch.object(main, 'QUESTION_IMAGE_DIR', root / 'attachments'),
                        patch.object(main, 'UPLOAD_DIR', root / 'uploads')]
        for p in self.patches: p.start()
        main.QUESTION_IMAGE_DIR.mkdir(); main.UPLOAD_DIR.mkdir()
        self.client = TestClient(main.app); self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def upload(self, name, content):
        return self.client.post('/api/question-attachments', files={'file': (name, content)}, params={'notebook_id': 'default'})

    def test_document_is_attachment_and_survives_history_delete_undo(self):
        response = self.upload('本次题目.md', b'What is resonance?')
        self.assertEqual(response.status_code, 201, response.text)
        attachment = response.json()['image']
        self.assertEqual(attachment['kind'], 'document')
        self.assertEqual(db.list_documents('default'), [])
        # Even a summary intent must answer this attachment, not replace it
        # with the notebook-wide source-summary workflow.
        analysis = {'intent': 'summary', 'needs_retrieval': False, 'search_query': 'resonance'}
        answer = AsyncMock(return_value='Resonance answer.')
        with patch.object(main, 'analyze_question', AsyncMock(return_value=analysis)), patch.object(main, 'answer_question', answer):
            result = self.client.post('/api/ask', json={'question': 'Solve this', 'document_ids': [], 'image_ids': [attachment['id']]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('What is resonance?', answer.call_args.args[0])
        self.assertEqual(answer.call_args.args[1], [])
        self.assertEqual(result.json()['citations'], [])
        chat_id = result.json()['chat_id']
        saved = db.get_exchange('default', chat_id)
        self.assertEqual(saved['images'][0]['kind'], 'document')
        self.assertEqual(self.client.get(attachment['url']).content, b'What is resonance?')
        self.client.delete(f'/api/notebooks/default/chat/{chat_id}')
        self.assertEqual(self.client.get(attachment['url']).status_code, 200)
        self.client.post('/api/notebooks/default/chat/undo')
        self.assertEqual(db.get_exchange('default', chat_id)['images'][0]['id'], attachment['id'])
        self.assertFalse(self.client.delete('/api/question-images/' + attachment['id']).json()['deleted'])
        self.assertEqual(db.list_documents('default'), [])

    def test_image_pdf_page_is_recognized_without_becoming_source(self):
        from PIL import Image
        stream = io.BytesIO(); Image.new('RGB', (20, 20), 'white').save(stream, format='PNG')
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 40), 'Question title')
            page.insert_image(pymupdf.Rect(50, 70, 200, 200), stream=stream.getvalue())
            content = pdf.tobytes()
        # A text title must not cause the actual image problem to be skipped.
        with patch.object(main, 'local_ocr', return_value='Image problem with all conditions'):
            response = self.upload('图片题.pdf', content)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn('Image problem with all conditions', response.json()['image']['ocr_text'])
        self.assertEqual(db.list_documents('default'), [])

    def test_pdf_and_office_files_stay_outside_source_library(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(); page.insert_text((50, 70), 'PDF attachment question')
            content = pdf.tobytes()
        response = self.upload('题目.pdf', content)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn('PDF attachment question', response.json()['image']['ocr_text'])
        self.assertEqual(self.client.get(response.json()['image']['url']).headers['content-type'], 'application/pdf')
        from docx import Document
        word = Document(); word.add_paragraph('Word question'); stream = io.BytesIO(); word.save(stream)
        self.assertEqual(self.upload('题目.docx', stream.getvalue()).status_code, 201)
        from pptx import Presentation
        presentation = Presentation(); slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(0, 0, 1000000, 1000000).text = 'PPT question'
        stream = io.BytesIO(); presentation.save(stream)
        self.assertEqual(self.upload('题目.pptx', stream.getvalue()).status_code, 201)
        self.assertEqual(db.list_documents('default'), [])

    def test_attachment_scope_limits_and_cleanup(self):
        other = self.client.post('/api/notebooks', json={'name': '其他笔记本'}).json()['notebook']['id']
        attachment = self.upload('题目.txt', b'Question').json()['image']
        response = self.client.post('/api/ask', json={'notebook_id': other, 'question': 'Solve', 'image_ids': [attachment['id']]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.upload('太长.txt', ('字' * 20000).encode()).status_code, 400)
        self.assertEqual(self.upload('空.txt', b'').status_code, 400)
        self.assertEqual(self.upload('不支持.zip', b'zip').status_code, 400)
        self.assertEqual(len(db.notebook_images('default')), 1)
        self.client.delete('/api/question-images/' + attachment['id'])
        self.assertEqual(list(main.QUESTION_IMAGE_DIR.iterdir()), [])

    def test_document_bytes_never_sent_as_vision_image(self):
        with tempfile.TemporaryDirectory() as temp:
            document = Path(temp) / 'question.pdf'; document.write_bytes(b'PDF bytes')
            image = Path(temp) / 'question.png'; image.write_bytes(b'PNG bytes')
            completion = AsyncMock(return_value='Image question')
            with patch('app.llm.chat_completion', completion):
                text, mode = asyncio.run(read_question([
                    {'name': 'question.pdf', 'path': str(document), 'ocr_text': 'Document question'},
                    {'name': 'question.png', 'path': str(image), 'ocr_text': ''},
                ], {'vision_model': 'mock', 'base_url': 'https://mock.example/v1'}))
            self.assertEqual(mode, 'mixed')
            self.assertIn('Document question', text); self.assertIn('Image question', text)
            content = completion.call_args.kwargs['messages'][0]['content']
            self.assertEqual(len(content), 2)
            self.assertNotIn('UERFIGJ5dGVz', str(content))
