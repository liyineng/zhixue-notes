import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main
from app.summaries import summary_batches, SUMMARY_BATCH_CHARS


class SummaryBatchTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_partition_keeps_every_page_and_does_not_mix_sources(self):
        contexts = [{'index': i, 'document_id': document, 'text': f'unique-{i} ' + '资料' * 5000}
                    for document, indices in [('first', range(1, 7)), ('last', range(7, 10))] for i in indices]
        batches = summary_batches(contexts)
        self.assertGreater(len(batches), 2)
        self.assertEqual([c['index'] for batch in batches for c in batch], list(range(1, 10)))
        self.assertTrue(all(len({c['document_id'] for c in batch}) == 1 for batch in batches))
        self.assertTrue(all(sum(len(c['text']) for c in batch) <= SUMMARY_BATCH_CHARS for batch in batches))
        oversized = {'index': 1, 'document_id': 'huge', 'text': 'a' * (SUMMARY_BATCH_CHARS + 1)}
        self.assertEqual(summary_batches([oversized]), [[oversized]])

    def test_each_source_gets_its_own_generation_and_persisted_coverage_for_both_endpoints(self):
        notebook = self.notebook('逐份整理')
        docs = [self.upload(notebook, f'L0{i}-课程.md', f'课程{i}独有内容。') for i in (1, 2)]
        db.update_settings({'model': 'mock', 'base_url': 'https://example.invalid'})
        observed = []
        async def summarize(contexts, settings, question=''):
            self.assertEqual(len({c['document_id'] for c in contexts}), 1)
            observed.append(contexts[0]['document_id'])
            self.assertIn('本次仅整理', question)
            # CJK numbering must remain bound to the global input indices between batches.
            return f"课程独有重点【{contexts[0]['index']}】。"
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': True, 'search_query': '', 'keywords': []})
        with patch.object(main, 'make_summary', side_effect=summarize), patch.object(main, 'analyze_question', planner):
            for endpoint in ('/api/summary', '/api/ask'):
                response = self.client.post(endpoint, json={'notebook_id': notebook, 'document_ids': docs, 'question': '整理全部资料'})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertIn('# L01-课程.md', result['answer'])
                self.assertIn('# L02-课程.md', result['answer'])
                self.assertEqual(len(result['summary_coverage']['document_details']), 2)
                self.assertTrue(all(d['citations'] == 1 for d in result['summary_coverage']['document_details']))
                self.assertEqual(len(result['summary_coverage']['cited_documents']), 2)
                self.assertEqual(db.get_exchange(notebook, result['chat_id'])['result']['summary_coverage'], result['summary_coverage'])
        self.assertEqual(observed, docs * 2)

    def test_long_single_file_batches_include_the_middle_and_tail(self):
        notebook = self.notebook('长课件')
        document = self.upload(notebook, '长课件.md', '临时内容。')
        contexts = [{'index': i, 'document_id': document, 'document_name': '长课件.md',
                     'document_kind': 'md', 'page': i, 'text': f'第{i}页独有知识点' + '资料' * 5000, 'lines': []} for i in range(1, 7)]
        seen = []
        async def summarize(batch, settings, question=''):
            seen.extend(c['page'] for c in batch)
            return '本部分重点。' + ''.join(f"[{c['index']}]" for c in batch)
        with patch.object(main, '_summary_contexts', return_value=contexts), patch.object(main, 'make_summary', side_effect=summarize):
            result = self.client.post('/api/summary', json={'notebook_id': notebook, 'document_ids': [document]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(seen, list(range(1, 7)))
        self.assertEqual(result.json()['summary_coverage']['document_details'][0]['read_pages'], 6)
        self.assertEqual(result.json()['summary_coverage']['document_details'][0]['cited_pages'], 6)

    def test_batch_cannot_cite_another_file_or_silently_omit_its_sources(self):
        notebook = self.notebook('引用隔离')
        docs = [self.upload(notebook, f'L0{i}.md', f'课程{i}独有内容。') for i in (1, 2)]
        db.update_settings({'model': 'mock', 'base_url': 'https://example.invalid'})
        for answers in (['第一份[1]', '错误沿用第一份[1]'], ['第一份[1]', '没有任何引用']):
            with patch.object(main, 'make_summary', AsyncMock(side_effect=answers)):
                response = self.client.post('/api/summary', json={'notebook_id': notebook, 'document_ids': docs})
            self.assertEqual(response.status_code, 502)
            exchange = db.get_exchange(notebook, response.headers['X-Chat-Id'])
            self.assertEqual(exchange['status'], 'error')
