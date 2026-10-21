import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.llm import chat_completion, answer_question


class CompletionTests(unittest.TestCase):
    def network_call(self, failures, *, status=200):
        attempts = []
        def handle(request):
            attempts.append(request)
            if len(attempts) <= failures:
                raise httpx.ReadTimeout('', request=request)
            return httpx.Response(status, json=self.reply('完整回答'))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.llm.httpx.AsyncClient', return_value=client), patch('app.llm.asyncio.sleep', AsyncMock()):
            result = asyncio.run(chat_completion(base_url='https://example.invalid', api_key='test',
                model='mock', messages=[], temperature=0, max_tokens=100))
        return result, len(attempts)

    def test_transient_timeout_retries_once_and_persistent_timeout_has_a_message(self):
        self.assertEqual(self.network_call(1), ('完整回答', 2))
        with self.assertRaisesRegex(RuntimeError, '请求超时'):
            self.network_call(2)

    def test_gateway_failure_retries_but_authentication_failure_does_not(self):
        for status, expected in [(503, 2), (401, 1)]:
            attempts = []
            def handle(request):
                attempts.append(request)
                return httpx.Response(status, text='upstream error')
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            with patch('app.llm.httpx.AsyncClient', return_value=client), patch('app.llm.asyncio.sleep', AsyncMock()):
                with self.assertRaisesRegex(RuntimeError, f'HTTP {status}'):
                    asyncio.run(chat_completion(base_url='https://example.invalid', api_key='test',
                        model='mock', messages=[], temperature=0, max_tokens=100))
            self.assertEqual(len(attempts), expected)

    def call(self, replies, *, base_url='https://api.deepseek.com', thinking=None, json_mode=False):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=replies[min(len(requests) - 1, len(replies) - 1)])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.llm.httpx.AsyncClient', return_value=client):
            result = asyncio.run(chat_completion(base_url=base_url, api_key='test-key',
                model='deepseek-flash', messages=[{'role': 'user', 'content': '题目'}],
                temperature=0, max_tokens=2600, thinking=thinking, json_mode=json_mode))
        return result, requests

    def reply(self, content, finish='stop'):
        return {'choices': [{'message': {'content': content, 'reasoning_content': 'private reasoning'},
                             'finish_reason': finish}]}

    def test_reasoning_budget_exhaustion_retries_without_returning_reasoning(self):
        result, requests = self.call([self.reply('', 'length'), self.reply('完整答案')])
        self.assertEqual(result, '完整答案')
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]['thinking'], {'type': 'disabled'})

    def test_extraction_disables_thinking_and_retries_transient_empty_once(self):
        result, requests = self.call([self.reply(None), self.reply('题目文字')], thinking=False)
        self.assertEqual(result, '题目文字')
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(r['thinking'] == {'type': 'disabled'} for r in requests))

    def test_problem_solving_retains_thinking_with_a_larger_retry_budget(self):
        result, requests = self.call([self.reply('', 'length'), self.reply('推导后的完整答案')], thinking=True)
        self.assertEqual(result, '推导后的完整答案')
        self.assertEqual(requests[1]['thinking'], {'type': 'enabled'})
        self.assertGreaterEqual(requests[1]['max_tokens'], 8192)

    def test_visual_reasoning_retry_grows_to_64k_and_preserves_original_messages(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=self.reply('部分正文' if len(requests) == 1 else '完整正文', 'length' if len(requests) == 1 else 'stop'))
        content = [{'type': 'text', 'text': '当前题目与资料'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}]
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.llm.httpx.AsyncClient', return_value=client):
            result = asyncio.run(chat_completion(base_url='https://api.deepseek.com', api_key='test',model='deepseek-flash',
                messages=[{'role': 'user', 'content': content}], temperature=0, max_tokens=32768, thinking=True))
        self.assertEqual(result, '完整正文')
        self.assertEqual([r['max_tokens'] for r in requests], [32768, 65536])
        self.assertTrue(all(r['thinking'] == {'type': 'enabled'} for r in requests))
        self.assertTrue(all(r['messages'][0]['content'] == content for r in requests))
        self.assertNotIn('private reasoning', str(requests))

    def test_persistent_length_reports_attempted_budgets_without_private_reasoning(self):
        with self.assertRaises(RuntimeError) as raised:
            self.call([self.reply('部分答案', 'length')], thinking=True)
        self.assertIn('2600 → 65536', str(raised.exception))
        self.assertEqual(raised.exception.diagnostics['attempted_output_budgets'], [2600, 65536])
        self.assertNotIn('private reasoning', str(raised.exception.diagnostics))

    def test_manual_context_cap_is_retained_in_the_reasoning_retry(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=self.reply('未完成', 'length'))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.llm.httpx.AsyncClient', return_value=client):
            with self.assertRaises(RuntimeError):
                asyncio.run(answer_question('核对题图', [], {'base_url': 'https://api.deepseek.com',
                    'model': 'deepseek-flash', 'context_window': 32768, 'max_tokens': 3000}))
        self.assertEqual([r['max_tokens'] for r in requests], [24576, 24576])

    def test_persistent_empty_is_an_error_not_none_or_success(self):
        with self.assertRaisesRegex(RuntimeError, '空的正文'):
            self.call([self.reply(None)])

    def test_truncated_text_is_not_accepted_as_complete(self):
        with self.assertRaisesRegex(RuntimeError, '长度上限'):
            self.call([self.reply('截断的答案', 'length')])

    def test_other_providers_do_not_receive_deepseek_parameters(self):
        result, requests = self.call([self.reply('正常正文')], base_url='https://other.example/v1', thinking=False)
        self.assertEqual(result, '正常正文')
        self.assertNotIn('thinking', requests[0])

    def test_quiz_json_mode_is_opt_in_and_only_sent_to_supported_provider(self):
        _, structured = self.call([self.reply('{"questions":[]}')], thinking=False, json_mode=True)
        self.assertEqual(structured[0]['response_format'], {'type': 'json_object'})
        _, ordinary = self.call([self.reply('普通回答')])
        self.assertNotIn('response_format', ordinary[0])
        _, other = self.call([self.reply('{"questions":[]}')], base_url='https://other.example/v1', json_mode=True)
        self.assertNotIn('response_format', other[0])


if __name__ == '__main__':
    unittest.main()
