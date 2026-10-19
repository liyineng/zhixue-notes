import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main
from app.conversation import recent_context, referenced_sources
from app.model_context import estimate_tokens
import pymupdf


class ConversationTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_recent_context_survives_reload_and_excludes_invalid_or_foreign_turns(self):
        notebook = self.notebook('上下文')
        other = self.notebook('隔离')
        document = self.upload(notebook, '来源.md', '网络协议规定通信规则。')
        for i in range(4):
            db.start_exchange(f'valid-{i}', notebook, f'问题{i}')
            db.finish_exchange(f'valid-{i}', {'answer': f'有效答案{i} [7]', 'image_question_text': '图片中的题干'})
        for identity, target, status in [('foreign', other, 'complete'), ('failed', notebook, 'error'),
                                         ('pending', notebook, 'pending'), ('deleted', notebook, 'complete'),
                                         ('cancelled', notebook, 'complete')]:
            db.start_exchange(identity, target, identity)
            if status != 'pending':
                db.finish_exchange(identity, {'answer': f'禁止出现{identity}', 'cancelled': identity == 'cancelled'}, error=status == 'error')
        self.client.delete(f'/api/notebooks/{notebook}/chat/deleted')
        # Read from persisted rows, independent of browser/client state.
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True, 'search_query': '协议', 'keywords': []})
        answer = AsyncMock(return_value='继续解释')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '再解释一下它', 'document_ids': []})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['context_turns'], 4)
        prompt, sources = answer.call_args.args[:2]
        self.assertEqual(sources, [])  # Even with uploaded sources and history, empty means empty.
        self.assertEqual(planner.call_args.args[0], prompt)
        self.assertIn('有效答案0', prompt)
        for i in range(1, 4):
            self.assertIn(f'有效答案{i}', prompt)
        self.assertLess(prompt.index('有效答案1'), prompt.index('有效答案3'))
        self.assertNotIn('禁止出现', prompt)
        self.assertNotIn('[7]', prompt)
        self.assertIn('【历史引用7】', prompt)
        self.assertIn('图片中的题干', prompt)
        self.assertIn('本次问题：再解释一下它', prompt)
        self.assertEqual(db.get_exchange(notebook, result.json()['chat_id'])['question'], '再解释一下它')
        self.assertIsNotNone(document)

    def test_long_history_is_bounded_and_keeps_recent_answer_ending(self):
        exchanges = [{'question': '题' * 4000, 'result': {'answer': '答' * 20000 + '最后结论',
                      'image_question_text': '图' * 20000}} for _ in range(4)]
        text, turns = recent_context(exchanges, token_budget=8000)
        self.assertEqual(turns, 1)
        self.assertLessEqual(estimate_tokens(text), 8000)
        self.assertEqual(text.count('最后结论'), 1)
        self.assertIn('上下文已截短', text)

    def test_first_question_has_no_history_wrapper(self):
        notebook = self.notebook('首次')
        planner = AsyncMock(return_value={'intent': 'chat', 'needs_retrieval': False, 'search_query': '', 'keywords': []})
        answer = AsyncMock(return_value='你好')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '你好'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(answer.call_args.args[0], '你好')
        self.assertEqual(response.json()['context_turns'], 0)

    def test_citation_numbers_load_real_pages_even_if_planner_confuses_them(self):
        notebook = self.notebook('引用中的习题')
        with pymupdf.open() as pdf:
            for number in range(1, 51):
                page = pdf.new_page()
                text = 'CRC received 110111001 divisor 11001 remainder 10.' if number == 28 else 'Moon link 1Mbps delay 1.25s frame 1KB.' if number == 50 else f'Other page {number}.'
                page.insert_text((30, 40), text)
            content = pdf.tobytes()
        response = self.client.post('/api/documents', params={'notebook_id': notebook},
            files={'file': ('L04.pdf', content, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()['document']['id']
        sources = [{**c, 'index': 22 if c['page'] == 28 else 42}
                   for c in main._summary_contexts([document]) if c['page'] in (28, 50)]
        db.start_exchange('reference-parent', notebook, '整理 L04')
        db.finish_exchange('reference-parent', {'answer': 'CRC[22]，月球链路[42]', 'citations': sources})
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': False,
                                         'search_query': '第22页 第42页', 'keywords': []})
        async def answer(question, contexts, *args):
            self.assertIn('历史引用 22 → 《L04.pdf》第 28 页', question)
            self.assertEqual([c['page'] for c in contexts], [28, 50])
            self.assertIn('110111001', contexts[0]['text'])
            self.assertIn('1.25s', contexts[1]['text'])
            self.assertEqual([c['index'] for c in contexts], [1, 2])
            return 'CRC 有差错 [1]，月球链路需计算窗口 [2]。'
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', side_effect=answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [document],
                'question': '这里22和42对应的题帮我做一下', 'top_k': 1})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([c['page'] for c in result.json()['citations']], [28, 50])
        # Explicit quoting uses the selected answer's numbering even after a newer answer exists.
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', side_effect=answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [document],
                'question': '引用22和42的题', 'reply_to_id': 'reference-parent'})
        self.assertEqual(result.status_code, 200, result.text)

    def test_reference_resolution_respects_selection_nearest_turn_and_page_wording(self):
        older = {'result': {'citations': [{'index': 22, 'document_id': 'allowed', 'page': 28}]}}
        latest = {'result': {'citations': [{'index': 22, 'document_id': 'excluded', 'page': 5}]}}
        self.assertEqual(referenced_sources('引用22', [older, latest], {'allowed'}), [])
        self.assertEqual(referenced_sources('这里22', [older], set()), [])
        self.assertEqual(referenced_sources('第22页的题', [older], {'allowed'}), [])
        self.assertEqual(referenced_sources('引用22的题', [older], {'allowed'})[0]['page'], 28)
