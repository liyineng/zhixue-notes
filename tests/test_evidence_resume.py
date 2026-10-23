"""Evidence checkpoints survive transport failures and never replace source validation.

API cases use a temporary SQLite database and local Markdown fixtures. Every model
entry point is AsyncMock; no provider, vision request, or personal data is used.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import test_workspace
from app import batch_jobs, db, generation, llm, main


class MemoryCheckpoints:
    def __init__(self, values=None):
        self.values = deepcopy(values or {})
        self.saved = []

    def get(self, index):
        return deepcopy(self.values.get(index))

    def save(self, index, value):
        self.values[index] = deepcopy(value)
        self.saved.append((index, deepcopy(value)))


class EvidenceResumeTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload
    settings = {'base_url': 'https://example.invalid', 'model': 'fixture', 'max_tokens': 8192,
                'context_window': 128000, 'temperature': .2}

    def contexts(self, count=17, document='selected-source'):
        return [{'index': number, 'document_id': document, 'document_name': '已选课程原理.md',
                 'document_kind': 'md', 'chunk_id': f'fixture-chunk-{number}', 'page': number,
                 'text': f'规则{number:03d}：使用原文给定的相位和反馈条件完成计算。',
                 'lines': [{'page': number, 'text': f'规则{number:03d}：使用原文给定的相位和反馈条件完成计算。',
                            'bbox': [10, number * 10, 400, number * 10 + 8]}]}
                for number in range(1, count + 1)]

    def model_numbers(self, request):
        return tuple(int(number) for number in re.findall(r'\[(\d+)\] 来源：', request['messages'][-1]['content']))

    def response(self, numbers, sources):
        return json.dumps({'evidence': [{'source': number, 'quote': sources[number]['text']} for number in numbers]},
                          ensure_ascii=False)

    def fixture(self):
        notebook = self.notebook('证据核对恢复')
        document = self.upload(notebook, '已选课程原理.md', '课程规则：使用原文的相位和反馈条件。')
        other = self.notebook('其他笔记本')
        foreign = self.upload(other, '外本秘密.md', '规则与外本秘密绝不能进入当前资料。')
        text = '公共条件：所有电压使用有效值。\n\n' + '\n\n'.join(
            f'7.{number} 正文标记_07_{number:02d}：已知真实题目条件，试求题目所问并说明理由。'
            for number in range(1, 5))
        attachment = self.client.post('/api/question-attachments', params={'notebook_id': notebook},
            files={'file': ('四道题.md', text.encode(), 'text/markdown')})
        self.assertEqual(attachment.status_code, 201, attachment.text)
        db.update_settings({**self.settings, 'top_k': 0})
        contexts = self.contexts(document=document)
        return {'notebook': notebook, 'document': document, 'other': other, 'foreign': foreign,
                'attachment': attachment.json()['image']['id'], 'contexts': contexts}

    def payload(self, fixture, identity):
        return {'notebook_id': fixture['notebook'], 'document_ids': [fixture['document']],
                'question': '请解答附件中的全部习题，按题号说明。', 'image_ids': [fixture['attachment']],
                'top_k': 0, 'request_id': identity}

    def plan(self):
        return {'intent': 'question', 'needs_retrieval': True, 'search_query': '课程规则', 'keywords': []}

    def answer(self, question, contexts, *args):
        numbers = re.findall(r'正文标记_07_(\d{2})', question)
        self.assertTrue(numbers)
        self.assertEqual({source['document_id'] for source in contexts}, {self.active_fixture['document']})
        return '\n\n'.join(f'## 习题 7.{int(number)}\n\n依照本次真实资料得到完整解答 [1]。' for number in numbers)

    @contextmanager
    def api_calls(self, fixture, planner, evidence_model, answer_model):
        self.active_fixture = fixture
        with ExitStack() as stack:
            stack.enter_context(patch.object(main, 'resolve_context', AsyncMock(return_value={
                'context_window': 128000, 'source': 'manual'})))
            stack.enter_context(patch.object(main, 'analyze_question', planner))
            stack.enter_context(patch.object(main, '_ensure_source_images', AsyncMock(return_value=[])))
            stack.enter_context(patch.object(main, '_contexts_for_results', return_value=fixture['contexts']))
            stack.enter_context(patch.object(llm, 'chat_completion', evidence_model))
            stack.enter_context(patch.object(main, 'answer_question', answer_model))
            yield

    def test_api_transport_failure_resumes_planner_evidence_and_answer_without_losing_phases(self):
        fixture = self.fixture()
        sources = {source['index']: source for source in fixture['contexts']}
        first_calls = []
        async def first_model(**request):
            numbers = self.model_numbers(request)
            first_calls.append(numbers)
            if len(first_calls) == 2:
                raise httpx.TransportError('fixture connection interrupted during the second evidence batch')
            return self.response(numbers, sources)
        first_evidence = AsyncMock(side_effect=first_model)
        first_planner = AsyncMock(return_value=self.plan())
        first_answer = AsyncMock(side_effect=self.answer)
        with self.api_calls(fixture, first_planner, first_evidence, first_answer):
            failed = self.client.post('/api/ask', json=self.payload(fixture, 'evidence-disconnected'))
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(first_planner.await_count, 1)
        self.assertEqual(first_evidence.await_count, 2)
        first_answer.assert_not_awaited()
        job = db.get_exchange(fixture['notebook'], 'evidence-disconnected')['result']['generation_job']
        self.assertEqual(len(job['phases']['analysis']['batches']), 1)
        self.assertEqual(job['phases']['evidence']['total'], 3)
        self.assertEqual(len(job['phases']['evidence']['batches']), 1)
        self.assertEqual(job['batches'], {})
        self.assertNotIn('api_key', json.dumps(job))
        db.init_db()  # Recovery must work after a normal service reopen as well.

        remaining_calls = []
        async def remainder(**request):
            numbers = self.model_numbers(request); remaining_calls.append(numbers)
            self.assertTrue(set(numbers).isdisjoint(first_calls[0]), 'a complete first batch must not be billed again')
            return self.response(numbers, sources)
        planner = AsyncMock(side_effect=AssertionError('a valid saved planner must not run again'))
        evidence = AsyncMock(side_effect=remainder)
        answer_calls = 0
        async def answer_then_disconnect(*args):
            nonlocal answer_calls
            answer_calls += 1
            if answer_calls == 2:
                raise RuntimeError('fixture answer batch temporarily disconnected')
            return self.answer(*args)
        answer = AsyncMock(side_effect=answer_then_disconnect)
        with self.api_calls(fixture, planner, evidence, answer):
            answer_failed = self.client.post(f"/api/notebooks/{fixture['notebook']}/chat/evidence-disconnected/retry",
                                            json={'request_id': 'answer-disconnected'})
        self.assertEqual(answer_failed.status_code, 502, answer_failed.text)
        planner.assert_not_awaited()
        self.assertEqual(evidence.await_count, 2)
        self.assertEqual(remaining_calls, [tuple(range(9, 17)), (17,)])
        job = db.get_exchange(fixture['notebook'], 'answer-disconnected')['result']['generation_job']
        self.assertEqual(len(job['phases']['analysis']['batches']), 1)
        self.assertEqual(len(job['phases']['evidence']['batches']), 3,
                         'answer-stage begin must merge the evidence completed during this attempt')
        self.assertEqual(len(job['batches']), 1)

        planner = AsyncMock(side_effect=AssertionError('planner must be reused'))
        evidence = AsyncMock(side_effect=AssertionError('all evidence batches must be reused'))
        answer = AsyncMock(side_effect=self.answer)
        with self.api_calls(fixture, planner, evidence, answer):
            complete = self.client.post(f"/api/notebooks/{fixture['notebook']}/chat/answer-disconnected/retry",
                                       json={'request_id': 'all-recovered'})
        self.assertEqual(complete.status_code, 200, complete.text)
        planner.assert_not_awaited(); evidence.assert_not_awaited()
        self.assertEqual(answer.await_count, 1, 'the completed first answer pair is also reusable')
        self.assertEqual(re.findall(r'^## 习题 (7\.\d+)\s*$', complete.json()['answer'], re.M),
                         ['7.1', '7.2', '7.3', '7.4'])
        self.assertEqual({citation['document_id'] for citation in complete.json()['citations']}, {fixture['document']})
        self.assertNotIn('generation_job', complete.json())

    def test_cached_evidence_rebuilds_current_lines_and_accepts_empty_verified_batches(self):
        candidates = self.contexts(9)
        # Batch-local result indices are not original source indices. Identities
        # and quotes must be validated against the current candidate, not a cache's lines.
        cached = [{**source, 'index': index, 'lines': [{'page': 999, 'text': 'cached fake geometry', 'bbox': [0, 0, 1, 1]}]}
                  for index, source in enumerate(candidates[:8], 1)]
        checkpoint = MemoryCheckpoints({0: cached, 1: []})
        batches = []
        def factory(value): batches.extend(deepcopy(value)); return checkpoint
        progress = []
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=AssertionError('valid cache must not call a model'))) as model:
            selected = asyncio.run(llm.select_evidence('课程规则', candidates, self.settings, 0,
                checkpoint_factory=factory, on_batch=lambda *args: progress.append(args)))
        model.assert_not_awaited()
        self.assertEqual([len(batch) for batch in batches], [8, 1])
        self.assertEqual([source['index'] for source in selected], list(range(1, 9)))
        self.assertEqual([source['lines'] for source in selected], [source['lines'] for source in candidates[:8]])
        self.assertEqual([(index, total, reused) for index, total, _, reused in progress], [(1, 2, True), (2, 2, True)])
        self.assertEqual(checkpoint.saved, [])

    def test_corrupt_forged_or_stale_cache_reruns_the_entire_batch(self):
        candidates = self.contexts(2)
        sources = {source['index']: source for source in candidates}
        valid = deepcopy(candidates)
        bad_values = [None, {}, ['not evidence'],
                      [{**valid[0], 'document_id': 'foreign-notebook-document'}],
                      [{**valid[0], 'chunk_id': 'different-original-chunk'}],
                      [{**valid[0], 'page': 400}],
                      [{**valid[0], 'text': '原文不存在的伪造摘录'}],
                      [valid[0], {**valid[1], 'text': 'unverified partial result'}]]
        for value in bad_values:
            with self.subTest(cache=value):
                checkpoint = MemoryCheckpoints({0: value})
                progress = []
                model = AsyncMock(return_value=self.response((1, 2), sources))
                with patch.object(llm, 'chat_completion', model):
                    selected = asyncio.run(llm.select_evidence('课程规则', candidates, self.settings, 0,
                        checkpoint_factory=lambda batches: checkpoint, on_batch=lambda *args: progress.append(args)))
                self.assertEqual(model.await_count, 1)
                self.assertEqual(selected, candidates)
                self.assertEqual(len(checkpoint.saved), 1)
                self.assertEqual(progress[0][3], False)

    def test_phase_fingerprints_invalidate_changed_model_question_scope_and_source_text(self):
        notebook = self.notebook('缓存指纹隔离')
        source = self.contexts(1)
        requirement = ['evidence', notebook, ['selected-source'], '原始完整问题和题干']
        changes = [('model', {**self.settings, 'model': 'changed-model'}, requirement, [source]),
                   ('question', self.settings, [*requirement[:-1], '不同问题'], [source]),
                   ('notebook', self.settings, ['evidence', 'foreign-notebook', ['selected-source'], requirement[-1]], [source]),
                   ('selection', self.settings, ['evidence', notebook, ['another-selected-source'], requirement[-1]], [source]),
                   ('text', self.settings, requirement, [[{**source[0], 'text': '已经变化的课程原文'}]])]
        for name, settings, changed_requirement, changed_batches in changes:
            with self.subTest(change=name):
                identity = 'fingerprint-' + name
                db.start_exchange(identity, notebook, '测试缓存隔离')
                batch_jobs.begin(identity, 'ask', {'notebook_id': notebook, 'document_ids': ['selected-source']})
                cache = batch_jobs.Checkpoints(identity, self.settings, requirement, [source], phase='evidence')
                cache.save(0, source)
                self.assertEqual(batch_jobs.Checkpoints(identity, self.settings, requirement, [source], phase='evidence').get(0), source)
                changed = batch_jobs.Checkpoints(identity, settings, changed_requirement, changed_batches, phase='evidence')
                self.assertIsNone(changed.get(0))

    def test_cancelled_exchange_rejects_retry_and_late_phase_checkpoint_save(self):
        notebook = self.notebook('取消核对不能继续写入')
        source = self.contexts(1)
        db.start_exchange('cancelled-evidence', notebook, '取消证据核对')
        batch_jobs.begin('cancelled-evidence', 'ask', {'notebook_id': notebook, 'document_ids': ['selected-source']})
        cache = batch_jobs.Checkpoints('cancelled-evidence', self.settings, '原题干', [source], phase='evidence')
        cache.save(0, [])
        self.assertTrue(generation.cancel(notebook, 'cancelled-evidence')['cancelled'])
        cancelled = deepcopy(db.get_exchange(notebook, 'cancelled-evidence')['result'])
        cache.save(0, source)
        self.assertEqual(db.get_exchange(notebook, 'cancelled-evidence')['result'], cancelled)
        with patch.object(llm, 'chat_completion', AsyncMock()) as model, patch.object(main, 'analyze_question', AsyncMock()) as planner:
            retry = self.client.post(f'/api/notebooks/{notebook}/chat/cancelled-evidence/retry', json={})
        self.assertEqual(retry.status_code, 400)
        model.assert_not_awaited(); planner.assert_not_awaited()
