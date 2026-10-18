import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.llm import cited_sources, select_evidence
from app.retrieval import BM25Index, quoted_evidence


class EvidenceTests(unittest.TestCase):
    def source(self):
        return {'index': 1, 'document_id': 'chosen-course', 'document_name': '网桥.pdf',
                'page': 25, 'text': '记录源地址和进入端口\n按目的地址查表转发',
                'lines': [{'page': 25, 'text': '记录源地址和进入端口', 'bbox': [0, 10, 100, 20]},
                          {'page': 25, 'text': '按目的地址查表转发', 'bbox': [0, 30, 100, 40]}]}

    def test_legacy_cross_page_chunks_become_real_pages_without_overlap(self):
        first = {'id': 'old-1', 'document_id': 'course', 'document_kind': 'pdf', 'page': 1,
                 'text': '生成树\n自学习', 'lines': [
                     {'page': 1, 'text': '生成树', 'bbox': [0, 0, 10, 10]},
                     {'page': 2, 'text': '自学习', 'bbox': [0, 0, 10, 10]}]}
        second = {**first, 'id': 'old-2', 'lines': [first['lines'][1],
                  {'page': 2, 'text': '记录源地址', 'bbox': [0, 20, 10, 30]}]}
        index = BM25Index([first, second])
        result = index.search('自学习')[0].chunk
        self.assertEqual(result['page'], 2)
        self.assertEqual(result['text'], '自学习\n记录源地址')
        self.assertEqual(len(result['lines']), 2)
        self.assertEqual({l['page'] for l in result['lines']}, {2})
        self.assertEqual(first['page'], 1)

    def test_quote_selects_only_actual_source_lines(self):
        result = quoted_evidence(self.source(), '按目的地址查表转发')
        self.assertEqual(result['text'], '按目的地址查表转发')
        self.assertEqual([l['bbox'][1] for l in result['lines']], [30])
        self.assertIsNone(quoted_evidence(self.source(), '根据生成树算法学习地址'))

    def test_evidence_selector_rejects_fabricated_quotes_and_unknown_sources(self):
        reply = {'evidence': [{'source': 1, 'quote': '记录源地址和进入端口'},
                              {'source': 1, 'quote': '伪造的原文'},
                              {'source': 2, 'quote': '其他笔记本的内容'},
                              {'source': 1, 'quote': '记录源地址和进入端口'}]}
        with patch('app.llm.chat_completion', AsyncMock(return_value=json.dumps(reply))):
            selected = asyncio.run(select_evidence('网桥自学习', [self.source()],
                {'model': 'mock', 'base_url': 'https://example.invalid'}))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['document_id'], 'chosen-course')
        self.assertEqual(len(selected[0]['lines']), 1)

    def test_only_cited_sources_remain_and_numbers_are_remapped_together(self):
        answer, sources = cited_sources('先学习源地址 [3]，再转发 [3]。`array[99]`',
            [{**self.source(), 'index': i} for i in [1, 2, 3]])
        self.assertEqual(answer, '先学习源地址 [1]，再转发 [1]。`array[99]`')
        self.assertEqual([s['index'] for s in sources], [1])
        with self.assertRaisesRegex(RuntimeError, '不存在'):
            cited_sources('伪造引用 [9]', [self.source()])

    def test_repeated_diagram_identifiers_cannot_amplify_query_weight(self):
        index = BM25Index([{'id': 'a', 'document_id': 'course', 'text': '自学习 源地址'},
                           {'id': 'b', 'document_id': 'course', 'text': 'H1 H2 H3'}])
        once = [(r.chunk['id'], r.score) for r in index.search('自学习 H1')]
        repeated = [(r.chunk['id'], r.score) for r in index.search('自学习 H1 H1 H1 H1')]
        self.assertEqual(once, repeated)

    def test_cjk_citations_are_validated_without_rewriting_math_or_code(self):
        answer, sources = cited_sources(r'依据【3】，公式 \(x[99]+1\)，代码 `【7】`。',
                                        [{**self.source(), 'index': 3}])
        self.assertEqual(answer, r'依据[1]，公式 \(x[99]+1\)，代码 `【7】`。')
        self.assertEqual([s['index'] for s in sources], [1])
        with self.assertRaisesRegex(RuntimeError, '不存在'):
            cited_sources('不存在的依据【9】', [self.source()])


if __name__ == '__main__':
    unittest.main()
