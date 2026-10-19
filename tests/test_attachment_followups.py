"""Exercise follow-ups must reload actual question conditions, not old solutions."""
import unittest
from unittest.mock import AsyncMock, patch
import test_workspace
from app import db, main
from app.attachment_followups import problem_blocks, recover_attachment_problem


class AttachmentFollowupTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook

    def seed(self, notebook):
        text = '第 1 页：\n' + '前面其他周的题目。' * 600 + '\n第 9 页：\n[W6-03] 画出三次握手。\n[W6-04] TCP 发送 3000B，RTT=10ms，MSL=30s，MSS=1000B。H 首先断开，求直到 CLOSED 的时间。\n[W6-05] Time 服务器查询 RTT=8ms。'
        response = self.client.post('/api/question-attachments', params={'notebook_id': notebook}, files={'file': ('作业.md', text.encode())})
        self.assertEqual(response.status_code, 201, response.text)
        attachment = response.json()['image']
        db.start_exchange('homework', notebook, '做第六周作业', [attachment])
        db.finish_exchange('homework', {'answer': '旧答案可能错误：网页下载 2RTT+60秒。', 'image_question_text': text, 'image_recognition_mode': 'document'})
        return attachment

    def test_followup_uses_full_actual_problem_and_retains_attachment(self):
        notebook = self.notebook('原题重读')
        attachment = self.seed(notebook)
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': False, 'search_query': 'W6-04', 'keywords': []})
        answer = AsyncMock(return_value='按原题核对 CLOSED 状态。')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': 'w6-04能再详细点吗', 'document_ids': []})
        self.assertEqual(result.status_code, 200, result.text)
        prompt = answer.call_args.args[0]
        for condition in ['3000B', '10ms', '30s', '1000B', 'CLOSED', '原始题干', '历史回答可能解错题']:
            self.assertIn(condition, prompt)
        self.assertNotIn('Time 服务器查询', prompt)
        self.assertEqual(answer.call_args.args[1], [])
        self.assertEqual(result.json()['image_recognition_mode'], 'attachment_followup')
        self.assertEqual(result.json()['question_attachment_refs'][0]['page'], 9)
        self.assertEqual(result.json()['question_attachment_refs'][0]['exercise_id'], 'W6-04')
        saved = db.get_exchange(notebook, result.json()['chat_id'])
        self.assertEqual(saved['images'][0]['id'], attachment['id'])
        self.client.delete(f'/api/notebooks/{notebook}/chat/homework')
        recent = db.recent_completed_exchanges(notebook, 3)
        recovered = recover_attachment_problem('W6-04', recent, notebook)
        self.assertIn('3000B', recovered['text'])
        self.assertEqual(db.list_documents(notebook), [])

    def test_unknown_exercise_fails_closed_without_a_model_call(self):
        notebook = self.notebook('缺失题号')
        self.seed(notebook)
        answer = AsyncMock()
        with patch.object(main, 'answer_question', answer):
            result = self.client.post('/api/ask', json={'notebook_id': notebook, 'question': '详细解答W6-99', 'document_ids': []})
        self.assertEqual(result.status_code, 400)
        self.assertIn('未找到完整', result.json()['detail'])
        answer.assert_not_called()

    def test_foreign_deleted_and_out_of_window_attachments_are_not_reused(self):
        notebook = self.notebook('当前课程')
        other = self.notebook('其他课程')
        attachment = self.seed(other)
        self.assertIsNone(recover_attachment_problem('W6-04', db.recent_completed_exchanges(notebook, 3), notebook))
        forged = [{'images': [attachment], 'result': {}}]
        self.assertIsNone(recover_attachment_problem('W6-04', forged, notebook))
        self.client.delete(f'/api/notebooks/{other}/chat/homework')
        self.assertIsNone(recover_attachment_problem('W6-04', db.recent_completed_exchanges(other, 3), other))
        self.client.post(f'/api/notebooks/{other}/chat/undo')
        for i in range(3):
            db.start_exchange(f'later-{i}', other, '不相关的新问题')
            db.finish_exchange(f'later-{i}', {'answer': '新回答'})
        self.assertIsNone(recover_attachment_problem('W6-04', db.recent_completed_exchanges(other, 3), other))

    def test_problem_boundaries_and_manual_corrections(self):
        blocks = problem_blocks('第 9 页：\n【W6－04】真实条件\n[W6-05] 别的题\n第 10 页：\n[W7-01] 后一周', ['W6-04'])
        self.assertEqual(blocks['W6-04']['page'], 9)
        self.assertNotIn('别的题', blocks['W6-04']['text'])
        notebook = self.notebook('校对题干')
        self.seed(notebook)
        db.finish_exchange('homework', {'answer': '旧答案', 'image_recognition_mode': 'manual', 'image_question_text': '[W6-04] 校对后 RTT=20ms。\n[W6-05] 其他题'})
        result = recover_attachment_problem('W6-04', db.recent_completed_exchanges(notebook, 3), notebook)
        self.assertIn('20ms', result['text'])
        self.assertNotIn('10ms', result['text'])
