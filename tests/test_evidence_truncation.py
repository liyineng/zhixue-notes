"""Adaptive evidence screening retries only complete-output truncation.

All model responses are AsyncMock fixtures; no provider request or local data is used.
"""
from __future__ import annotations

import asyncio
import json
import re
import unittest
from collections import Counter
from unittest.mock import AsyncMock, patch

import httpx

from app import llm


class EvidenceTruncationTests(unittest.TestCase):
    settings = {'model': 'fixture', 'base_url': 'https://example.invalid', 'context_window': 128000}

    def candidate(self, index: int, *, page: int | None = None, text: str | None = None):
        page = index if page is None else page
        text = text or f'规则{index:03d}：记录源地址及入端口，按目的地址查表决定转发或过滤。'
        return {'index': index, 'id': f'fixture-chunk-{index}', 'document_id': 'selected-course',
                'document_name': 'L06-网桥课件.pdf', 'document_kind': 'pdf', 'page': page,
                'text': text, 'lines': [{'page': page, 'text': text, 'bbox': [10, 20, 400, 40]}]}

    def numbers(self, request):
        return tuple(int(number) for number in re.findall(r'\[(\d+)\] 来源：', request['messages'][-1]['content']))

    def response(self, numbers, sources, *, invalid=False):
        entries = [{'source': number, 'quote': sources[number]['text']} for number in numbers]
        if invalid and numbers:
            entries += [dict(entries[0]), {'source': numbers[0], 'quote': '原文里不存在的伪造规则'},
                        {'source': 999999, 'quote': sources[numbers[0]]['text']}]
        return json.dumps({'evidence': entries}, ensure_ascii=False)

    def test_all_356_candidates_are_verified_after_partial_and_nested_batch_truncation(self):
        candidates = [self.candidate(number) for number in range(1, 357)]
        sources = {source['index']: source for source in candidates}
        complete, truncated = [], []

        async def reply(**request):
            numbers = self.numbers(request)
            self.assertTrue(numbers)
            self.assertLessEqual(len(numbers), 8, 'all=0 must still use bounded requests')
            # Some eight-item batches succeed immediately. Others must be split
            # repeatedly down to leaves; both paths must reach the final source.
            if len(numbers) > 1 and ((numbers[0] - 1) // 8) % 3 == 0:
                truncated.append(numbers)
                raise llm.OutputTruncatedError('模型达到输出长度上限，正文未完整返回。')
            complete.append(numbers)
            return self.response(numbers, sources, invalid=True)

        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=reply)) as model:
            selected = asyncio.run(llm.select_evidence('网桥如何自学习和转发？', candidates, self.settings, 0))
        self.assertTrue(truncated)
        self.assertTrue(any(len(batch) == 1 for batch in complete))
        self.assertTrue(any(len(batch) > 1 for batch in complete))
        self.assertTrue(any(len(batch) == 2 for batch in truncated), 'truncation must recurse beyond the first split')
        self.assertEqual(Counter(number for batch in complete for number in batch), Counter(range(1, 357)),
                         'every candidate must be verified exactly once in a complete response')
        self.assertGreater(model.await_count, 45)
        self.assertEqual(len(selected), 356)
        self.assertEqual([source['index'] for source in selected], list(range(1, 357)))
        self.assertEqual([source['page'] for source in selected], list(range(1, 357)))
        self.assertEqual([source['id'] for source in selected], [source['id'] for source in candidates])
        for source in selected:
            self.assertEqual(source['document_id'], 'selected-course')
            self.assertEqual(source['text'], sources[source['page']]['text'])
            self.assertEqual(source['lines'], sources[source['page']]['lines'])
        self.assertEqual([source['index'] for source in candidates], list(range(1, 357)), 'input references must stay intact')

    def test_one_candidate_retries_once_with_shorter_quote_and_returns_actual_source_line(self):
        candidate = self.candidate(37, page=47)
        reply = self.response((37,), {37: candidate}, invalid=True)
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=[llm.OutputTruncatedError('length'), reply])) as model:
            selected = asyncio.run(llm.select_evidence('转发规则', [candidate], self.settings, 6))
        self.assertEqual(model.await_count, 2)
        first, second = [call.kwargs['messages'][0]['content'] for call in model.await_args_list]
        self.assertRegex(first, r'摘录不超过\s*500\s*字')
        self.assertRegex(second, r'摘录不超过\s*160\s*字')
        self.assertEqual([self.numbers(call.kwargs) for call in model.await_args_list], [(37,), (37,)])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['index'], 1)
        self.assertEqual(selected[0]['page'], 47)
        self.assertEqual(selected[0]['text'], candidate['text'])
        self.assertEqual(selected[0]['lines'], candidate['lines'])

    def test_one_candidate_still_truncated_fails_with_name_and_real_page_after_two_calls(self):
        candidate = self.candidate(37, page=47)
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=llm.OutputTruncatedError('length'))) as model:
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(llm.select_evidence('转发规则', [candidate], self.settings, 0))
        self.assertEqual(model.await_count, 2, 'a single candidate must not retry forever')
        self.assertIn(candidate['document_name'], str(raised.exception))
        self.assertRegex(str(raised.exception), r'(?:第\s*)?47\s*页')
        self.assertNotIn('第 37 页', str(raised.exception), 'citation numbering is not a page number')
        prompts = [call.kwargs['messages'][0]['content'] for call in model.await_args_list]
        self.assertRegex(prompts[0], r'摘录不超过\s*500\s*字')
        self.assertRegex(prompts[1], r'摘录不超过\s*160\s*字')

    def test_runtime_http_and_timeout_errors_propagate_without_recursive_model_calls(self):
        candidates = [self.candidate(number) for number in range(1, 9)]
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        errors = [RuntimeError('Model request failed with HTTP 401: invalid credentials'),
                  RuntimeError('模型接口请求超时，自动重试后仍未收到完整响应。'),
                  # Identical human-readable wording alone must not trigger the
                  # typed truncation recovery branch.
                  RuntimeError('模型输出达到长度上限，未完整返回结果。'),
                  httpx.HTTPStatusError('provider HTTP 503', request=request,
                                        response=httpx.Response(503, request=request))]
        for error in errors:
            with self.subTest(error=type(error).__name__ + ':' + str(error)), \
                 patch.object(llm, 'chat_completion', AsyncMock(side_effect=error)) as model:
                with self.assertRaises(type(error)) as raised:
                    asyncio.run(llm.select_evidence('转发规则', candidates, self.settings, 0))
                self.assertIs(raised.exception, error)
                self.assertEqual(model.await_count, 1)

    def test_cancellation_propagates_without_retrying_or_accepting_partial_evidence(self):
        cancellation = asyncio.CancelledError('fixture generation cancelled')
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=cancellation)) as model:
            with self.assertRaises(asyncio.CancelledError) as raised:
                asyncio.run(llm.select_evidence('转发规则', [self.candidate(1), self.candidate(2)], self.settings, 0))
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(model.await_count, 1)

    def test_cancellation_after_a_successful_recursive_leaf_does_not_return_partial_results(self):
        candidates = [self.candidate(1), self.candidate(2)]
        sources = {candidate['index']: candidate for candidate in candidates}
        cancellation = asyncio.CancelledError('cancelled while checking the second leaf')
        replies = [llm.OutputTruncatedError('length'), self.response((1,), sources), cancellation]
        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=replies)) as model:
            with self.assertRaises(asyncio.CancelledError) as raised:
                asyncio.run(llm.select_evidence('转发规则', candidates, self.settings, 0))
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(model.await_count, 3)
        self.assertEqual([self.numbers(call.kwargs) for call in model.await_args_list], [(1, 2), (1,), (2,)])

    def test_recursive_results_deduplicate_and_keep_only_verbatim_quotes(self):
        first = self.candidate(11, page=25, text='记录源地址和进入端口。')
        second = {**first, 'index': 19, 'id': 'overlapping-original-chunk'}
        sources = {11: first, 19: second}

        async def reply(**request):
            numbers = self.numbers(request)
            if len(numbers) > 1:
                raise llm.OutputTruncatedError('length')
            return self.response(numbers, sources, invalid=True)

        with patch.object(llm, 'chat_completion', AsyncMock(side_effect=reply)) as model:
            selected = asyncio.run(llm.select_evidence('转发规则', [first, second], self.settings, 6))
        self.assertEqual(model.await_count, 3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['index'], 1)
        self.assertEqual(selected[0]['document_id'], first['document_id'])
        self.assertEqual(selected[0]['page'], 25)
        self.assertEqual(selected[0]['text'], first['text'])
        self.assertEqual(selected[0]['lines'], first['lines'])

    def test_invalid_or_incomplete_json_is_not_misclassified_as_length_truncation(self):
        candidates = [self.candidate(1), self.candidate(2)]
        responses = ['{"evidence":[{"source":1,"quote":"记录源地址', '{"wrong_key": []}', 'not json']
        for response in responses:
            with self.subTest(response=response), \
                 patch.object(llm, 'chat_completion', AsyncMock(return_value=response)) as model:
                with self.assertRaisesRegex(RuntimeError, '有效结果'):
                    asyncio.run(llm.select_evidence('转发规则', candidates, self.settings, 6))
                self.assertEqual(model.await_count, 1)


if __name__ == '__main__':
    unittest.main()
