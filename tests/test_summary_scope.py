import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import pymupdf
import test_workspace
from app import main
from app.llm import make_summary
from app.retrieval import summary_documents


class SummaryScopeTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def pdf(self, notebook, name, count):
        with pymupdf.open() as doc:
            for i in range(1, count + 1):
                page = doc.new_page()
                page.insert_text((72, 72), f'Topic on page {i}: Network architecture and protocols.')
            response = self.client.post('/api/documents', params={'notebook_id': notebook},
                files={'file': (name, doc.tobytes(), 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        return response.json()['document']['id']

    def test_named_summary_covers_entire_file_even_with_top_k_one(self):
        notebook = self.notebook('章节整理')
        wanted = self.pdf(notebook, 'L03-网络体系结构.pdf', 24)
        other = self.pdf(notebook, 'L04-数据链路层.pdf', 3)
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': True, 'search_query': '网络结构', 'keywords': []})
        async def summarize(contexts, settings, question=''):
            self.assertIn('L03', question)
            self.assertEqual({c['document_id'] for c in contexts}, {wanted})
            self.assertEqual([c['page'] for c in contexts], list(range(1, 25)))
            self.assertTrue(all({l['page'] for l in c['lines']} == {c['page']} for c in contexts))
            return '开篇内容 [1]，中间内容 [12]，结尾内容 [24]。'
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', side_effect=summarize):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [wanted, other],
                'question': 'L03讲了什么，整理一下', 'top_k': 1})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['summary_coverage']['pages'], 24)
        self.assertEqual(result['summary_coverage']['documents'], ['L03-网络体系结构.pdf'])
        self.assertEqual([c['page'] for c in result['citations']], [1, 12, 24])
        self.assertEqual(result['answer'], '开篇内容 [1]，中间内容 [2]，结尾内容 [3]。')
        self.assertEqual(self.client.get(f'/api/notebooks/{notebook}/chat').json()['exchanges'][0]['result']['summary_coverage']['pages'], 24)

    def test_named_file_cannot_expand_beyond_selected_sources(self):
        notebook = self.notebook('范围隔离')
        self.upload(notebook, 'L03-网络.md', 'L03独有内容')
        other = self.upload(notebook, 'L04-链路.md', 'L04独有内容')
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': True, 'search_query': 'L03', 'keywords': []})
        summarize = AsyncMock(return_value='不应该被调用')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', summarize):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [other], 'question': '整理L03'})
        self.assertEqual(response.status_code, 400, response.text)
        summarize.assert_not_called()

    def test_title_and_multiple_lesson_names_are_resolved(self):
        documents = [{'id': 'a', 'name': 'L03-网络体系结构.pdf'}, {'id': 'b', 'name': 'L04-数据链路层.pdf'}]
        self.assertEqual(summary_documents('L3讲了什么', documents), documents[:1])
        self.assertEqual(summary_documents('整理网络体系结构', documents), documents[:1])
        self.assertEqual(summary_documents('整理L03和L04', documents), documents)
        self.assertEqual(summary_documents('整理全部资料', documents), documents)

    def test_summary_payload_does_not_truncate_each_page_at_620_characters(self):
        text = '开始 ' + '课程内容 ' * 180 + '结尾独有知识点'
        completion = AsyncMock(return_value='摘要 [1]')
        with patch('app.llm.chat_completion', completion):
            asyncio.run(make_summary([{'index': 1, 'document_name': 'L03.pdf', 'page': 1, 'text': text}],
                {'base_url': 'https://example.invalid', 'model': 'test'}, question='整理L03'))
        payload = completion.call_args.kwargs
        self.assertIn('结尾独有知识点', payload['messages'][1]['content'])
        self.assertIn('整理L03', payload['messages'][1]['content'])
