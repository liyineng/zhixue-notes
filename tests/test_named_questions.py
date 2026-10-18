import unittest
from unittest.mock import AsyncMock, patch

import pymupdf
import test_workspace
from app import db, main
from app.retrieval import named_question_documents, exercise_sources


class NamedQuestionTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def course(self, notebook):
        with pymupdf.open() as pdf:
            for number in range(1, 51):
                page = pdf.new_page()
                page.insert_text((30, 40), f'Course page {number}. Native network concepts and introductory explanation.')
            response = self.client.post('/api/documents', params={'notebook_id': notebook},
                files={'file': ('L04-直连网络.pdf', pdf.tobytes(), 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        identity = response.json()['document']['id']
        for page, text in [(28, '〔题目〕CRC 接收序列110111001，除数11001，判断差错。'),
                           (50, '〔题目〕月球链路1Mbps，单程1.25s，每帧1KB，求序号位数。')]:
            db.append_image_chunk(identity, f'image-{identity}-{page}', page,
                [{'page': page, 'text': text, 'bbox': [20, 60, 400, 140], 'image_recognition': 'vision'}])
        main._invalidate_index()
        return identity

    def test_named_course_exercises_include_image_questions_without_keyword_or_top_k_loss(self):
        notebook = self.notebook('按课件做题')
        wanted = self.course(notebook)
        other = self.upload(notebook, 'L05-其他课程.md', '〔题目〕这不是所指定的课件。')
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': False,
                                         'search_query': 'L04 两道题', 'keywords': []})
        for question, pages in [('L04里面有两道题帮我做一下', [28, 50]), ('帮我解答L04第28页的题', [28])]:
            async def answer(question, contexts, settings, names, needs_retrieval):
                self.assertEqual({c['document_id'] for c in contexts}, {wanted})
                self.assertEqual([c['page'] for c in contexts], pages)
                self.assertTrue(all('〔题目〕' in c['text'] for c in contexts))
                self.assertTrue(needs_retrieval)
                self.assertEqual(names, ['L04-直连网络.pdf'])
                return '题目已读取。' + ''.join(f"[{c['index']}]" for c in contexts)
            with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', side_effect=answer):
                response = self.client.post('/api/ask', json={'notebook_id': notebook,
                    'document_ids': [wanted, other], 'question': question, 'top_k': 1})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([c['page'] for c in response.json()['citations']], pages)

    def test_named_question_cannot_read_unselected_course_or_override_empty_selection(self):
        notebook = self.notebook('勾选范围')
        self.upload(notebook, 'L04-直连网络.md', '〔题目〕只有这份资料有的题目。')
        other = self.upload(notebook, 'L05-其他.md', '其他资料。')
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True,
                                         'search_query': 'L04 题目', 'keywords': []})
        answer = AsyncMock(return_value='当前没有勾选可用的资料。')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            excluded = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [other], 'question': '做L04的题'})
            self.assertEqual(excluded.status_code, 400)
            answer.assert_not_called()
            empty = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [], 'question': '做L04的题'})
            self.assertEqual(empty.status_code, 200)
            self.assertEqual(answer.call_args.args[1], [])

    def test_host_identifiers_and_concept_questions_do_not_trigger_exercise_scanning(self):
        documents = [{'id': 'course', 'name': 'L04-直连网络.pdf'}]
        self.assertIsNone(named_question_documents('H1经过B1的过程怎么解题', documents))
        self.assertEqual(named_question_documents('L4中的问题', documents), documents)
        self.assertEqual(exercise_sources('L04的CRC原理是什么', [{'page': 28, 'text': '〔题目〕CRC'}]), [])
        contexts = [{'page': 26, 'text': 'CRC 运算（例1）'}, {'page': 28, 'text': '〔题目〕接收方判断差错'}]
        self.assertEqual(exercise_sources('解答L04中的题', contexts), contexts[1:])
