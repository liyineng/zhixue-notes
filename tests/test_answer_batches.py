"""Pure parsing: full-attachment batches must never broaden scope or lose text."""
import unittest

from app.answer_batches import attachment_exercises


DEFAULT = '请解答附件中的题目，并结合选中的课程资料。'


class AttachmentExercisePlanTests(unittest.TestCase):
    def fourteen(self):
        # Selected textbook exercises can legitimately skip many numbers.
        numbers = [1, 2, 3, 4, 5, 13, 14, 18, 23, 27, 28, 30, 33, 37]
        prefix = ('附件《电磁场与电磁波_第7章指定习题_仅题目.pdf》：\n第 1 页：\n'
                  '电磁场与电磁波 · 第7章指定习题\n\n'
                  '公共前言：所有题均采用真空参数 ε₀、μ₀，角频率取题干给定值。\n\n')
        parts = [prefix]
        for number in numbers:
            parts.append(f'7.{number} 已知均匀平面波的电场，本题完整条件为唯一条件-{number}。\n'
                         rf'\[E_{{{number}}}=E_0\cos(\omega t-\beta z)\hat x\]' + '\n'
                         '(1) 试求磁场与传播方向。\n(2) 计算平均功率密度并列出所用公式。\n\n')
            if number == 13:
                parts.append('1/2\n\n第 2 页：\n电磁场与电磁波 · 第7章指定习题（续）\n\n')
        parts.append('最后补充：题末给定的单位必须保留。\n2/2\n')
        return ''.join(parts), prefix, numbers

    def test_fourteen_selected_exercises_keep_both_pages_first_last_and_every_character(self):
        text, prefix, numbers = self.fourteen()
        plan = attachment_exercises(DEFAULT, text)
        self.assertEqual([block['label'] for block in plan], [f'7.{number}' for number in numbers])
        self.assertEqual(len(plan), 14)
        self.assertIn('唯一条件-1。', plan[0]['text'])
        self.assertIn('唯一条件-37。', plan[-1]['text'])
        self.assertNotIn('唯一条件-2。', plan[0]['text'])
        self.assertNotIn('唯一条件-33。', plan[-1]['text'])
        self.assertIn('最后补充：题末给定的单位必须保留。\n2/2\n', plan[-1]['text'])
        for block in plan:
            self.assertTrue(block['text'].startswith(prefix))
            self.assertIn('公共前言：所有题均采用真空参数 ε₀、μ₀', block['text'])
            self.assertIn('(1) 试求', block['text'])
            self.assertIn('(2) 计算', block['text'])
        self.assertEqual(''.join(block['text'][len(prefix):] for block in plan), text[len(prefix):],
                         'apart from repeating shared setup, the entire original body must survive exactly')
        self.assertIn('第 2 页', next(block['text'] for block in plan if block['label'] == '7.13'))

    def test_cross_page_continuation_remains_with_its_exercise(self):
        text = ('附件题目共同前言。\n7.1 已知第一题条件。\n'
                '第 2 页：\n第一题跨页补充条件：介质介电常数为4ε₀，试求所有量。\n'
                '7.2 已知第二题条件并求解。\n7.3 已知第三题条件并求解。\n7.4 已知第四题条件并求解。\n')
        plan = attachment_exercises(DEFAULT, text)
        self.assertEqual(len(plan), 4)
        self.assertIn('第一题跨页补充条件：介质介电常数为4ε₀', plan[0]['text'])
        self.assertNotIn('第一题跨页补充条件', plan[1]['text'])

    def test_week_codes_normalize_without_dropping_brackets_unicode_hyphens_or_setup(self):
        prefix = '作业公共条件：统一使用SI单位。\n'
        text = prefix + ('【W6－04】第一道题的真实条件，试求传播方向。\n'
                         '[w6-05] 第二道题条件，试求磁场。\n'
                         'W 6 – 09 第三道题条件，试求能量。\n'
                         'W6—12 第四道题条件，试求功率。\n')
        plan = attachment_exercises('请逐题解答上传的全部作业，不要漏题。', text)
        self.assertEqual([block['label'] for block in plan], ['W6-04', 'W6-05', 'W6-09', 'W6-12'])
        self.assertIn('【W6－04】第一道题', plan[0]['text'])
        self.assertTrue(all(block['text'].startswith(prefix) for block in plan))
        self.assertEqual(''.join(block['text'][len(prefix):] for block in plan), text[len(prefix):])

    def test_top_level_heading_followed_by_word_question_is_not_another_question_reference(self):
        text = '\n'.join(f'7.{number} 题目正文：已知当前题干条件，试求全部结果。' for number in range(1, 5))
        plan = attachment_exercises(DEFAULT, text)
        self.assertEqual([block['label'] for block in plan], ['7.1', '7.2', '7.3', '7.4'])
        self.assertEqual(''.join(block['text'] for block in plan), text)

    def test_single_range_subset_and_exclusions_never_expand_to_the_whole_attachment(self):
        text, _, _ = self.fourteen()
        questions = ['请解答附件中的7.1题', '请解答附件里的7.1到7.5',
                     '只解答附件第一题', '请单独解答附件第三题', '请做附件前四道题',
                     '解答附件中的部分题目', '请解答附件全部题目，除了7.13',
                     '请解答附件第2页的所有题目', '请解答附件中标星的题目', '请做附件中的偶数题',
                     '请解答附件全部题目，跳过不熟悉的题', '解答附件，范围为1-4',
                     '只要附件题目的最终答案', '请不要做附件里的最后一道题',
                     '详细解释W6-04，结合附件', '上一条回答提到7.1，请解答附件全部题目']
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(attachment_exercises(question, text), [])

    def test_no_plan_for_ordinary_chat_summary_no_numbering_or_only_subquestions(self):
        text, _, _ = self.fourteen()
        for question in ['这些公式是什么意思', '请总结附件的知识点', '请解答这个概念', '']:
            with self.subTest(question=question):
                self.assertEqual(attachment_exercises(question, text), [])
        for source in ['请解答以下没有可靠编号的若干问题。',
                       '(1) 试求电场。\n(2) 试求磁场。\n(3) 试求功率。\n(4) 试求方向。',
                       '1. 试求电场。\n2. 试求磁场。\n3. 试求功率。\n4. 试求方向。',
                       '7.1 试求电场。\n7.2 试求磁场。\n7.3 试求功率。']:
            with self.subTest(source=source):
                self.assertEqual(attachment_exercises(DEFAULT, source), [])

    def test_duplicate_out_of_order_mixed_and_ambiguous_chapters_decline_batching(self):
        for labels in [['7.1', '7.2', '7.2', '7.4'], ['7.1', '7.01', '7.2', '7.3'],
                       ['7.1', '7.3', '7.2', '7.4'], ['7.1', '7.2', 'W6-03', 'W6-04'],
                       ['7.1', '7.2', '8.1', '8.2']]:
            text = '\n'.join(f'{label} 已知该题完整条件，试求题目所问。' for label in labels)
            with self.subTest(labels=labels):
                self.assertEqual(attachment_exercises(DEFAULT, text), [])

    def test_math_code_and_nested_ids_are_not_top_level_exercises(self):
        hidden = ('```python\n7.98 = expression\n7.99 这是代码字符串，不是题目\n```\n'
                  '~~~text\n7.77 也是代码示例\n~~~\n'
                  '\\[\n7.88 = x+y\n\\]\n'
                  '$$\n7.89 = x-y\n$$\n'
                  '\\(\n7.90 = z\n\\)\n'
                  '`7.91 这是行内代码`\n')
        text = ('附件公共前言。\n7.1 已知电场，试求磁场。\n' + hidden +
                '(1) 求幅值。\n(2) 求相位。\n'
                '7.2 已知第二题，试求方向。\n'
                '7.2.1 这是本题中的嵌套说明，不是新题。\n'
                '7.13 已知第三题，试求功率。\n7.14 已知第四题，试求阻抗。\n')
        plan = attachment_exercises(DEFAULT, text)
        self.assertEqual([block['label'] for block in plan], ['7.1', '7.2', '7.13', '7.14'])
        self.assertIn(hidden, plan[0]['text'])
        self.assertIn('7.2.1 这是本题中的嵌套说明', plan[1]['text'])
        math_only = '7.1 x=1\n7.2 y=2\n7.3 z=3\n7.4 t=4\n'
        self.assertEqual(attachment_exercises(DEFAULT, math_only), [])

    def test_history_unbalanced_markup_and_ambiguous_interstitial_shared_setup_are_rejected(self):
        base = '7.1 已知第一题条件。\n7.2 已知第二题条件。\n7.3 已知第三题条件。\n7.4 已知第四题条件。\n'
        sources = ['近期对话：\n' + base, '原回答：\n' + base,
                   '```\n' + base, '\\[\n' + base, '$$\n' + base,
                   base.replace('7.3', '以下各题共用条件：频率为10GHz。\n7.3'),
                   base.replace('已知第三题条件', '条件同上题，试求相应量'),
                   base.replace('已知第三题条件', '参照习题7.1的条件，试求相应量'),
                   '附件《甲.pdf》：\n' + base + '附件《乙.pdf》：\n' + base]
        for text in sources:
            with self.subTest(source=text[:70]):
                self.assertEqual(attachment_exercises(DEFAULT, text), [])


if __name__ == '__main__':
    unittest.main()
