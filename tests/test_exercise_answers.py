"""Whole-attachment exercise answers are scoped, complete and resumable; no model calls."""
import asyncio
import re
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, llm, main


class ExerciseAnswerTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def prepare(self, *, history=True):
        notebook = self.notebook('整份习题解答')
        document = self.upload(notebook, '课程原理.md', '振荡规则：根据反馈与相位条件判断电路。')
        other = self.notebook('另一门课')
        foreign = self.upload(other, '不应读取.md', '外本秘密：不得进入本次解答。')
        public = '公共条件：所有题目中的电压均采用有效值。'
        text = public + '\n\n' + '\n\n'.join(
            f'7.{number} 正文标记_07_{number:02d}：使用题干给定的条件完成计算并说明理由。'
            for number in range(1, 15))
        response = self.client.post('/api/question-attachments', params={'notebook_id': notebook},
            files={'file': ('第七章习题.md', text.encode(), 'text/markdown')})
        self.assertEqual(response.status_code, 201, response.text)
        attachment = response.json()['image']['id']
        db.update_settings({'base_url': 'https://example.invalid', 'model': 'fixture',
                            'max_tokens': 8192, 'top_k': 0, 'context_window': 128000})
        if history:
            db.start_exchange('prior-agreement', notebook, '请记住后续计算中的单位约定。')
            db.finish_exchange('prior-agreement', {'answer': '历史约定：结果统一用 kHz 表示。'})
        contexts = [{'index': number, 'document_id': document, 'document_name': '课程原理.md',
                     'document_kind': 'md', 'chunk_id': f'fixture-page-{number}', 'page': number,
                     'text': f'规则{number}：本条说明振荡相位与反馈条件。',
                     'lines': [{'page': number, 'text': f'规则{number}：本条说明振荡相位与反馈条件。',
                                'bbox': [10, 20, 220, 40]}]}
                    for number in range(1, 44)]
        return {'notebook': notebook, 'document': document, 'other': other, 'foreign': foreign,
                'attachment': attachment, 'contexts': contexts, 'public': public}

    def payload(self, fixture, identity):
        return {'notebook_id': fixture['notebook'], 'document_ids': [fixture['document']],
                'image_ids': [fixture['attachment']], 'question': '请解答附件中的全部习题，按题号逐题说明。',
                'request_id': identity}

    def numbers(self, question):
        return [int(number) for number in re.findall(r'正文标记_07_(\d{2})', question)]

    def answer(self, question, contexts, *args, **kwargs):
        numbers = self.numbers(question)
        self.assertTrue(numbers, 'Every exercise request must retain its actual problem text')
        index = 43 if numbers[0] % 4 == 1 else 7
        citation = f'依据规则{index} [{index}]。' if contexts else '仅依据本次题干说明，无课程资料引用。'
        return '\n\n'.join(f'## 习题 7.{number}\n\n第{number}题的完整解答。{citation}' for number in numbers)

    @contextmanager
    def calls(self, fixture, answer):
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': True,
                                         'search_query': '振荡规则', 'keywords': []})
        selector = AsyncMock(return_value=fixture['contexts'])
        model = AsyncMock(side_effect=answer)
        with ExitStack() as stack:
            stack.enter_context(patch.object(main, 'resolve_context', AsyncMock(return_value={
                'context_window': 128000, 'source': 'manual'})))
            stack.enter_context(patch.object(main, 'analyze_question', planner))
            stack.enter_context(patch.object(main, 'select_evidence', selector))
            stack.enter_context(patch.object(main, '_ensure_source_images', AsyncMock(return_value=[])))
            stack.enter_context(patch.object(main, 'answer_question', model))
            yield model, selector

    def assert_complete(self, result):
        headings = re.findall(r'^## 习题 7\.(\d+)\s*$', result['answer'], re.M)
        self.assertEqual([int(number) for number in headings], list(range(1, 15)))

    def test_fourteen_questions_use_seven_scoped_batches_and_one_citation_mapping(self):
        fixture = self.prepare()
        with self.calls(fixture, self.answer) as (model, selector):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'fourteen-complete'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 7)
        self.assertEqual(selector.await_count, 1)
        for batch, call in enumerate(model.await_args_list):
            prompt, contexts, settings = call.args[:3]
            self.assertEqual(self.numbers(prompt), [batch * 2 + 1, batch * 2 + 2])
            self.assertIn(fixture['public'], prompt)
            self.assertIn('历史约定：结果统一用 kHz 表示', prompt)
            self.assertNotIn('外本秘密', prompt)
            self.assertEqual(contexts, fixture['contexts'])
            self.assertEqual(settings['max_tokens'], 8192)
            self.assertEqual(call.kwargs, {})  # This change must not restore the reverted image-solving path.
        result = response.json()
        self.assert_complete(result)
        self.assertEqual([citation['index'] for citation in result['citations']], [1, 2])
        self.assertEqual([citation['page'] for citation in result['citations']], [7, 43])
        self.assertEqual([citation['chunk_id'] for citation in result['citations']],
                         ['fixture-page-7', 'fixture-page-43'])
        self.assertIn('依据规则7 [1]', result['answer'])
        self.assertIn('依据规则43 [2]', result['answer'])
        self.assertTrue(all(citation['document_id'] == fixture['document'] for citation in result['citations']))
        self.assertEqual(db.get_exchange(fixture['notebook'], 'fourteen-complete')['status'], 'complete')

    def test_identical_historical_attachment_snapshot_is_not_replaced_by_current_batch(self):
        fixture = self.prepare(history=False)
        attachment = db.get_question_image(fixture['attachment'])
        transcription, mode = asyncio.run(main.read_question([attachment], db.get_settings()))
        self.assertEqual(mode, 'document')  # This uses only local Markdown, never vision.
        db.start_exchange('old-identical-attachment', fixture['notebook'], '先前已讨论过同一份附件。')
        db.finish_exchange('old-identical-attachment', {
            'answer': '历史完整快照说明：此条只用作近期对话背景。',
            'image_question_text': transcription, 'image_recognition_mode': 'document'})
        current_wrapper = '用户本次问题的附件内容'
        batches = []

        async def answer(question, contexts, *args):
            historical, marker, current = question.rpartition(current_wrapper)
            self.assertTrue(marker, 'The current attachment must retain its own prompt wrapper')
            self.assertIn('历史完整快照说明', historical)
            self.assertIn(transcription, historical)
            self.assertEqual(self.numbers(historical), list(range(1, 15)))
            self.assertEqual(question.count(transcription), 1)
            numbers = self.numbers(current)
            self.assertEqual(len(numbers), 2)
            self.assertIn(fixture['public'], current)
            batches.append(numbers)
            return self.answer(current, contexts, *args)

        with self.calls(fixture, answer) as (model, _):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'same-attachment-again'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 7)
        self.assertEqual(batches, [[number, number + 1] for number in range(1, 15, 2)])
        self.assert_complete(response.json())
        old = db.get_exchange(fixture['notebook'], 'old-identical-attachment')
        self.assertEqual(old['result']['image_question_text'], transcription)
        self.assertEqual(response.json()['image_question_text'], transcription)

    def test_truncated_pair_bisects_without_losing_first_or_last_exercise(self):
        fixture = self.prepare()
        attempted = []

        async def answer(question, contexts, *args):
            numbers = self.numbers(question)
            attempted.append(numbers)
            if numbers == [3, 4]:
                raise llm.OutputTruncatedError('fixture: pair reached the output limit')
            return self.answer(question, contexts, *args)

        with self.calls(fixture, answer) as (model, _):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'pair-bisected'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 9)
        self.assertIn([3], attempted)
        self.assertIn([4], attempted)
        self.assert_complete(response.json())

    def test_missing_exercise_in_a_pair_is_retried_as_individual_questions(self):
        fixture = self.prepare()

        async def answer(question, contexts, *args):
            if self.numbers(question) == [3, 4]:
                return '## 习题 7.3\n\n只返回了第三题。依据规则7 [7]。'
            return self.answer(question, contexts, *args)

        with self.calls(fixture, answer) as (model, _):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'missing-in-pair'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 9)
        self.assert_complete(response.json())

    def test_single_question_truncation_fails_clearly_without_saving_a_partial_pair(self):
        fixture = self.prepare()

        async def answer(question, contexts, *args):
            if self.numbers(question) in ([1, 2], [1]):
                raise llm.OutputTruncatedError('fixture: still too long')
            return self.answer(question, contexts, *args)

        with self.calls(fixture, answer) as (model, _):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'single-too-long'))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn('7.1', response.json()['detail'])
        self.assertRegex(response.json()['detail'], '长度|输出|截断')
        self.assertEqual(model.await_count, 2)
        record = db.get_exchange(fixture['notebook'], 'single-too-long')
        self.assertEqual(record['status'], 'error')
        self.assertNotIn('answer', record['result'])
        self.assertEqual(record['result']['generation_job']['batches'], {})

    def test_failed_batch_retry_reuses_only_validated_original_groups(self):
        fixture = self.prepare()

        async def first_attempt(question, contexts, *args):
            if self.numbers(question) == [3, 4]:
                raise RuntimeError('fixture: connection failed')
            return self.answer(question, contexts, *args)

        with self.calls(fixture, first_attempt) as (model, _):
            failed = self.client.post('/api/ask', json=self.payload(fixture, 'exercise-failed'))
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(model.await_count, 2)
        job = db.get_exchange(fixture['notebook'], 'exercise-failed')['result']['generation_job']
        self.assertEqual(job['kind'], 'ask')
        self.assertEqual(job['total'], 7)
        self.assertEqual(len(job['batches']), 1)
        db.init_db()
        with self.calls(fixture, self.answer) as (model, _):
            recovered = self.client.post(f"/api/notebooks/{fixture['notebook']}/chat/exercise-failed/retry",
                                         json={'request_id': 'exercise-recovered'})
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(model.await_count, 6)
        self.assertTrue(all(self.numbers(call.args[0]) != [1, 2] for call in model.await_args_list))
        self.assert_complete(recovered.json())
        self.assertEqual(db.get_exchange(fixture['notebook'], 'exercise-failed')['status'], 'error')

    def test_invalid_citation_is_not_checkpointed_or_hidden_by_splitting(self):
        fixture = self.prepare()

        async def answer(question, contexts, *args):
            return '\n\n'.join(f'## 习题 7.{number}\n\n错误来源 [999]。' for number in self.numbers(question))

        with self.calls(fixture, answer) as (model, _):
            response = self.client.post('/api/ask', json=self.payload(fixture, 'invalid-citation'))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn('引用', response.json()['detail'])
        self.assertEqual(model.await_count, 1)
        job = db.get_exchange(fixture['notebook'], 'invalid-citation')['result']['generation_job']
        self.assertEqual(job['batches'], {})

    def test_empty_selection_and_foreign_sources_or_attachments_never_broaden_scope(self):
        fixture = self.prepare(history=False)
        payload = {**self.payload(fixture, 'empty-selection'), 'document_ids': []}
        with self.calls(fixture, self.answer) as (model, selector):
            response = self.client.post('/api/ask', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(model.await_count, 7)
            selector.assert_not_awaited()
            for call in model.await_args_list:
                self.assertEqual(call.args[1], [])
                self.assertEqual(call.args[3], [])
                self.assertNotIn('外本秘密', call.args[0])
            self.assertEqual(response.json()['citations'], [])
            model.reset_mock()
            payload = {**self.payload(fixture, 'foreign-source'), 'document_ids': [fixture['foreign']]}
            self.assertEqual(self.client.post('/api/ask', json=payload).status_code, 400)
            payload = {**self.payload(fixture, 'foreign-attachment'), 'notebook_id': fixture['other'], 'document_ids': []}
            self.assertEqual(self.client.post('/api/ask', json=payload).status_code, 400)
            model.assert_not_awaited()

    def test_empty_selection_failed_batch_retry_keeps_explicit_empty_source_scope(self):
        fixture = self.prepare(history=False)

        async def first_attempt(question, contexts, *args):
            self.assertEqual(contexts, [])
            if self.numbers(question) == [3, 4]:
                raise RuntimeError('fixture: temporary connection failure')
            return self.answer(question, contexts, *args)

        payload = {**self.payload(fixture, 'empty-failed'), 'document_ids': []}
        with self.calls(fixture, first_attempt) as (model, selector):
            failed = self.client.post('/api/ask', json=payload)
            self.assertEqual(failed.status_code, 502, failed.text)
            self.assertEqual(model.await_count, 2)
            selector.assert_not_awaited()
        job = db.get_exchange(fixture['notebook'], 'empty-failed')['result']['generation_job']
        self.assertEqual(job['payload']['document_ids'], [])
        self.assertEqual(len(job['batches']), 1)
        with self.calls(fixture, self.answer) as (model, selector):
            recovered = self.client.post(f"/api/notebooks/{fixture['notebook']}/chat/empty-failed/retry",
                                         json={'request_id': 'empty-recovered'})
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(model.await_count, 6)
            selector.assert_not_awaited()
            for call in model.await_args_list:
                self.assertEqual(call.args[1], [])
                self.assertEqual(call.args[3], [])
                self.assertNotIn('外本秘密', call.args[0])
        self.assertEqual(recovered.json()['citations'], [])
        self.assert_complete(recovered.json())

    def test_cancellation_stops_later_groups_and_rejects_late_completion(self):
        fixture = self.prepare(history=False)
        started = threading.Event()
        stopped = threading.Event()

        async def answer(question, contexts, *args):
            if self.numbers(question) == [3, 4]:
                started.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    stopped.set()
                    raise
            return self.answer(question, contexts, *args)

        with self.calls(fixture, answer) as (model, _), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, '/api/ask', json=self.payload(fixture, 'cancel-exercises'))
            self.assertTrue(started.wait(3), 'The second group must start after saving the first group')
            self.assertEqual(self.client.post(
                f"/api/notebooks/{fixture['other']}/chat/cancel-exercises/cancel").status_code, 404)
            cancelled = self.client.post(f"/api/notebooks/{fixture['notebook']}/chat/cancel-exercises/cancel")
            self.assertTrue(cancelled.json()['cancelled'])
            response = future.result(timeout=3)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertTrue(stopped.wait(1))
            self.assertEqual(model.await_count, 2)
        record = db.get_exchange(fixture['notebook'], 'cancel-exercises')
        self.assertEqual(record['status'], 'error')
        self.assertTrue(record['result']['cancelled'])
        self.assertFalse(db.finish_pending_exchange('cancel-exercises', {'answer': 'late result'}))
        self.assertTrue(db.get_exchange(fixture['notebook'], 'cancel-exercises')['result']['cancelled'])


if __name__ == '__main__':
    unittest.main()
