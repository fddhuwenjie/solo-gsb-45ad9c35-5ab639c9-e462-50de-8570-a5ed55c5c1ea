# -*- coding: utf-8 -*-
"""
针织行式编译器（纯函数模块，不依赖 Flask）

针法表（消耗 / 产出）：
    k      1 / 1   下针
    p      1 / 1   上针
    yo     0 / 1   挂线（空加针）
    k2tog  2 / 1   两针并一针

语法：
    一行 = 多个针法项
    项   = k | p | yo | k2tog
         | "(" 行 ")" xN      嵌套重复
         | "*" 行 "*"         行末重复（必须位于整行末尾，不允许嵌套）
    空白、逗号（, ，）、顿号（、）仅作分隔。

行末重复的语义：
    若 "*…*" 内部完整执行一次的消耗量 C <= 0，报错（无法确定次数）；
    令 R = 当前剩余未编织针数，若 R % C != 0，报错并给出余针 R；
    否则恰好重复 R / C 次。整行消耗必须精确等于上一行产出的针数，
    既不能截断也不能补针。
"""
import unicodedata

# ---- 限制（防止恶意/手滑展开出超大结果）-----------------------------------
MAX_CAST_ON = 2000
MAX_ROWS = 500
MAX_LINE_LEN = 2000
MAX_GROUP_REPEAT = 999
MAX_STITCHES_PER_ROW = 20000

# ---- 针法定义 -------------------------------------------------------------
STITCHES = {
    'k':     {'consume': 1, 'produce': 1, 'name': 'k（下针）'},
    'p':     {'consume': 1, 'produce': 1, 'name': 'p（上针）'},
    'yo':    {'consume': 0, 'produce': 1, 'name': 'yo（挂线）'},
    'k2tog': {'consume': 2, 'produce': 1, 'name': 'k2tog（二并一）'},
}

# 全角符号按 1:1 归一化，字符位置保持不变
_NORMALIZE = {'（': '(', '）': ')', '＊': '*', '，': ',', '、': ','}


def _norm_char(c):
    """单字符归一化：全角→半角（仅当结果为 1 个字符，保证位置不错位）。"""
    c = _NORMALIZE.get(c, c)
    if not c.isascii():
        n = unicodedata.normalize('NFKC', c)
        if len(n) == 1:
            c = n
    return c


def _norm_window(src, i, size):
    """从 i 起取 size 个源字符并逐字归一化（每个源字符仍占 1 个位置）。"""
    return ''.join(_norm_char(c) for c in src[i:i + size])


class KnitError(Exception):
    """编译错误：行号 row（1 基）、列号 col（1 基）、余针 remaining（可为 None）。"""

    def __init__(self, row, col, message, remaining=None):
        super().__init__(message)
        self.row = row
        self.col = col
        self.message = message
        self.remaining = remaining

    def to_dict(self):
        return {
            'row': self.row,
            'col': self.col,
            'message': self.message,
            'remaining': self.remaining,
        }


# ---- 词法分析 -------------------------------------------------------------
# token: (type, value, start, end)，start/end 为 0 基半开区间

def tokenize(src, row):
    toks = []
    i, n = 0, len(src)
    while i < n:
        ch = _norm_char(src[i])
        low = ch.lower()

        if ch.isspace() or ch == ',':
            i += 1
            continue

        # 关键字优先（k2tog 必须在 k 之前匹配）
        if _norm_window(src, i, 5).lower() == 'k2tog':
            toks.append(('ST', 'k2tog', i, i + 5))
            i += 5
            continue
        if _norm_window(src, i, 2).lower() == 'yo':
            toks.append(('ST', 'yo', i, i + 2))
            i += 2
            continue
        if low in ('k', 'p'):
            toks.append(('ST', low, i, i + 1))
            i += 1
            continue

        if ch == '(':
            toks.append(('LP', '(', i, i + 1))
            i += 1
            continue
        if ch == ')':
            toks.append(('RP', ')', i, i + 1))
            i += 1
            continue
        if ch == '*':
            toks.append(('STAR', '*', i, i + 1))
            i += 1
            continue
        if low == 'x':
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            if j == i + 1:
                raise KnitError(row, i + 1,
                                "'x' 后需要重复次数，例如 (k,p)x3")
            toks.append(('X', int(src[i + 1:j]), i, j))
            i = j
            continue

        if ch.isdigit():
            raise KnitError(row, i + 1,
                            "不支持 'k3' 这类数字后缀；重复请写 (k)x3，"
                            "整行重复用 *…*")
        raise KnitError(row, i + 1, "无法识别的字符 %r" % src[i])

    return toks


# ---- 语法分析（递归下降，支持嵌套括号）--------------------------------------
# AST 节点：
#   {'kind':'st',  'sym':..., 's':i, 'e':j}
#   {'kind':'grp', 'items':[...], 'repeat':N, 's','e'}
#   {'kind':'star', 'items':[...], 's','e'}   仅允许出现在行末顶层

def parse_row(src, row):
    toks = tokenize(src, row)
    items, i = _parse_seq(toks, 0, row, stops=(), top=True)
    if i < len(toks):  # 理论上顶层没有终止符，不会走到这里
        raise KnitError(row, toks[i][2] + 1, "意外的记号")

    # “末尾 *…*”：星号重复必须是最后一个顶层项
    for idx, it in enumerate(items):
        if it['kind'] == 'star' and idx != len(items) - 1:
            raise KnitError(row, it['s'] + 1,
                            "'*…*' 只能位于行末，其后不能再出现其他针法")
    return items


def _parse_seq(toks, i, row, stops, top):
    items = []
    while i < len(toks) and toks[i][0] not in stops:
        typ, val, s, e = toks[i]

        if typ == 'ST':
            items.append({'kind': 'st', 'sym': val, 's': s, 'e': e})
            i += 1

        elif typ == 'LP':
            inner, j = _parse_seq(toks, i + 1, row, stops=('RP',), top=False)
            if j >= len(toks):
                raise KnitError(row, s + 1, "括号 '(' 缺少匹配的 ')'")
            rp_s = toks[j][2]
            j += 1  # 跳过 ')'
            repeat = 1
            if j < len(toks) and toks[j][0] == 'X':
                repeat = toks[j][1]
                if repeat > MAX_GROUP_REPEAT:
                    raise KnitError(
                        row, toks[j][2] + 1,
                        "重复次数过大（上限 %d）" % MAX_GROUP_REPEAT)
                j += 1
            items.append({'kind': 'grp', 'items': inner,
                          'repeat': repeat, 's': s, 'e': rp_s + 1})
            i = j

        elif typ == 'STAR':
            if not top:
                raise KnitError(row, s + 1,
                                "'*…*' 只能写在行末，不能嵌套在括号里")
            inner, j = _parse_seq(toks, i + 1, row, stops=('STAR',), top=False)
            if j >= len(toks):
                raise KnitError(row, s + 1,
                                "'*' 缺少配对的 '*'，行末重复应写成 *…*")
            star_end = toks[j][3]
            j += 1
            items.append({'kind': 'star', 'items': inner,
                          's': s, 'e': star_end})
            i = j

        elif typ == 'RP':
            raise KnitError(row, s + 1, "多余的右括号 ')'")
        elif typ == 'X':
            raise KnitError(row, s + 1, "'xN' 只能紧跟在 ')' 之后使用")
        else:  # pragma: no cover - 防御性分支
            raise KnitError(row, s + 1, "意外的记号")

    return items, i


# ---- 静态针数（不展开也能算出一段的消耗/产出）-------------------------------

def _static_count(items):
    consume = produce = 0
    for it in items:
        if it['kind'] == 'st':
            d = STITCHES[it['sym']]
            consume += d['consume']
            produce += d['produce']
        elif it['kind'] == 'grp':
            c, p = _static_count(it['items'])
            consume += c * it['repeat']
            produce += p * it['repeat']
        # star 的次数依赖运行时余针，不计入
    return consume, produce


# ---- 行展开 ---------------------------------------------------------------

def expand(items, available, row):
    """把 AST 展开为针法列表；available = 本行可消耗针数（上一行产出）。

    返回 (stitches, consumed, produced)。stitches 中每一项：
        {'sym':..., 's': 源码起始列(0基), 'e': 源码结束列(0基, 开区间)}
    消耗必须恰好等于 available，否则抛 KnitError（余针 / 针数不足）。
    """
    out = []
    consumed = 0

    def emit(st):
        nonlocal consumed
        d = STITCHES[st['sym']]
        c, p = d['consume'], d['produce']
        remaining = available - consumed
        if c > remaining:
            raise KnitError(
                row, st['s'] + 1,
                "针数不足：%s 需消耗 %d 针，但本行只剩 %d 针"
                % (d['name'], c, remaining),
                remaining=remaining)
        consumed += c
        out.append({'sym': st['sym'], 's': st['s'], 'e': st['e']})
        if len(out) > MAX_STITCHES_PER_ROW:
            raise KnitError(
                row, st['s'] + 1,
                "展开针数超过上限 %d（检查 xN 是否过大）"
                % MAX_STITCHES_PER_ROW)
        return p

    def walk(seq):
        for it in seq:
            if it['kind'] == 'st':
                emit(it)
            elif it['kind'] == 'grp':
                for _ in range(it['repeat']):
                    walk(it['items'])
            else:  # star：只允许在顶层且位于末尾
                once_c, once_p = _static_count(it['items'])
                if once_c <= 0:
                    raise KnitError(
                        row, it['s'] + 1,
                        "'*…*' 内单次消耗必须为正针数（当前为 %d），"
                        "否则无法确定重复次数" % once_c,
                        remaining=available - consumed)
                remain = available - consumed
                if remain % once_c != 0:
                    raise KnitError(
                        row, it['s'] + 1,
                        "'*…*' 单次消耗 %d 针，不能整除余针 %d（%d %% %d = %d）"
                        "；不得截断或补针"
                        % (once_c, remain, remain, once_c, remain % once_c),
                        remaining=remain)
                for _ in range(remain // once_c):
                    walk(it['items'])

    walk(items)

    if consumed != available:
        remain = available - consumed
        # 余针定位到行尾（空行则定位到第 1 列）
        raise KnitError(
            row, max(1, items[-1]['e']) if items else 1,
            "行末仍余 %d 针未编织（不得截断或补针）" % remain,
            remaining=remain)

    produced = len(out)  # 四种针法均产出 1 针
    return out, consumed, produced


# ---- 整段编译：逐行推导 ----------------------------------------------------

def compile_rows(cast_on, sources):
    """sources: 每行源码字符串列表。返回可直接 JSON 序列化的结果。"""
    if not isinstance(cast_on, int) or isinstance(cast_on, bool):
        raise ValueError('castOn 必须是整数')
    if not (1 <= cast_on <= MAX_CAST_ON):
        raise ValueError('起针数必须在 1..%d 之间' % MAX_CAST_ON)
    if not isinstance(sources, (list, tuple)) or not all(
            isinstance(s, str) for s in sources):
        raise ValueError('rows 必须是字符串列表')
    if len(sources) > MAX_ROWS:
        raise ValueError('行数超过上限 %d' % MAX_ROWS)

    result_rows = []
    available = cast_on
    blocked = False  # 一旦某行出错，后续行无法推导针数

    for idx, src in enumerate(sources):
        row_no = idx + 1
        if len(src) > MAX_LINE_LEN:
            raise ValueError('第 %d 行超过 %d 字符上限' % (row_no, MAX_LINE_LEN))

        if blocked:
            result_rows.append({
                'row': row_no, 'source': src, 'status': 'blocked',
            })
            continue

        try:
            ast = parse_row(src, row_no)
            stitches, consumed, produced = expand(ast, available, row_no)
            result_rows.append({
                'row': row_no,
                'source': src,
                'status': 'ok',
                'in': available,
                'out': produced,
                'consumed': consumed,
                'stitches': stitches,
            })
            available = produced
        except KnitError as err:
            result_rows.append({
                'row': row_no,
                'source': src,
                'status': 'error',
                'in': available,
                'error': err.to_dict(),
            })
            blocked = True

    return {'castOn': cast_on, 'rows': result_rows}
