# -*- coding: utf-8 -*-
"""compiler 模块单元测试：python -m unittest -v"""
import unittest

from compiler import KnitError, compile_rows, expand, parse_row


def run(cast_on, *lines):
    return compile_rows(cast_on, list(lines))


def run_defs(cast_on, defs, *lines):
    return compile_rows(cast_on, list(lines), list(defs))


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


class TestSnippets(unittest.TestCase):
    def test_basic_ref_expands_in_place(self):
        r = run_defs(4, ['edge = k k'], '@edge p p')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual([s['sym'] for s in row['stitches']],
                         ['k', 'k', 'p', 'p'])

    def test_ref_locates_both_ends(self):
        # 定义 "edge = k k"（0 基列 0..7）：k 在定义第 8、10 列（1 基）；
        # 行内 "@edge" 占第 1–5 列
        r = run_defs(2, ['edge = k k'], '@edge')
        st0 = r['rows'][0]['stitches'][0]
        self.assertEqual(st0['def']['name'], 'edge')
        self.assertEqual((st0['def']['s'], st0['def']['e']), (7, 8))
        self.assertEqual((st0['rs'], st0['re']), (0, 5))

    def test_same_snippet_multiple_refs_not_confused(self):
        r = run_defs(6, ['e = k k'], '@e p p @e')
        spans = [(s['rs'], s['re']) for s in r['rows'][0]['stitches']]
        # 两次 @e：0 基列 0..2 与 7..9（半开区间）
        self.assertEqual(spans[0:2], [(0, 2), (0, 2)])
        self.assertEqual(spans[4:6], [(7, 9), (7, 9)])
        # 实际针法都映射回同一定义列
        defs = [(s['def']['s'], s['def']['e'])
                for s in r['rows'][0]['stitches'] if s['def']]
        self.assertEqual(defs, [(4, 5), (6, 7), (4, 5), (6, 7)])

    def test_forward_and_mutual_refs(self):
        defs = ['a = k @b', 'b = p p']
        r = run_defs(3, defs, '@a')
        self.assertEqual([s['sym'] for s in r['rows'][0]['stitches']],
                         ['k', 'p', 'p'])

    def test_group_count_uses_actual_snippet_consumption(self):
        # b = p yo（耗1产2），a = k @b（耗2产3），(a)x2 → 耗4产6
        r = run_defs(4, ['b = p yo', 'a = k @b'], '(@a)x2')
        row = r['rows'][0]
        self.assertEqual((row['in'], row['out']), (4, 6))

    def test_star_repeat_count_uses_snippet_consumption(self):
        # u = k2tog yo：单次耗 2 产 2；k k 后余 10 → 重复 5 次，出 12
        r = run_defs(12, ['u = k2tog yo'], 'k k *@u*')
        row = r['rows'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['out'], 12)

    def test_star_not_divisible_with_snippet_reports_remaining(self):
        r = run_defs(11, ['u = k2tog yo'], 'k k *@u*')
        err = r['rows'][0]['error']
        self.assertEqual(err['remaining'], 9)
        self.assertIn('不能整除', err['message'])

    def test_star_inside_snippet_definition_rejected(self):
        # 片段被引用时才报错，且定位到行内引用
        r = run_defs(4, ['a = k *p*'], '@a k')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)              # @a 的起始列
        self.assertEqual(err['loc']['source'], 'def')
        self.assertEqual(err['loc']['name'], 'a')
        self.assertIn('行末重复', err['message'])

    def test_missing_ref_localized_at_use_site(self):
        r = run_defs(4, [], 'k @nope k')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 3)
        self.assertIn('nope', err['message'])

    def test_transitive_missing_ref_names_chain(self):
        # 主定位在本行最外层引用 @a（第 1 列）；loc 指向父定义 a 里的坏 @b
        # （"a = k @b" 中 @b 起始于第 7 列，1 基）
        r = run_defs(2, ['a = k @b'], '@a')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 1)
        self.assertIn('@b', err['message'])
        self.assertIn('@a', err['message'])
        self.assertEqual(err['loc'],
                         {'source': 'dref', 'name': 'a', 'line': 1, 'col': 7})

    def test_circular_ref_localized_with_involved_defs(self):
        r = run_defs(4, ['a = k @b', 'b = @a'], '@a')
        err = r['rows'][0]['error']
        self.assertIn('循环', err['message'])
        self.assertIn('a', err['message'])
        self.assertIn('b', err['message'])
        # loc 回到行内第一个 @a（闭环处）
        self.assertEqual(err['loc'], {'source': 'row', 'row': 1, 'col': 1})

    def test_self_reference_is_cycle(self):
        r = run_defs(2, ['a = k @a'], '@a')
        self.assertIn('循环', r['rows'][0]['error']['message'])

    def test_unused_broken_definition_keeps_rows_ok(self):
        r = run_defs(4, ['bad = (k p', '没有等号的一行'], 'k k p p')
        self.assertEqual(r['rows'][0]['status'], 'ok')
        bad = next(d for d in r['defs'] if d['name'] == 'bad')
        self.assertEqual(bad['status'], 'error')
        self.assertFalse(bad['used'])

    def test_definition_used_flag(self):
        r = run_defs(4,
                     ['a = k', 'b = p', 'unused = yo'],
                     '@a', '@a')
        flags = {d.name: d.used for d in _defs_by_name(r)}
        self.assertTrue(flags['a'])
        self.assertFalse(flags['b'])
        self.assertFalse(flags['unused'])

    def test_used_definition_breaks_row_other_def_still_diagnostic(self):
        # 被引用的坏定义阻断本行；未引用的坏定义仍在 defs 中独立报诊断
        r = run_defs(4, ['bad = (k', 'other = *p*'], 'k @bad k')
        self.assertEqual(r['rows'][0]['status'], 'error')
        names = {d['name']: d['status'] for d in r['defs']}
        self.assertEqual(names['bad'], 'error')
        self.assertEqual(names['other'], 'error')

    def test_runtime_shortage_inside_snippet_localized(self):
        # cast 2：k 之后只剩 1 针，@a 里的 k2tog 需要 2 针
        r = run_defs(2, ['a = k2tog'], 'k @a')
        err = r['rows'][0]['error']
        self.assertEqual(err['col'], 3)            # 定位行内 @a
        self.assertEqual(err['remaining'], 1)
        self.assertEqual(err['loc']['source'], 'def')
        self.assertEqual(err['loc']['name'], 'a')

    def test_error_row_blocks_following_with_snippets(self):
        r = run_defs(4, ['a = k k k'], '@a', 'k k k k')
        self.assertEqual(r['rows'][0]['status'], 'error')
        self.assertEqual(r['rows'][1]['status'], 'blocked')

    def test_modify_definition_recomputes_rows(self):
        before = run_defs(4, ['a = k k p p'], '@a')
        after = run_defs(4, ['a = k k'], '@a p p')
        self.assertTrue(all(rw['status'] == 'ok'
                            for rw in before['rows'] + after['rows']))

    def test_fullwidth_at_and_def_name(self):
        r = run_defs(2, ['边 = k k'], '＠边')
        self.assertEqual(r['rows'][0]['status'], 'ok')

    def test_duplicate_definition_ignores_second(self):
        r = run_defs(2, ['a = k k', 'a = p p'], '@a')
        self.assertEqual(r['rows'][0]['status'], 'ok')
        self.assertEqual([s['sym'] for s in r['rows'][0]['stitches']],
                         ['k', 'k'])
        dup = [d for d in r['defs'] if d['name'] == 'a']
        self.assertEqual(dup[1]['status'], 'error')


def _defs_by_name(result):
    class _Wrap:
        def __init__(self, d):
            self.name = d['name']
            self.used = d['used']
    return [_Wrap(d) for d in result['defs'] if d['name']]


if __name__ == '__main__':
    unittest.main()
