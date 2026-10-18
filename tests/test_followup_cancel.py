import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main, generation


class FollowupCancelTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def parent(self, notebook, identity='parent', error=False):
        db.start_exchange(identity, notebook, '原问题：为什么需要序号？')
        db.finish_exchange(identity, {'answer': '原回答：使用序号去重 [7]。',
            'image_question_text': '原图片题干包含两个主机', 'citations': [{'index': 7}]}, error=error)
        return identity

    def test_quote_is_explicit_scoped_and_saved_as_snapshot_without_automatic_history(self):
        notebook = self.notebook('引用追问')
        parent = self.parent(notebook)
        db.start_exchange('unrelated', notebook, '无关旧问题')
        db.finish_exchange('unrelated', {'answer': '绝不能自动发给模型的无关历史'})
        questions = []
        async def answer(question, contexts, *args):
            questions.append(question)
            self.assertEqual(contexts, [])  # Empty selection cannot revive the old sources.
            return '进一步解释'
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True, 'search_query': '序号', 'keywords': []})
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', side_effect=answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [],
                'question': '详细解释这个结论', 'reply_to_id': parent})
            self.assertEqual(result.status_code, 200, result.text)
            plain = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [], 'question': '独立的新问题'})
            self.assertEqual(plain.status_code, 200, plain.text)
        self.assertIn('原回答：使用序号去重', questions[0])
        self.assertIn('【历史引用7】', questions[0])
        self.assertNotIn('[7]', questions[0])
        self.assertIn('原图片题干包含两个主机', questions[0])
        self.assertNotIn('绝不能自动发给模型', questions[0])
        self.assertEqual(questions[1], '独立的新问题')
        reply = db.get_exchange(notebook, result.json()['chat_id'])
        self.assertEqual(reply['reply_to']['id'], parent)
        self.client.delete(f'/api/notebooks/{notebook}/chat/{parent}')
        self.assertEqual(db.get_exchange(notebook, reply['id'])['reply_to']['answer'], '原回答：使用序号去重 [7]。')

    def test_quotes_from_other_notebooks_failed_or_deleted_answers_are_rejected(self):
        first = self.notebook('原笔记本'); second = self.notebook('另一本')
        parent = self.parent(first)
        for notebook, reference in [(second, parent), (first, self.parent(first, 'failed-parent', error=True))]:
            self.assertEqual(self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '解释', 'reply_to_id': reference}).status_code, 400)
        self.client.delete(f'/api/notebooks/{first}/chat/{parent}')
        self.assertEqual(self.client.post('/api/ask', json={'notebook_id': first, 'question': '解释', 'reply_to_id': parent}).status_code, 400)

    def test_cancel_stops_ask_and_summary_and_late_results_cannot_overwrite(self):
        notebook = self.notebook('取消生成')
        document = self.upload(notebook, '来源.md', '网络协议定义通信规则。')
        for endpoint, mocked in [('/api/ask', 'analyze_question'), ('/api/summary', 'make_summary')]:
            started = threading.Event(); stopped = threading.Event()
            async def slow(*args, **kwargs):
                started.set()
                try: await asyncio.sleep(20)
                except asyncio.CancelledError:
                    stopped.set(); raise
            identity = 'cancel-ask' if endpoint.endswith('ask') else 'cancel-summary'
            with patch.object(main, mocked, side_effect=slow), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.client.post, endpoint, json={'notebook_id': notebook,
                    'document_ids': [document], 'question': '网络协议是什么', 'request_id': identity})
                self.assertTrue(started.wait(3))
                other = self.notebook('不可跨本取消')
                self.assertEqual(self.client.post(f'/api/notebooks/{other}/chat/{identity}/cancel').status_code, 404)
                cancelled = self.client.post(f'/api/notebooks/{notebook}/chat/{identity}/cancel')
                self.assertTrue(cancelled.json()['cancelled'])
                response = future.result(timeout=3)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertTrue(stopped.wait(1))
            record = db.get_exchange(notebook, identity)
            self.assertEqual(record['status'], 'error')
            self.assertTrue(record['result']['cancelled'])
            self.assertFalse(db.finish_pending_exchange(identity, {'answer': '迟到的成功回答'}))
            self.assertTrue(db.get_exchange(notebook, identity)['result']['cancelled'])

    def test_cancel_before_request_arrives_prevents_model_call(self):
        notebook = self.notebook('抢先取消')
        self.assertTrue(self.client.post(f'/api/notebooks/{notebook}/chat/not-started/cancel').json()['cancelled'])
        planner = AsyncMock()
        with patch.object(main, 'analyze_question', planner):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '不要发送', 'request_id': 'not-started'})
        self.assertEqual(result.status_code, 409)
        planner.assert_not_called()
        self.assertEqual(db.list_exchanges(notebook), [])
