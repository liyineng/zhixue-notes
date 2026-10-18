import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main
from app.conversation import MAX_CONTEXT_CHARS, recent_context


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
        self.assertEqual(result.json()['context_turns'], 3)
        prompt, sources = answer.call_args.args[:2]
        self.assertEqual(sources, [])  # Even with uploaded sources and history, empty means empty.
        self.assertEqual(planner.call_args.args[0], prompt)
        self.assertNotIn('有效答案0', prompt)
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
        text, turns = recent_context(exchanges)
        self.assertEqual(turns, 3)
        self.assertLessEqual(len(text), MAX_CONTEXT_CHARS)
        self.assertEqual(text.count('最后结论'), 3)
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
