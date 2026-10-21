import unittest
from unittest.mock import AsyncMock, patch
import httpx
from app import model_context as context
from app.conversation import recent_context


class ModelContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        context._cache.clear()

    async def test_metadata_capacity_cache_and_manual_override(self):
        settings = {'base_url': 'https://example.test/v1', 'model': 'course-model', 'api_key': 'secret'}
        response = httpx.Response(200, json={'data': [{'id': 'other', 'context_window': 9},
            {'id': 'course-model', 'context_length': 128000, 'max_output_tokens': 16000}]},
            request=httpx.Request('GET', 'https://example.test/v1/models'))
        client = AsyncMock()
        client.get.return_value = response
        with patch.object(context.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            info = await context.resolve_context(settings)
            self.assertEqual(info['context_window'], 128000)
            self.assertEqual(info['source'], 'provider_api')
            self.assertEqual(await context.resolve_context(settings), info)
            client.get.assert_awaited_once_with('https://example.test/v1/models', headers={'Authorization': 'Bearer secret'})
            manual = await context.resolve_context({**settings, 'context_window': 64000})
            self.assertEqual(manual, {'context_window': 64000, 'source': 'manual'})

    async def test_unavailable_metadata_falls_back_without_breaking_questions(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError('offline')
        with patch.object(context.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            info = await context.resolve_context({'base_url': 'https://example.test/v1', 'model': 'unknown'})
        self.assertEqual(info['source'], 'fallback')
        self.assertEqual(info['context_window'], context.FALLBACK_WINDOW)

    def test_capacity_reserves_current_input_evidence_and_output(self):
        settings = {'max_tokens': 4000}
        info = {'context_window': 64000}
        budget = context.history_budget(info, settings, '当前问题', 12000)
        self.assertEqual(budget, 64000 - 4000 - 12000 - 8192 - len('当前问题'.encode()))
        self.assertEqual(context.history_budget({'context_window': 100}, settings, '问题', 12000), 0)

    def test_reasoning_budgets_and_history_reserve_follow_same_bounded_policy(self):
        settings = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'max_tokens': 3000}
        self.assertEqual(context.answer_output_budgets(settings), (32768, 65536))
        expected = 1048576 - 65536 - 12000 - len('问题'.encode()) - 8192
        self.assertEqual(context.history_budget(context.context_info(settings), settings, '问题', 12000), expected)
        self.assertEqual(context.answer_output_budgets({**settings, 'max_tokens': 100000}), (100000, 100000))
        self.assertEqual(context.answer_output_budgets({**settings, 'max_tokens': 500000}), (393216, 393216))
        self.assertEqual(context.answer_output_budgets({**settings, 'context_window': 32768}), (24576, 24576))
        self.assertEqual(context.answer_output_budgets({'base_url': 'https://other.invalid', 'model': 'test', 'max_tokens': 3000}), (3000, 3000))

    def test_history_grows_with_model_budget_and_keeps_newest_first(self):
        exchanges = [{'question': f'问题{i}', 'result': {'answer': '答案' * 1000 + str(i)}} for i in range(10)]
        full, count = recent_context(exchanges, 100000)
        self.assertEqual(count, 10)
        self.assertNotIn('上下文已截短', full)
        short, count = recent_context(exchanges, 7000)
        self.assertEqual(count, 1)
        self.assertIn('问题9', short)
        self.assertNotIn('问题8', short)
        for budget in (0, 50, 100, 101, 200, 7000):
            text, _ = recent_context(exchanges, budget)
            self.assertLessEqual(context.estimate_tokens(text), budget)
