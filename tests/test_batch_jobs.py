import json
import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import batch_jobs, db, generation


class BatchJobTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def prepare(self):
        notebook = self.notebook('恢复验收')
        docs = [self.upload(notebook, f'资料{n}.md', f'资料{n}的独有知识点。') for n in (1, 2)]
        db.update_settings({'base_url': 'https://example.invalid', 'model': 'test'})
        return notebook, docs

    def test_summary_failure_restart_retry_reuses_only_valid_completed_batches(self):
        notebook, docs = self.prepare()
        model = AsyncMock(side_effect=['已验证的第一批 [1]', RuntimeError('模型暂时超时')])
        with patch('app.main.make_summary', model):
            failed = self.client.post('/api/summary', json={'notebook_id': notebook, 'document_ids': docs, 'request_id': 'summary-fail'})
        self.assertEqual(failed.status_code, 502, failed.text)
        job = db.get_exchange(notebook, 'summary-fail')['result']['generation_job']
        self.assertEqual(len(job['batches']), 1)
        self.assertEqual(job['total'], 2)
        db.init_db()
        model = AsyncMock(return_value='恢复的第二批 [2]')
        with patch('app.main.make_summary', model):
            result = self.client.post(f'/api/notebooks/{notebook}/chat/summary-fail/retry', json={'request_id': 'summary-recovered'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(model.await_count, 1)
        self.assertIn('已验证的第一批', result.json()['answer'])
        self.assertIn('恢复的第二批', result.json()['answer'])
        self.assertEqual({c['document_id'] for c in result.json()['citations']}, set(docs))
        self.assertEqual(db.get_exchange(notebook, 'summary-fail')['status'], 'error')
        self.assertNotIn('generation_job', result.json())

    def test_quiz_retry_reuses_validated_questions_without_reusing_foreign_context(self):
        notebook, docs = self.prepare()
        def question(index, text):
            return json.dumps({'questions': [{'prompt': text, 'options': ['甲', '乙', '丙', '丁'], 'answer': 0,
                'explanation': f'直接依据 [{index}]', 'sources': [index]}]})
        with patch('app.quizzes.chat_completion', AsyncMock(side_effect=[question(1, '第一批题目'), RuntimeError('断线')])):
            failed = self.client.post('/api/quiz', json={'notebook_id': notebook, 'document_ids': docs, 'request_id': 'quiz-fail'})
        self.assertEqual(failed.status_code, 502, failed.text)
        completion = AsyncMock(return_value=question(2, '第二批题目'))
        with patch('app.quizzes.chat_completion', completion):
            result = self.client.post(f'/api/notebooks/{notebook}/chat/quiz-fail/retry', json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(completion.await_count, 1)
        self.assertEqual([q['sources'] for q in result.json()['quiz']['questions']], [[1], [2]])
        other = self.notebook('别的课程')
        self.assertEqual(self.client.post(f'/api/notebooks/{other}/chat/quiz-fail/retry', json={}).status_code, 404)
        self.client.patch(f'/api/documents/{docs[0]}', json={'notebook_id': other})
        with patch('app.quizzes.chat_completion', AsyncMock()) as model:
            self.assertEqual(self.client.post(f'/api/notebooks/{notebook}/chat/quiz-fail/retry', json={}).status_code, 400)
            model.assert_not_awaited()

    def test_checkpoint_invalidated_by_changed_evidence_model_or_requirement_and_late_save_ignored(self):
        notebook, docs = self.prepare()
        db.start_exchange('check', notebook, '复习')
        batch_jobs.begin('check', 'quiz', {'document_ids': docs})
        batch = [[{'index': 1, 'document_id': docs[0], 'text': '原文'}]]
        cache = batch_jobs.Checkpoints('check', {'model': 'a', 'api_key': 'private'}, '旧主题', batch)
        cache.save(0, '有效结果')
        self.assertEqual(batch_jobs.Checkpoints('check', {'model': 'a', 'api_key': 'changed'}, '旧主题', batch).get(0), '有效结果')
        self.assertIsNone(batch_jobs.Checkpoints('check', {'model': 'b'}, '旧主题', batch).get(0))
        cache.save(0, '有效结果')
        self.assertIsNone(batch_jobs.Checkpoints('check', {'model': 'a'}, '新主题', batch).get(0))
        cache.save(0, '有效结果')
        self.assertIsNone(batch_jobs.Checkpoints('check', {'model': 'a'}, '旧主题', [[{**batch[0][0], 'text': '新原文'}]]).get(0))
        db.finish_pending_exchange('check', {'cancelled': True}, error=True)
        cache.save(0, '不该写入')
        self.assertEqual(db.get_exchange(notebook, 'check')['result'], {'cancelled': True})

    def test_restart_retains_checkpoints_and_progress(self):
        notebook, docs = self.prepare()
        db.start_exchange('interrupted', notebook, '复习')
        batch_jobs.begin('interrupted', 'summary', {'document_ids': docs})
        cache = batch_jobs.Checkpoints('interrupted', {}, '主题', [[{'text': '原文'}]])
        cache.save(0, '批次结果')
        db.set_generation_progress('interrupted', 'answer', '生成提纲', '第一批已完成')
        db.init_db()
        result = db.get_exchange(notebook, 'interrupted')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(result['result']['generation_job']['batches']), 1)
        self.assertIn('第一批', result['result']['progress']['detail'])

    def test_retry_keeps_original_explicit_scope_after_more_sources_are_uploaded(self):
        notebook, docs = self.prepare()
        with patch('app.main.make_summary', AsyncMock(side_effect=['第一批 [1]', RuntimeError('超时')])):
            failed = self.client.post('/api/summary', json={
                'notebook_id': notebook, 'document_ids': docs, 'request_id': 'scope-fail'})
        self.assertEqual(failed.status_code, 502, failed.text)
        extra = self.upload(notebook, '后来上传的资料.md', '这份资料从未在原任务中勾选。')
        model = AsyncMock(return_value='第二批 [2]')
        with patch('app.main.make_summary', model):
            result = self.client.post(f'/api/notebooks/{notebook}/chat/scope-fail/retry', json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(model.await_count, 1)
        self.assertEqual({citation['document_id'] for citation in result.json()['citations']}, set(docs))
        self.assertNotIn(extra, {context['document_id'] for context in model.await_args.args[0]})

    def test_cancelled_or_deleted_generation_cannot_be_retried_or_late_saved(self):
        notebook, docs = self.prepare()
        db.start_exchange('cancelled-job', notebook, '测验')
        batch_jobs.begin('cancelled-job', 'quiz', {'notebook_id': notebook, 'document_ids': docs})
        cache = batch_jobs.Checkpoints('cancelled-job', {}, '主题', [[{'text': '原文'}]])
        cache.save(0, ['已完成的一批'])
        self.assertTrue(generation.cancel(notebook, 'cancelled-job')['cancelled'])
        cache.save(0, ['取消后到达的结果'])
        self.assertTrue(db.get_exchange(notebook, 'cancelled-job')['result']['cancelled'])
        with patch('app.quizzes.chat_completion', AsyncMock()) as model:
            retry = self.client.post(f'/api/notebooks/{notebook}/chat/cancelled-job/retry', json={})
            self.assertEqual(retry.status_code, 400)
            self.assertIsNotNone(db.delete_exchange(notebook, 'cancelled-job'))
            retry = self.client.post(f'/api/notebooks/{notebook}/chat/cancelled-job/retry', json={})
            self.assertEqual(retry.status_code, 404)
            model.assert_not_awaited()

    def test_ordinary_and_quoted_summary_questions_resume_completed_batches(self):
        for quoted in (False, True):
            with self.subTest(quoted=quoted):
                notebook, docs = self.prepare()
                identity = 'quoted-summary' if quoted else 'ordinary-summary'
                payload = {'notebook_id': notebook, 'document_ids': docs, 'request_id': identity,
                           'question': '请根据勾选资料生成复习提纲'}
                if quoted:
                    parent = identity + '-parent'
                    db.start_exchange(parent, notebook, '上次问题')
                    db.finish_pending_exchange(parent, {'answer': '这是用户引用的回答快照。', 'citations': []})
                    payload['reply_to_id'] = parent
                planner = AsyncMock(return_value={
                    'intent': 'summary', 'needs_retrieval': True, 'search_query': '', 'keywords': []})
                capacity = AsyncMock(return_value={'context_window': 32768, 'source': 'manual'})
                model = AsyncMock(side_effect=['普通提问保留的第一批 [1]', RuntimeError('第二批超时')])
                with (patch('app.main.resolve_context', capacity), patch('app.main.analyze_question', planner),
                      patch('app.main.make_summary', model)):
                    failed = self.client.post('/api/ask', json=payload)
                self.assertEqual(failed.status_code, 502, failed.text)
                job = db.get_exchange(notebook, identity)['result']['generation_job']
                self.assertEqual(job['kind'], 'ask')
                self.assertEqual(len(job['batches']), 1)
                resumed_model = AsyncMock(return_value='普通提问恢复的第二批 [2]')
                with (patch('app.main.resolve_context', capacity), patch('app.main.analyze_question', planner),
                      patch('app.main.make_summary', resumed_model)):
                    result = self.client.post(f'/api/notebooks/{notebook}/chat/{identity}/retry', json={})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(resumed_model.await_count, 1)
                self.assertIn('普通提问保留的第一批', result.json()['answer'])
                self.assertIn('普通提问恢复的第二批', result.json()['answer'])
                self.assertEqual({citation['document_id'] for citation in result.json()['citations']}, set(docs))
