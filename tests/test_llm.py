import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.llm import chat_completion


class CompletionTests(unittest.TestCase):
    def call(self, replies, *, base_url='https://api.deepseek.com', thinking=None):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=replies[min(len(requests) - 1, len(replies) - 1)])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.llm.httpx.AsyncClient', return_value=client):
            result = asyncio.run(chat_completion(base_url=base_url, api_key='test-key',
                model='deepseek-flash', messages=[{'role': 'user', 'content': '题目'}],
                temperature=0, max_tokens=2600, thinking=thinking))
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


if __name__ == '__main__':
    unittest.main()
