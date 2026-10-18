import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import db, main
from app.retrieval import summary_documents, named_question_documents


class ChapterScopeTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_chinese_chapter_and_summary_focus_only_read_the_requested_lesson(self):
        notebook = self.notebook('中文章节')
        first = self.upload(notebook, 'L01-组成与分类.md', '第一讲独有内容，分组交换与网络分类。')
        second = self.upload(notebook, 'L02-性能与需求.md', '第二讲独有内容，时延与性能。')
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': True, 'search_query': '全部章节', 'keywords': []})
        for endpoint, extra, wanted, name in [('/api/ask', {'question': '整理第一章'}, first, 'L01-组成与分类.md'),
                                              ('/api/summary', {'focus': '第一章'}, first, 'L01-组成与分类.md'),
                                              ('/api/ask', {'question': '第二章讲了什么'}, second, 'L02-性能与需求.md')]:
            async def summarize(contexts, settings, question=''):
                self.assertEqual({c['document_id'] for c in contexts}, {wanted})
                return f"该章重点 [{contexts[0]['index']}]"
            with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', side_effect=summarize) as summarizer:
                response = self.client.post(endpoint, json={'notebook_id': notebook, 'document_ids': [first, second], **extra})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(summarizer.call_count, 1)
            result = response.json()
            self.assertEqual(result['summary_coverage']['selected_documents'], [name])
            self.assertEqual({c['document_id'] for c in result['citations']}, {wanted})
            self.assertEqual(db.get_exchange(notebook, result['chat_id'])['result']['summary_coverage']['selected_documents'], [name])

    def test_missing_or_unrecognized_chapter_never_falls_back_to_all_sources(self):
        notebook = self.notebook('章节范围')
        first = self.upload(notebook, 'L01.md', '第一讲资料。')
        second = self.upload(notebook, 'L02.md', '第二讲资料。')
        planner = AsyncMock(return_value={'intent': 'summary', 'needs_retrieval': True, 'search_query': '', 'keywords': []})
        for question, selected in [('整理第一章', [second]), ('整理第九章', [first, second]), ('整理第壹章', [first, second])]:
            summarizer = AsyncMock()
            with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', summarizer):
                response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': selected, 'question': question})
            self.assertEqual(response.status_code, 400, response.text)
            summarizer.assert_not_called()

    def test_ordinals_ranges_and_explicit_chapter_filenames(self):
        documents = [{'id': str(i), 'name': f'L{i:02d}-课程.pdf'} for i in (1, 2, 3, 11, 21)]
        for question, indices in [('整理第一章', [0]), ('整理第1章', [0]), ('整理第十一章', [3]),
                                  ('整理第二十一讲', [4]), ('整理第一至第三章', [0, 1, 2]),
                                  ('整理前两章', [0, 1]), ('整理第一章和第二章', [0, 1])]:
            self.assertEqual(summary_documents(question, documents), [documents[i] for i in indices])
        self.assertEqual(summary_documents('整理第八章', documents), [])
        explicit = {'id': 'native', 'name': '第一章-基础.pdf'}
        self.assertEqual(named_question_documents('第一章讲了什么', [*documents, explicit]), [explicit])
        self.assertEqual(summary_documents('整理全部来源', documents), documents)
