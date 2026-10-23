import asyncio
import json
import ssl
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.llm import ModelConnectionError, OutputTruncatedError, _connection_diagnostic, chat_completion


REAL_ASYNC_CLIENT = httpx.AsyncClient


class CompletionTests(unittest.TestCase):
    def network_call(self, failures, *, status=200):
        attempts = []
        def handle(request):
            attempts.append(request)
            if len(attempts) <= failures:
                raise httpx.ReadTimeout('', request=request)
            return httpx.Response(status, json=self.reply('完整回答'))
        def factory(**_options):
            return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
        with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', AsyncMock()):
            result = asyncio.run(chat_completion(base_url='https://example.invalid', api_key='test',
                model='mock', messages=[], temperature=0, max_tokens=100))
        return result, len(attempts)

    def test_transient_timeout_retries_once_and_persistent_timeout_has_a_message(self):
        self.assertEqual(self.network_call(1), ('完整回答', 2))
        with self.assertRaisesRegex(RuntimeError, '请求超时'):
            self.network_call(2)

    def test_gateway_failure_retries_but_authentication_failure_does_not(self):
        for status, expected in [(429, 2), (502, 2), (503, 2), (504, 2), (401, 1), (403, 1)]:
            attempts = []
            def handle(request):
                attempts.append(request)
                return httpx.Response(status, text='upstream error')
            clients = []
            def factory(**_options):
                client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
                clients.append(client)
                return client
            with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', AsyncMock()):
                with self.assertRaisesRegex(RuntimeError, f'HTTP {status}'):
                    asyncio.run(chat_completion(base_url='https://example.invalid', api_key='test',
                        model='mock', messages=[], temperature=0, max_tokens=100))
            self.assertEqual(len(attempts), expected)
            self.assertEqual(len(clients), expected)
            self.assertTrue(all(client.is_closed for client in clients))

    def call(self, replies, *, base_url='https://api.deepseek.com', thinking=None, json_mode=False):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=replies[min(len(requests) - 1, len(replies) - 1)])

        def factory(**_options):
            return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
        with patch('app.llm.httpx.AsyncClient', side_effect=factory):
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

    def test_persistent_empty_is_an_error_not_none_or_success(self):
        with self.assertRaisesRegex(RuntimeError, '空的正文'):
            self.call([self.reply(None)])

    def test_truncated_text_is_not_accepted_as_complete(self):
        with self.assertRaisesRegex(RuntimeError, '长度上限'):
            self.call([self.reply('截断的答案', 'length')])

    def test_extraction_reports_truncation_even_when_json_looks_complete(self):
        with self.assertRaises(OutputTruncatedError):
            self.call([self.reply('{"evidence":[]}', 'length')], thinking=False,
                      json_mode=True)

    def test_truncation_without_content_still_has_a_typed_error(self):
        with self.assertRaises(OutputTruncatedError):
            self.call([{'choices': [{'message': {}, 'finish_reason': 'length'}]}],
                      thinking=False)

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

    def test_transport_failures_use_new_closed_clients_before_success(self):
        attempts, clients = [], []
        def handle(request):
            attempts.append(request)
            if len(attempts) == 1:
                raise httpx.ReadError('temporary read failure', request=request)
            if len(attempts) == 2:
                raise httpx.RemoteProtocolError('temporary response disconnect', request=request)
            return httpx.Response(200, json=self.reply('重连后的完整结果'))
        def factory(**_options):
            client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(client)
            return client
        sleeper = AsyncMock()
        with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', sleeper):
            result = asyncio.run(chat_completion(base_url='https://example.invalid', api_key='fake-key',
                model='mock', messages=[], temperature=0, max_tokens=100))
        self.assertEqual(result, '重连后的完整结果')
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len({id(client) for client in clients}), 3)
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual([call.args[0] for call in sleeper.await_args_list], [.5, 2])

    def test_persistent_transport_failure_is_typed_and_sanitized_after_four_attempts(self):
        secret = 'FAKE_SECRET_DO_NOT_LOG'
        attempts, clients = [], []
        def handle(request):
            attempts.append(request)
            raise httpx.RemoteProtocolError(f'upstream disconnected Authorization: Bearer {secret}', request=request)
        def factory(**_options):
            client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(client)
            return client
        sleeper = AsyncMock()
        with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', sleeper):
            with self.assertRaises(ModelConnectionError) as raised:
                asyncio.run(chat_completion(base_url='https://example.invalid', api_key=secret,
                    model='mock', messages=[], temperature=0, max_tokens=100))
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertIsInstance(raised.exception.__cause__, httpx.RemoteProtocolError)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len({id(client) for client in clients}), 4)
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual([call.args[0] for call in sleeper.await_args_list], [.5, 2, 5])
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn('Authorization', str(raised.exception))
        self.assertNotIn('upstream disconnected', str(raised.exception))
        self.assertNotIn('example.invalid', str(raised.exception))
        self.assertIn('RemoteProtocolError', str(raised.exception))
        self.assertIn('4', str(raised.exception))

    def test_timeout_remains_two_attempts_with_closed_clients(self):
        attempts, clients = [], []
        def handle(request):
            attempts.append(request)
            raise httpx.ReadTimeout('fake sensitive timeout context', request=request)
        def factory(**_options):
            client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(client)
            return client
        with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, '请求超时'):
                asyncio.run(chat_completion(base_url='https://example.invalid', api_key='fake-key',
                    model='mock', messages=[], temperature=0, max_tokens=100))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(clients), 2)
        self.assertTrue(all(client.is_closed for client in clients))

    def test_cancelling_during_backoff_stops_further_attempts(self):
        attempts, clients = [], []
        def handle(request):
            attempts.append(request)
            raise httpx.ReadError('temporary connection error', request=request)
        def factory(**_options):
            client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(client)
            return client
        async def scenario():
            sleeping, gate = asyncio.Event(), asyncio.Event()
            async def backoff(_delay):
                sleeping.set()
                await gate.wait()
            with patch('app.llm.httpx.AsyncClient', side_effect=factory), patch('app.llm.asyncio.sleep', side_effect=backoff):
                task = asyncio.create_task(chat_completion(base_url='https://example.invalid', api_key='fake-key',
                    model='mock', messages=[], temperature=0, max_tokens=100))
                await asyncio.wait_for(sleeping.wait(), timeout=2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                gate.set()
        asyncio.run(scenario())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].is_closed)

    def test_connection_diagnostic_keeps_numeric_nested_causes_and_excludes_sensitive_context(self):
        secret = 'FAKE_NETWORK_DIAGNOSTIC_SECRET'
        sensitive = f'raw private message Authorization: Bearer {secret} https://example.invalid/?key={secret}'
        certificate = ssl.SSLCertVerificationError(1, sensitive)
        certificate.verify_code = 18
        certificate.verify_message = sensitive
        eof = ssl.SSLEOFError(8, sensitive)
        network = OSError(61, sensitive)
        nested = ExceptionGroup(sensitive, [network, ExceptionGroup(sensitive, [eof, certificate])])
        request = httpx.Request('POST', f'https://example.invalid/?key={secret}',
                                headers={'Authorization': f'Bearer {secret}', 'X-Private': sensitive})
        outer = httpx.ConnectError(sensitive, request=request)
        outer.__cause__ = nested
        diagnostic = _connection_diagnostic(outer)
        self.assertTrue(any(item.get('errno') == 61 for item in diagnostic))
        self.assertTrue(any(item.get('type') == 'SSLEOFError' and item.get('errno') == 8 for item in diagnostic))
        self.assertTrue(any(item.get('type') == 'SSLCertVerificationError' and item.get('verify_code') == 18
                            for item in diagnostic))
        self.assertGreaterEqual(sum(item['type'] == 'ExceptionGroup' for item in diagnostic), 2)
        self.assertTrue(all(set(item).issubset({'type', 'errno', 'verify_code'}) for item in diagnostic))
        serialized = json.dumps(diagnostic)
        for private in (secret, sensitive, 'Authorization', 'Bearer', 'X-Private', 'https://', 'example.invalid', 'headers'):
            self.assertNotIn(private, serialized)


if __name__ == '__main__':
    unittest.main()
