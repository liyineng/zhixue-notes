import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_workspace
from app import db, main, generation


class GenerationProgressTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_ask_reports_actual_stages_and_status_is_notebook_scoped(self):
        notebook = self.notebook('进度')
        other = self.notebook('隔离')
        document = self.upload(notebook, '协议.md', '网络协议规定通信规则。')
        observations = []
        ready = threading.Event()
        release = threading.Event()
        def observe():
            observations.append(db.get_exchange(notebook, 'progress-job')['result']['progress'])
        async def plan(*args):
            observe()
            return {'intent': 'question', 'needs_retrieval': True, 'search_query': '网络协议', 'keywords': []}
        async def evidence(question, contexts, *args):
            observe()
            return contexts[:1]
        async def answer(*args):
            observe()
            ready.set()
            # The read-only status endpoint must work while the model is awaited.
            import asyncio
            while not release.is_set():
                await asyncio.sleep(.01)
            return '协议规定通信规则 [1]。'
        with patch.object(main, 'analyze_question', side_effect=plan), patch.object(main, 'select_evidence', side_effect=evidence), \
             patch.object(main, 'answer_question', side_effect=answer), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, '/api/ask', json={'notebook_id': notebook,
                'document_ids': [document], 'question': '什么是网络协议', 'request_id': 'progress-job'})
            try:
                self.assertTrue(ready.wait(3))
                status = self.client.get(f'/api/notebooks/{notebook}/chat/progress-job/status')
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()['status'], 'pending')
                self.assertEqual(status.json()['progress']['steps'][-1]['stage'], 'answer')
                self.assertEqual(self.client.get(f'/api/notebooks/{other}/chat/progress-job/status').status_code, 404)
                history = self.client.get(f'/api/notebooks/{notebook}/chat').json()['exchanges'][-1]
                self.assertEqual(history['result']['progress'], status.json()['progress'])
            finally:
                release.set()
            self.assertEqual(future.result(3).status_code, 200)
        self.assertEqual([p['steps'][-1]['stage'] for p in observations], ['analysis', 'evidence', 'answer'])
        self.assertEqual([s['stage'] for s in observations[-1]['steps']],
                         ['question', 'analysis', 'sources', 'retrieval', 'evidence', 'answer'])
        db.set_generation_progress('progress-job', 'late', '迟到进度', '不可覆盖完成回答')
        record = db.get_exchange(notebook, 'progress-job')
        self.assertNotIn('progress', record['result'])
        self.assertIn('协议', record['result']['answer'])

    def test_cancelled_progress_cannot_be_revived_and_plain_chat_skips_retrieval(self):
        notebook = self.notebook('取消进度')
        db.start_exchange('cancel-progress', notebook, '取消')
        db.set_generation_progress('cancel-progress', 'analysis', '分析问题', '处理中')
        generation.cancel(notebook, 'cancel-progress')
        db.set_generation_progress('cancel-progress', 'answer', '生成回答', '迟到')
        result = db.get_exchange(notebook, 'cancel-progress')
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['result']['cancelled'])
        self.assertNotIn('progress', result['result'])
        async def plan(*args):
            return {'intent': 'chat', 'needs_retrieval': False, 'search_query': '', 'keywords': []}
        async def answer(*args):
            stages = db.get_exchange(notebook, 'plain-progress')['result']['progress']['steps']
            self.assertEqual([s['stage'] for s in stages], ['question', 'analysis', 'answer'])
            return '你好'
        with patch.object(main, 'analyze_question', side_effect=plan), patch.object(main, 'answer_question', side_effect=answer):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '你好', 'request_id': 'plain-progress'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_summary_progress_tracks_reading_and_generation(self):
        notebook = self.notebook('提纲进度')
        document = self.upload(notebook, '资料.md', '网络协议规定通信规则。')
        async def summary(*args, **kwargs):
            progress = db.get_exchange(notebook, 'summary-progress')['result']['progress']
            self.assertEqual([s['stage'] for s in progress['steps']], ['sources', 'retrieval', 'answer'])
            self.assertIn('片段', progress['detail'])
            return '复习提纲'
        with patch.object(main, 'make_summary', side_effect=summary):
            response = self.client.post('/api/summary', json={'notebook_id': notebook, 'document_ids': [document], 'request_id': 'summary-progress'})
        self.assertEqual(response.status_code, 200, response.text)
