# -*- coding: utf-8 -*-
"""compiler 模块单元测试：python -m unittest -v"""
import unittest

from compiler import KnitError, compile_rows, expand, parse_row


def run(cast_on, *lines):
    return compile_rows(cast_on, list(lines))


def ok_rows(result):
    return [r for r in result['rows'] if r['status'] == 'ok']


class TestBasicStitches(unittest.TestCase):
    def test_kp_passthrough(self):
        r = run(4, 'k k p k')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual((row['in'], row['out']), (4, 4))
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'k', 'p', 'k'])

    def test_yo_consumes_nothing(self):
        r = run(2, 'k yo k')
        row = r['rows'][0]
        self.assertEqual((row['in'], row['out']), (2, 3))

    def test_k2tog_decreases(self):
        r = run(4, 'k2tog k2tog')
        row = r['rows'][0]
        self.assertEqual((row['in'], row['out']), (4, 2))

    def test_row_chain_derives_next_input(self):
        # 第 1 行 4→3，第 2 行必须以 3 针为输入
        r = run(4, 'k k k2tog', 'k yo k k')
        rows = r['rows']
        self.assertEqual(rows[0]['out'], 3)
        self.assertEqual(rows[1]['in'], 3)
        self.assertEqual(rows[1]['out'], 4)

    def test_case_and_fullwidth(self):
        r = run(2, '（K，Ｐ）x1')
        self.assertEqual(r['rows'][0]['status'], 'ok')

    def test_digit_suffix_rejected(self):
        r = run(9, 'k3')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 2)
        self.assertIn('(k)x3', err['message'])


class TestGroups(unittest.TestCase):
    def test_simple_group(self):
        r = run(6, '(k p)x3')
        row = r['rows'][0]
        self.assertEqual(row['out'], 6)
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'p'] * 3)

    def test_nested_group(self):
        # (k (p yo)x2)x2：内层 耗1产2，x2 → 耗2产4；外层 耗3产5，x2 → 耗6产10
        r = run(6, '(k (p yo)x2)x2')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 10)

    def test_group_repeat_maps_to_same_source(self):
        r = run(4, '(k2tog)x2')
        spans = [(s['s'], s['e']) for s in r['rows'][0]['stitches']]
        self.assertEqual(spans, [(1, 6), (1, 6)])  # 两次展开都映射回同一源码段

    def test_unclosed_paren(self):
        r = run(4, '(k p')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)
        self.assertIn("')'", err['message'])

    def test_stray_rparen(self):
        r = run(4, 'k p)')
        self.assertEqual(r['rows'][0]['error']['col'], 4)

    def test_stray_x(self):
        r = run(4, 'k x3 p')
        self.assertEqual(r['rows'][0]['error']['col'], 3)


class TestStarRepeat(unittest.TestCase):
    def test_star_fills_to_end(self):
        # 10 针：k4 后余 6，*yo k2tog* 单次耗 2 → 重复 3 次
        r = run(10, 'k k k k *yo k2tog*')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 4 + 3 * 2)

    def test_star_whole_row(self):
        r = run(12, '*k k p p*')
        row = r['rows'][0]
        self.assertEqual(row['out'], 12)

    def test_star_not_divisible_reports_remaining(self):
        # 10 针，单次耗 3 → 10 % 3 = 1，余针 10
        r = run(10, '*k2tog k*')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 10)
        self.assertIn('不能整除', err['message'])
        self.assertEqual(err['col'], 1)

    def test_star_zero_consumption_rejected(self):
        r = run(6, '*yo*')
        err = r['rows'][0]['error']
        self.assertIn('单次消耗必须为正', err['message'])
        self.assertEqual(err['remaining'], 6)

    def test_star_must_be_at_end(self):
        r = run(6, '*k2tog* k')
        self.assertIn('行末', r['rows'][0]['error']['message'])

    def test_star_cannot_nest_in_group(self):
        r = run(6, '(*k2tog*)x2')
        self.assertIn('行末', r['rows'][0]['error']['message'])

    def test_unclosed_star(self):
        r = run(6, '*k2tog')
        self.assertIn("'*'", r['rows'][0]['error']['message'])


class TestStitchAccounting(unittest.TestCase):
    def test_leftover_stitches_error(self):
        # 5 针只织了 3 针 → 余 2，定位行尾
        r = run(5, 'k k k')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 2)
        self.assertIn('余 2 针', err['message'])
        self.assertEqual(err['col'], 5)

    def test_insufficient_stitches_error(self):
        # 只剩 1 针时遇到 k2tog（需 2 针）
        r = run(3, 'k k k2tog')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 1)
        self.assertEqual(err['col'], 5)
        self.assertIn('针数不足', err['message'])

    def test_empty_line_errors_when_stitches_remain(self):
        r = run(2, '')
        self.assertEqual(r['rows'][0]['error']['remaining'], 2)

    def test_error_blocks_following_rows(self):
        r = run(4, 'k k k k', 'k k', 'k k k k')
        rows = r['rows']
        self.assertEqual(rows[0]['status'], 'ok')
        self.assertEqual(rows[1]['status'], 'error')
        self.assertEqual(rows[2]['status'], 'blocked')

    def test_recompute_after_fix(self):
        # 修正中间行后，后续行按新针数继续推导
        r = run(4, 'k k k k', 'k k k k', 'k2tog k2tog')
        self.assertTrue(all(row['status'] == 'ok' for row in r['rows']))
        self.assertEqual(r['rows'][2]['out'], 2)


class TestValidation(unittest.TestCase):
    def test_bad_cast_on(self):
        with self.assertRaises(ValueError):
            compile_rows(0, ['k'])
        with self.assertRaises(ValueError):
            compile_rows('24', ['k'])

    def test_expand_position_is_one_based(self):
        # 第 2 个记号处出错，列号应为该记号起始列（1 基）
        ast = parse_row('k k2tog', 1)
        with self.assertRaises(KnitError) as ctx:
            expand(ast, 1, 1)
        self.assertEqual(ctx.exception.col, 3)


if __name__ == '__main__':
    unittest.main()
