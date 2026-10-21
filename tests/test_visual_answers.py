import asyncio
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import test_workspace
from app import db, main
from app.llm import answer_question, uses_original_question_images, CompletionLimitError


class VisualAnswerPayloadTests(unittest.TestCase):
    def test_configured_same_vision_solver_receives_original_and_source_evidence_together(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'circuit.png'; image.write_bytes(b'original-circuit-with-tap')
            settings = {'base_url': 'https://provider.invalid/v1', 'model': 'vision-capable',
                        'vision_model': 'vision-capable', 'vision_base_url': 'https://provider.invalid/v1/chat/completions',
                        'api_key': 'chat-secret', 'vision_api_key': 'vision-secret'}
            model = AsyncMock(return_value='根据原图而非错误转写解答 [1]')
            with patch('app.llm.chat_completion', model):
                asyncio.run(answer_question('转写可能漏掉抽头', [{'index': 1, 'document_name': '课程资料', 'page': 11,
                    'text': '应核对三端电抗性质'}], settings, images=[{'path': str(image)}]))
            call = model.call_args.kwargs
            self.assertEqual(call['api_key'], 'chat-secret')
            content = call['messages'][1]['content']
            self.assertIn('应核对三端电抗性质', content[0]['text'])
            self.assertIn('识别文字只作辅助', content[0]['text'])
            self.assertEqual(base64.b64decode(content[1]['image_url']['url'].split(',')[1]), image.read_bytes())
            self.assertIn('抽头', call['messages'][0]['content'])
            self.assertIn('极间电容', call['messages'][0]['content'])

    def test_separate_provider_or_model_does_not_silently_send_picture_or_vision_key(self):
        for change in ({'vision_base_url': 'https://other.invalid/v1'}, {'vision_model': 'another-model'}, {'vision_model': ''}):
            settings = {'base_url': 'https://chat.invalid/v1', 'model': 'chat', 'vision_model': 'chat',
                        'api_key': 'chat-secret', 'vision_api_key': 'private-vision-secret', **change}
            self.assertFalse(uses_original_question_images(settings))
            completion = AsyncMock(return_value='连接不清，请校对')
            with patch('app.llm.chat_completion', completion):
                asyncio.run(answer_question('电路连接文字', [], settings, images=[{'path': '/not-read.png'}]))
            call = completion.call_args.kwargs
            self.assertIsInstance(call['messages'][1]['content'], str)
            self.assertIn('未收到原图', call['messages'][1]['content'])
            self.assertNotIn('private-vision-secret', str(call))


class VisualAnswerPipelineTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook

    def test_original_picture_survives_recognition_to_final_solver_and_saved_diagnostics(self):
        notebook = self.notebook('图片解题验收')
        stream = io.BytesIO(); Image.new('RGB', (160, 120), 'white').save(stream, format='PNG')
        with patch.object(main, 'local_ocr', return_value='题目'):
            upload = self.client.post('/api/question-images', params={'notebook_id': notebook},
                files={'file': ('circuit.png', stream.getvalue(), 'image/png')})
        self.assertEqual(upload.status_code, 201, upload.text)
        image_id = upload.json()['image']['id']
        db.update_settings({'base_url': 'https://model.invalid/v1', 'model': 'vision', 'vision_model': 'vision', 'context_window': 128000})
        plan = {'intent': 'question', 'needs_retrieval': True, 'search_query': '三端式相位判断', 'keywords': []}
        completion = AsyncMock(return_value='请先核对原图节点，不能按错误转写判定缺少支路。')
        with patch.object(main, 'analyze_question', AsyncMock(return_value=plan)), \
             patch.object(main, 'read_question', AsyncMock(return_value=('可能漏掉电感抽头的转写', 'vision'))), \
             patch('app.llm.chat_completion', completion):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [],
                'image_ids': [image_id], 'question': '解答题图'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['citations'], [])
        self.assertEqual(response.json()['image_answer_mode'], 'original')
        content = completion.call_args.kwargs['messages'][1]['content']
        self.assertEqual(content[1]['type'], 'image_url')
        self.assertIn('可能漏掉电感抽头的转写', content[0]['text'])
        saved = db.get_exchange(notebook, response.json()['chat_id'])
        self.assertEqual(saved['result']['image_answer_mode'], 'original')
        self.assertEqual(saved['images'][0]['id'], image_id)
        # Failed model output retains completed recognition and safe diagnostics.
        error = CompletionLimitError([32768, 65536], {'usage': {'completion_tokens': 65536}}, thinking=True)
        with patch.object(main, 'analyze_question', AsyncMock(return_value=plan)), \
             patch.object(main, 'read_question', AsyncMock(return_value=('已经读取的节点表', 'vision'))), \
             patch.object(main, 'answer_question', AsyncMock(side_effect=error)):
            failed = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [],
                'image_ids': [image_id], 'question': '重新核对题图'})
        self.assertEqual(failed.status_code, 502)
        identity = failed.headers['X-Chat-Id']
        result = db.get_exchange(notebook, identity)['result']
        self.assertEqual(result['image_question_text'], '已经读取的节点表')
        self.assertEqual(result['image_answer_mode'], 'original')
        self.assertEqual(result['model_response']['attempted_output_budgets'], [32768, 65536])
