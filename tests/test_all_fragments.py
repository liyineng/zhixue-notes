import asyncio
import json
import re
import unittest
from unittest.mock import AsyncMock, patch
import pymupdf
import test_workspace
from app import db, main, llm
from app.retrieval import BM25Index


class AllFragmentTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_default_six_and_optional_all_preserve_scope(self):
        notebook = self.notebook('全部片段')
        other = self.notebook('其他课程')
        self.upload(other, 'private.md', 'TCP foreign source')
        with pymupdf.open() as pdf:
            for n in range(30):
                pdf.new_page().insert_text((20, 40), f'TCP course rule on page {n + 1}.')
            raw = pdf.tobytes()
        result = self.client.post('/api/documents', params={'notebook_id': notebook},
            files={'file': ('course.pdf', raw, 'application/pdf')})
        document = result.json()['document']['id']
        self.assertEqual(self.client.get('/api/settings').json()['top_k'], 6)
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True,
                                         'search_query': 'TCP', 'keywords': []})
        answer = AsyncMock(return_value='课程规则 [30]。')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            answer.return_value = '课程规则 [6]。'
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'TCP规则是什么', 'document_ids': [document]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(answer.call_args.args[1]), 6)
            self.client.put('/api/settings', json={'top_k': 0})
            answer.return_value = '课程规则 [30]。'
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'TCP规则是什么', 'document_ids': [document]})
            self.assertEqual(response.status_code, 200, response.text)
            contexts = answer.call_args.args[1]
            self.assertEqual(len(contexts), 30)
            self.assertEqual({c['document_id'] for c in contexts}, {document})
            self.assertEqual({c['page'] for c in contexts}, set(range(1, 31)))
            self.assertEqual(response.json()['citations'][0]['page'], 30)
            answer.return_value = '未选择资料。'
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'TCP规则是什么', 'document_ids': []})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(answer.call_args.args[1], [])
            # Explicit zero overrides a previously saved numeric limit.
            self.client.put('/api/settings', json={'top_k': 1})
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'TCP规则是什么', 'document_ids': [document], 'top_k': 0})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(answer.call_args.args[1]), 30)

    def test_all_evidence_trims_history_before_rejecting_model_capacity(self):
        notebook = self.notebook('上下文预算')
        document = self.upload(notebook, 'TCP.md', 'TCP course rule.')
        self.client.put('/api/settings', json={'model': 'mock', 'base_url': 'https://example.invalid',
                                              'context_window': 22000, 'top_k': 0})
        db.start_exchange('prior', notebook, '旧问题')
        db.finish_exchange('prior', {'answer': 'A' * 10000})
        contexts = [{'index': i, 'document_id': document, 'document_name': 'TCP.md',
                     'page': i, 'text': 'TCP ' * 125, 'lines': []} for i in range(1, 11)]
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True,
                                         'search_query': 'TCP', 'keywords': []})
        answer = AsyncMock(return_value='规则 [10]。')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'select_evidence', AsyncMock(return_value=contexts)), patch.object(main, 'answer_question', answer):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'TCP规则', 'document_ids': [document]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(answer.call_args.args[1]), 10)
        self.assertEqual(response.json()['context_turns'], 1)
        self.assertLess(response.json()['history_tokens_estimated'], 10000)
        self.assertIn('上下文已截短', answer.call_args.args[0])

    def test_search_all_has_no_twenty_hit_cap_and_keeps_empty_filter(self):
        index = BM25Index([{'id': str(i), 'document_id': 'chosen' if i < 30 else 'foreign',
                           'text': f'TCP rule {i}', 'ordinal': i} for i in range(35)])
        self.assertEqual(len(index.search('TCP', top_k=0, document_ids={'chosen'})), 30)
        self.assertEqual(len(index.search('TCP', top_k=6, document_ids={'chosen'})), 6)
        self.assertEqual(index.search('TCP', top_k=0, document_ids=set()), [])

    def test_all_evidence_checks_each_batch_and_renumbers_globally(self):
        candidates = [{'index': i, 'document_id': 'chosen', 'document_name': '课程.pdf',
                       'page': i, 'text': '记录源地址和进入端口', 'lines': []} for i in range(1, 26)]
        async def reply(**kwargs):
            numbers = re.findall(r'\[(\d+)\] 来源：', kwargs['messages'][1]['content'])
            self.assertLessEqual(len(numbers), 8)
            return json.dumps({'evidence': [{'source': int(n), 'quote': '记录源地址和进入端口'} for n in numbers]})
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=reply)) as model:
            selected = asyncio.run(llm.select_evidence('网桥自学习', candidates,
                {'model': 'mock', 'base_url': 'https://example.invalid', 'context_window': 128000}, 0))
        self.assertEqual(model.await_count, 4)
        self.assertEqual([c['index'] for c in selected], list(range(1, 26)))
        self.assertEqual([c['page'] for c in selected], list(range(1, 26)))
        with patch.object(llm, 'chat_completion', AsyncMock()) as model:
            with self.assertRaisesRegex(RuntimeError, '上下文容量'):
                asyncio.run(llm.select_evidence('问题', candidates,
                    {'model': 'mock', 'base_url': 'https://example.invalid', 'context_window': 100}, 0))
            model.assert_not_awaited()
