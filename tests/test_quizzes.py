import asyncio
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main
from app.quizzes import build_quiz, remap_references, validate_questions


def question(source=1, prompt='谐振时哪个说法正确？'):
    return {'prompt': prompt, 'options': ['电抗抵消', '电感为零', '电容为零', '电阻为零'],
            'answer': 0, 'explanation': '谐振时感抗与容抗抵消。', 'sources': [source]}


class QuizTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def prepare(self):
        notebook = self.notebook('电路测验')
        wanted = self.upload(notebook, 'L02-谐振.md', r'谐振时感抗与容抗抵消，\(\omega_0=1/\sqrt{LC}\)。')
        self.upload(notebook, 'L03-其他.md', '不能进入此次出题范围')
        db.update_settings({'base_url': 'https://example.invalid', 'model': 'test'})
        return notebook, wanted

    def create(self, notebook, document):
        completion = AsyncMock(return_value=json.dumps({'questions': [question()]}, ensure_ascii=False))
        with patch('app.quizzes.chat_completion', completion):
            response = self.client.post('/api/quiz', json={'notebook_id': notebook, 'document_ids': [document],
                'count': 'less', 'difficulty': 'hard', 'language': 'en', 'topic': '第二章'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('不能进入此次出题范围', str(completion.call_args))
        self.assertIn('English', completion.call_args.kwargs['messages'][1]['content'])
        return response.json()

    def test_create_grade_refresh_reset_delete_and_undo(self):
        notebook, document = self.prepare()
        result = self.create(notebook, document)
        self.assertEqual(result['quiz']['answers'], {})
        self.assertEqual(result['quiz']['requested_count'], 5)
        self.assertEqual(len(result['quiz']['questions']), 1)
        self.assertTrue(result['warnings'])
        self.assertEqual({c['document_id'] for c in result['citations']}, {document})
        endpoint = f"/api/notebooks/{notebook}/quiz/{result['chat_id']}"
        response = self.client.post(endpoint + '/answer', json={'question_id': 'q1', 'option': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['quiz']['answers'], {'q1': 2})
        # An answered question cannot be silently corrected in another request.
        self.assertEqual(self.client.post(endpoint + '/answer', json={'question_id': 'q1', 'option': 0}).json()['quiz']['answers'], {'q1': 2})
        db.init_db()
        history = self.client.get(f'/api/notebooks/{notebook}/chat').json()['exchanges']
        self.assertEqual(history[-1]['result']['quiz']['answers'], {'q1': 2})
        reset = self.client.post(endpoint + '/reset').json()['quiz']
        self.assertEqual(reset['answers'], {})
        self.assertEqual(reset['attempt'], 2)
        self.client.delete(f"/api/notebooks/{notebook}/chat/{result['chat_id']}")
        self.assertEqual(self.client.post(endpoint + '/answer', json={'question_id': 'q1', 'option': 1}).status_code, 404)
        undo = self.client.post(f'/api/notebooks/{notebook}/chat/undo')
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertEqual(self.client.post(endpoint + '/answer', json={'question_id': 'q1', 'option': 1}).status_code, 200)

    def test_empty_foreign_and_unresolved_scope_never_calls_model(self):
        notebook, document = self.prepare()
        other = self.notebook('其他课')
        foreign = self.upload(other, 'L04.md', '其他笔记本的私有资料')
        with patch('app.quizzes.chat_completion', AsyncMock()) as model:
            for ids, topic in [([], ''), ([foreign], ''), ([document], '第九章')]:
                response = self.client.post('/api/quiz', json={'notebook_id': notebook, 'document_ids': ids, 'topic': topic})
                self.assertGreaterEqual(response.status_code, 400)
            model.assert_not_awaited()

    def test_foreign_attempts_and_invalid_answers_are_rejected(self):
        notebook, document = self.prepare()
        result = self.create(notebook, document)
        other = self.notebook('无权限范围')
        for suffix, body in [('answer', {'question_id': 'q1', 'option': 0}), ('reset', {})]:
            response = self.client.post(f"/api/notebooks/{other}/quiz/{result['chat_id']}/{suffix}", json=body)
            self.assertEqual(response.status_code, 404)
        endpoint = f"/api/notebooks/{notebook}/quiz/{result['chat_id']}/answer"
        for body in [{'question_id': 'missing', 'option': 0}, {'question_id': 'q1', 'option': 4}, {'question_id': 'q1', 'option': True}]:
            self.assertGreaterEqual(self.client.post(endpoint, json=body).status_code, 400)
        self.assertEqual(db.get_exchange(notebook, result['chat_id'])['result']['quiz']['answers'], {})

    def test_dedicated_page_and_read_api_are_notebook_scoped_and_reject_deleted_quizzes(self):
        notebook, document = self.prepare()
        result = self.create(notebook, document)
        identity = result['chat_id']
        other = self.notebook('其他笔记本')
        response = self.client.get(f'/quiz/{identity}', params={'notebook_id': notebook})
        self.assertEqual(response.status_code, 200)
        self.assertIn('quiz-page.js', response.text)
        read = self.client.get(f'/api/notebooks/{notebook}/quiz/{identity}')
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()['quiz'], result['quiz'])
        self.assertEqual(self.client.get(f'/api/notebooks/{other}/quiz/{identity}').status_code, 404)
        self.assertEqual(self.client.get(f'/quiz/{identity}', params={'notebook_id': other}).status_code, 404)
        self.client.delete(f'/api/notebooks/{notebook}/chat/{identity}')
        self.assertEqual(self.client.get(f'/api/notebooks/{notebook}/quiz/{identity}').status_code, 404)
        self.assertEqual(self.client.get(f'/quiz/{identity}', params={'notebook_id': notebook}).status_code, 404)

    def test_clearing_conversations_preserves_application_quizzes_and_saved_answers(self):
        notebook, document = self.prepare()
        result = self.create(notebook, document)
        identity = result['chat_id']
        self.client.post(f'/api/notebooks/{notebook}/quiz/{identity}/answer', json={'question_id': 'q1', 'option': 2})
        db.start_exchange('ordinary', notebook, '普通问题')
        db.finish_exchange('ordinary', {'answer': '普通回答'})
        response = self.client.delete(f'/api/notebooks/{notebook}/chat')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.get_exchange(notebook, 'ordinary'))
        saved = self.client.get(f'/api/notebooks/{notebook}/quiz/{identity}')
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['quiz']['answers'], {'q1': 2})

    def test_malformed_answer_keys_or_fabricated_sources_fail_without_completed_quiz(self):
        notebook, document = self.prepare()
        for change in [{'sources': [999]}, {'answer': True}, {'answer': 4}, {'options': ['A'] * 4}, {'explanation': ''}]:
            item = {**question(), **change}
            with patch('app.quizzes.chat_completion', AsyncMock(return_value=json.dumps({'questions': [item]}))):
                response = self.client.post('/api/quiz', json={'notebook_id': notebook, 'document_ids': [document]})
            self.assertEqual(response.status_code, 502, response.text)
        self.assertTrue(all(e['status'] == 'error' for e in db.list_exchanges(notebook)))

    def test_cancel_quiz_stops_job_and_preserves_cancelled_record(self):
        notebook, document = self.prepare()
        entered, stopped = threading.Event(), threading.Event()
        async def wait(*args, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        identity = 'cancel-quiz'
        with patch.object(main, 'build_quiz', side_effect=wait), ThreadPoolExecutor() as pool:
            pending = pool.submit(self.client.post, '/api/quiz', json={
                'notebook_id': notebook, 'document_ids': [document], 'request_id': identity})
            self.assertTrue(entered.wait(3))
            response = self.client.post(f'/api/notebooks/{notebook}/chat/{identity}/cancel')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(pending.result(timeout=3).status_code, 409)
            self.assertTrue(stopped.wait(3))
        self.assertTrue(db.get_exchange(notebook, identity)['result']['cancelled'])

    def test_batch_validation_and_global_renumbering_preserve_math_indices(self):
        contexts = [{'index': 7, 'document_id': 'a', 'document_name': 'A', 'text': '资料A', 'page': 1},
                    {'index': 22, 'document_id': 'b', 'document_name': 'B', 'text': '资料B', 'page': 2}]
        replies = [{'questions': [question(7)]}, {'questions': [question(22, '资料B的正确结论？')]}]
        replies[1]['questions'][0]['explanation'] = r'\(x[22]=1\) 依据 [22]'
        with patch('app.quizzes.chat_completion', AsyncMock(side_effect=[json.dumps(r) for r in replies])):
            result = asyncio.run(build_quiz(contexts, {'base_url': 'mock', 'model': 'mock'}, count='less',
                difficulty='medium', language='zh-CN', topic='', on_batch=AsyncMock()))
        self.assertEqual([q['sources'] for q in result['questions']], [[1], [2]])
        self.assertEqual(result['questions'][1]['explanation'], r'\(x[22]=1\) 依据 [2]')
        self.assertEqual(remap_references('`x[22]` [22]', {22: 2}), '`x[22]` [2]')
        with self.assertRaises(ValueError):
            validate_questions({'questions': [question(22)]}, contexts[:1], 1)

    def test_inline_batch_citations_missing_from_sources_are_merged_and_renumbered(self):
        contexts = [{'index': n, 'document_id': 'selected', 'document_name': '课件',
                     'page': n, 'text': '资料依据'} for n in (7, 22, 31)]
        item = question(7)
        item['explanation'] = r'依据【22】和 [7]，公式 \(x[99]=1\)，代码 `a[88]`。'
        item['options'][1] += ' [31]'
        completion = AsyncMock(return_value=json.dumps({'questions': [item]}))
        with patch('app.quizzes.chat_completion', completion):
            result = asyncio.run(build_quiz(contexts, {'base_url': 'mock', 'model': 'mock'}, count='less',
                difficulty='medium', language='zh-CN', topic='', on_batch=AsyncMock()))
        self.assertEqual(completion.await_count, 1)
        saved = result['questions'][0]
        self.assertEqual(saved['sources'], [1, 2, 3])
        self.assertEqual(saved['explanation'], r'依据[2]和 [1]，公式 \(x[99]=1\)，代码 `a[88]`。')
        self.assertEqual(saved['options'][1], '电感为零 [3]')
        self.assertEqual([c['page'] for c in result['citations']], [7, 22, 31])
        self.assertEqual(item['sources'], [7], 'validation must not mutate model input')

    def test_inline_foreign_citations_and_excess_sources_still_fail(self):
        for field in ('prompt', 'explanation', 'option'):
            item = question()
            if field == 'option':
                item['options'][1] += ' [999]'
            else:
                item[field] += ' [999]'
            with self.assertRaisesRegex(RuntimeError, '不存在'):
                validate_questions({'questions': [item]}, [{'index': 1}], 1)
        item = question()
        item['explanation'] = ' '.join(f'[{n}]' for n in range(1, 8))
        with self.assertRaisesRegex(ValueError, '最多引用六个'):
            validate_questions({'questions': [item]}, [{'index': n} for n in range(1, 8)], 1)

    def test_unescaped_math_json_retries_same_batch_with_structured_mode(self):
        contexts = [{'index': 1, 'document_id': 'selected', 'document_name': '第二章', 'page': 1, 'text': '谐振公式'}]
        invalid = r'{"questions":[{"prompt":"计算 \(Q\)","options":["a","b","c","d"],"answer":0,"explanation":"说明","sources":[1]}]}'
        valid = question()
        valid['prompt'] = r'计算 \(Q\)'
        valid['explanation'] = r'\(Q=\frac{\omega_0 L}{R}\) [1]'
        completion = AsyncMock(side_effect=[invalid, json.dumps({'questions': [valid]})])
        with patch('app.quizzes.chat_completion', completion):
            result = asyncio.run(build_quiz(contexts, {'base_url': 'mock', 'model': 'mock'}, count='less',
                difficulty='medium', language='zh-CN', topic='', on_batch=AsyncMock()))
        self.assertEqual(completion.await_count, 2)
        self.assertTrue(all(c.kwargs['json_mode'] for c in completion.call_args_list))
        self.assertEqual(result['questions'][0]['explanation'], valid['explanation'])
        self.assertEqual(result['questions'][0]['sources'], [1])

    def test_valid_json_with_corrupted_latex_control_characters_is_rejected(self):
        item = question()
        item['explanation'] = '\\(\x0crac{1}{2}\\)'
        with self.assertRaisesRegex(ValueError, '转义错误'):
            validate_questions({'questions': [item]}, [{'index': 1}], 1)
