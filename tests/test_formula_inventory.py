import asyncio
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pymupdf
import test_workspace
from app import db, main
from app.document_images import ensure_presentation_formulas, presentation_equation_pages
from app.llm import make_summary
from app.retrieval import is_formula_inventory


class FormulaInventoryTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_inventory_overrides_planner_and_reads_only_requested_selected_chapter(self):
        notebook = self.notebook('公式课件')
        wanted = self.upload(notebook, '第二章.md', r'谐振频率 \(f_0=1/(2\pi\sqrt{LC})\)')
        other = self.upload(notebook, '第三章.md', '其他章不应出现')
        planner = AsyncMock(return_value={'intent': 'question', 'needs_retrieval': False,
                                          'search_query': '公式', 'keywords': []})
        summary = AsyncMock(return_value=r'谐振频率：\[f_0=\frac{1}{2\pi\sqrt{LC}}\] [1]')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', summary):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [wanted, other],
                                                         'question': '第二章有哪些公式', 'top_k': 1})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['output_mode'], 'formulas')
        self.assertEqual({c['document_id'] for c in result['citations']}, {wanted})
        self.assertTrue(summary.call_args.kwargs['formula_inventory'])
        self.assertIn('只列实际公式', summary.call_args.kwargs['question'])
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'make_summary', summary):
            empty = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [],
                                                       'question': '有哪些公式'})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()['citations'], [])
        self.assertEqual(summary.await_count, 1)

    def test_formula_prompt_replaces_outline_and_requires_math_and_evidence(self):
        completion = AsyncMock(return_value='公式 [1]')
        with patch('app.llm.chat_completion', completion):
            asyncio.run(make_summary([{'index': 1, 'document_name': '第二章', 'page': 1, 'text': '公式'}],
                {'base_url': 'https://example.invalid', 'model': 'test'}, '第二章有哪些公式', formula_inventory=True))
        system = completion.call_args.kwargs['messages'][0]['content']
        self.assertIn('直接逐条给出实际数学表达式', system)
        self.assertIn(r'\frac', system)
        self.assertIn('不得用常识补写', system)
        self.assertNotIn('1. 本章摘要：', system)

    def test_inventory_does_not_match_formula_explanations(self):
        for question in ['第二章有哪些公式', '汇总全部公式', '列出本章的表达式']:
            self.assertTrue(is_formula_inventory(question), question)
        for question in ['这个公式怎么推导', '解释谐振频率公式', '整理第二章知识点', '这个公式有什么问题', '公式有哪些错误']:
            self.assertFalse(is_formula_inventory(question), question)

    def test_dense_formula_batches_keep_all_pages_and_local_citations(self):
        contexts = [{'index': i, 'document_id': 'selected', 'document_name': '第二章',
                     'page': i, 'text': '公式' * 1500} for i in range(1, 8)]
        seen = []
        async def summarize(batch, settings, question='', **kwargs):
            self.assertLessEqual(sum(len(c['text']) for c in batch), 8000)
            seen.extend(c['page'] for c in batch)
            return f"公式 [{batch[0]['index']}]"
        with patch.object(main, 'make_summary', side_effect=summarize):
            asyncio.run(main._generate_summary(contexts, {}, '有哪些公式', 'test-missing',
                                               [{'id': 'selected', 'name': '第二章'}], formula_inventory=True))
        self.assertEqual(seen, list(range(1, 8)))

    def test_empty_timeout_messages_retain_failure_stage_for_ask_and_summary(self):
        notebook = self.notebook('错误详情')
        document = self.upload(notebook, '资料.md', '公式名称和表达式')
        for endpoint, target in [('/api/ask', 'analyze_question'), ('/api/summary', 'make_summary')]:
            with patch.object(main, target, AsyncMock(side_effect=TimeoutError())):
                response = self.client.post(endpoint, json={'notebook_id': notebook, 'document_ids': [document],
                                                            'question': '解释公式'})
            self.assertEqual(response.status_code, 502, response.text)
            self.assertIn('超时', response.json()['detail'])
            exchange = db.list_exchanges(notebook)[-1]
            self.assertEqual(exchange['status'], 'error')
            self.assertTrue(exchange['result']['failed_progress']['steps'])
            self.assertEqual(exchange['result']['error_type'], 'TimeoutError')

    def test_unread_formula_pages_are_reported_even_if_model_omits_warning(self):
        notebook = self.notebook('未读公式')
        document = self.upload(notebook, '第二章.md', '公式名称')
        with patch.object(main, 'analyze_question', AsyncMock(return_value={
                'intent': 'summary', 'needs_retrieval': True, 'search_query': '公式', 'keywords': []})), \
             patch.object(main, '_ensure_source_images', AsyncMock(return_value=[{'document_name': '第二章.md', 'pages': [3, 5]}])), \
             patch.object(main, 'make_summary', AsyncMock(return_value='公式名称 [1]')):
            response = self.client.post('/api/ask', json={'notebook_id': notebook, 'document_ids': [document],
                                                         'question': '第二章有哪些公式'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('3, 5 页', response.json()['answer'])
        self.assertIn('不保证完整', response.json()['answer'])

    def test_presentation_formula_cache_retains_original_chunks_and_retries_uncertain_pages(self):
        notebook = self.notebook('对象公式')
        identity = self.upload(notebook, '第二章.md', '原来的文字与引用')
        document = {**db.get_document(identity), 'kind': 'pptx'}
        original = db.document_chunk_ids(identity)
        pdf_path = Path(self.temp.name) / 'preview.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page(); pdf.new_page(); pdf.save(pdf_path)
        recognizer = AsyncMock(side_effect=[(r'\[Q=\frac{\omega_0 L}{R}\]', 'vision'), ('[看不清]', 'vision')])
        progress = []
        with patch('app.document_images.presentation_equation_pages', return_value=[1, 2]), \
             patch('app.document_images.presentation_pdf', return_value=pdf_path), \
             patch('app.document_images.read_question', recognizer):
            report = asyncio.run(ensure_presentation_formulas(document, {'vision_model': 'test'}, progress.append))
        self.assertEqual(report, {'added_pages': [1], 'unread_pages': [2]})
        self.assertEqual(progress, [1, 2])
        self.assertTrue(original.issubset(db.document_chunk_ids(identity)))
        supplement = next(c for c in db.all_chunks() if c['id'].startswith('formulas-v1-'))
        self.assertEqual(supplement['page'], 1)
        self.assertEqual(supplement['lines'][0]['image_recognition'], 'vision')
        with patch('app.document_images.presentation_equation_pages', return_value=[1, 2]), \
             patch('app.document_images.presentation_pdf') as preview:
            report = asyncio.run(ensure_presentation_formulas(document, {}))
        self.assertEqual(report['unread_pages'], [2])
        preview.assert_not_called()

    def test_equation_page_detection_includes_unanchored_equations_excludes_diagrams(self):
        path = Path(self.temp.name) / 'objects.pptx'
        with zipfile.ZipFile(path, 'w') as archive:
            for page, program in [(1, 'Word.Picture.8'), (2, 'Equation.3'), (3, 'MathType.7')]:
                archive.writestr(f'ppt/slides/slide{page}.xml', f'<slide><oleObj progId="{program}"/></slide>')
        self.assertEqual(presentation_equation_pages(path), [2, 3])
