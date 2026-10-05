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
         | "@名称"            引用一个片段（按该处每次引用展开）
    空白、逗号（, ，）、顿号（、）仅作分隔。

片段（macro）：
    在“片段定义区”按  名称 = 针法序列  逐行编写，例如
        edge = k k
        band = (@edge, p p)x2
    行源码用  @edge  引用；片段可以相互引用，也允许前向引用（先使用后定义）。
    片段内只能写针法 / 分组 / 引用，*不能* 写行末重复 *…*。
    未被任何行（传递）引用的片段，即使内容写错也不影响正常行；缺失引用、
    循环引用、片段语法错误一律定位到“使用它的编织行”的 @引用 位置，并在
    信息中给出所涉定义与引用链，其后各行仍按原规则阻断。

行末重复的语义：
    若 "*…*" 内部完整执行一次的消耗量 C <= 0，报错（无法确定次数）；
    令 R = 当前剩余未编织针数，若 R % C != 0，报错并给出余针 R；
    否则恰好重复 R / C 次。整行消耗必须精确等于上一行产出的针数，
    既不能截断也不能补针。片段的 C 按其实际展开的针法消耗计算。
"""
import unicodedata

# ---- 限制（防止恶意/手滑展开出超大结果）-----------------------------------
MAX_CAST_ON = 2000
MAX_ROWS = 500
MAX_LINE_LEN = 2000
MAX_DEF_LINES = 200
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
    """编译错误。

    行侧定位：row（1 基）、col（1 基）、余针 remaining（可为 None）。
    若错误源自片段，loc 给出“实际出错定义”的位置
        {'source': 'def', 'name': 片段名, 'col': 定义内 1 基列}
    或循环引用时回到行侧引用的指针 {'source': 'row', 'row': r, 'col': c}。
    """

    def __init__(self, row, col, message, remaining=None, loc=None):
        super().__init__(message)
        self.row = row
        self.col = col
        self.message = message
        self.remaining = remaining
        self.loc = loc

    def to_dict(self):
        d = {
            'row': self.row,
            'col': self.col,
            'message': self.message,
            'remaining': self.remaining,
        }
        if self.loc is not None:
            d['loc'] = self.loc
        return d


# ---- 片段名称字符 ---------------------------------------------------------

def _is_name_start(c):
    return c.isalpha() or c in ('_',)


def _is_name_part(c):
    return c.isalnum() or c in ('_',)


# ---- 词法分析 -------------------------------------------------------------
# token: (type, value, start, end)，start/end 为 0 基半开区间
# REF 的 value 为归一化后的片段名（NFKC，用于查表），raw 保留原始拼写。

def tokenize(src, row, offset=0, allow_star=True):
    """src 既可以是整行（offset=0），也可以是片段体（offset = 等号后起点）。

    start/end 始终相对于 src 起点 + offset，即定义行/行源码内的真实列。
    allow_star=False 时片段体内出现 '*' 直接报错。
    """
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
            toks.append(('ST', 'k2tog', offset + i, offset + i + 5))
            i += 5
            continue
        if _norm_window(src, i, 2).lower() == 'yo':
            toks.append(('ST', 'yo', offset + i, offset + i + 2))
            i += 2
            continue
        if low in ('k', 'p'):
            toks.append(('ST', low, offset + i, offset + i + 1))
            i += 1
            continue

        # 片段引用 @名称：允许字母/下划线开头，其后可含数字（k1、p2 之类）
        if ch == '@':
            j = i + 1
            if j < n and _is_name_start(_norm_char(src[j])):
                j += 1
                while j < n and _is_name_part(_norm_char(src[j])):
                    j += 1
                name = ''.join(_norm_char(c) for c in src[i + 1:j])
                toks.append(('REF', {'name': name, 'raw': src[i + 1:j]},
                             offset + i, offset + j))
                i = j
                continue
            # @ 后面不是合法名称首字符
            raise KnitError(row, offset + i + 1,
                            "'@' 后应为已定义的片段名称，例如 @edge")

        if ch == '(':
            toks.append(('LP', '(', offset + i, offset + i + 1))
            i += 1
            continue
        if ch == ')':
            toks.append(('RP', ')', offset + i, offset + i + 1))
            i += 1
            continue
        if ch == '*':
            if not allow_star:
                raise KnitError(row, offset + i + 1,
                                "片段中不允许写行末重复 '*…*'；"
                                "请把行末重复写在编织行上")
            toks.append(('STAR', '*', offset + i, offset + i + 1))
            i += 1
            continue
        if low == 'x':
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            if j == i + 1:
                raise KnitError(row, offset + i + 1,
                                "'x' 后需要重复次数，例如 (k,p)x3")
            toks.append(('X', int(src[i + 1:j]), offset + i, offset + j))
            i = j
            continue

        if ch.isdigit():
            raise KnitError(row, offset + i + 1,
                            "不支持 'k3' 这类数字后缀；重复请写 (k)x3，"
                            "整行重复用 *…*")
        raise KnitError(row, offset + i + 1, "无法识别的字符 %r" % src[i])

    return toks


# ---- 语法分析（递归下降，支持嵌套括号）--------------------------------------
# AST 节点：
#   {'kind':'st',  'sym':..., 's':i, 'e':j}
#   {'kind':'grp', 'items':[...], 'repeat':N, 's','e'}
#   {'kind':'star','items':[...], 's','e'}   仅允许出现在行末顶层
#   {'kind':'ref', 'name': 归一化名, 'raw': 原始拼写, 's','e'}

def parse_row(src, row):
    toks = tokenize(src, row)
    return _parse_tokens(toks, row, allow_star=True)


def parse_def_body(src, row, offset):
    """解析片段体：与行相同的针法序列，但不允许行末 '*…*'。"""
    toks = tokenize(src, row, offset=offset, allow_star=False)
    return _parse_tokens(toks, row, allow_star=False)


def _parse_tokens(toks, row, allow_star):
    items, i = _parse_seq(toks, 0, row, stops=(), top=True,
                          allow_star=allow_star)
    if i < len(toks):  # 理论上顶层没有终止符，不会走到这里
        raise KnitError(row, toks[i][2] + 1, "意外的记号")

    # “末尾 *…*”：星号重复必须是最后一个顶层项
    for idx, it in enumerate(items):
        if it['kind'] == 'star' and idx != len(items) - 1:
            raise KnitError(row, it['s'] + 1,
                            "'*…*' 只能位于行末，其后不能再出现其他针法")
    return items


def _parse_seq(toks, i, row, stops, top, allow_star=True):
    items = []
    while i < len(toks) and toks[i][0] not in stops:
        typ, val, s, e = toks[i]

        if typ == 'ST':
            items.append({'kind': 'st', 'sym': val, 's': s, 'e': e})
            i += 1

        elif typ == 'REF':
            items.append({'kind': 'ref', 'name': val['name'],
                          'raw': val['raw'], 's': s, 'e': e})
            i += 1

        elif typ == 'LP':
            inner, j = _parse_seq(toks, i + 1, row, stops=('RP',), top=False,
                                  allow_star=allow_star)
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
            inner, j = _parse_seq(toks, i + 1, row, stops=('STAR',),
                                  top=False, allow_star=allow_star)
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

def _static_count(items, def_counts=None):
    """def_counts: 片段名 -> (单次消耗, 单次产出)。仅用于已解析通过的片段；
    传入的 items 必须已经过引用校验（引用都能在 def_counts 中查到）。"""
    def_counts = def_counts or {}
    consume = produce = 0
    for it in items:
        if it['kind'] == 'st':
            d = STITCHES[it['sym']]
            consume += d['consume']
            produce += d['produce']
        elif it['kind'] == 'grp':
            c, p = _static_count(it['items'], def_counts)
            consume += c * it['repeat']
            produce += p * it['repeat']
        elif it['kind'] == 'ref':
            c, p = def_counts[it['name']]
            consume += c
            produce += p
        # star 的次数依赖运行时余针，不计入
    return consume, produce


# ---- 片段定义 ---------------------------------------------------------------

class _Def:
    __slots__ = ('line', 'name', 'raw', 'body',
                 'items', 'error', 'counts', 'used')

    def __init__(self, line, name, raw, body):
        self.line = line          # 1 基定义行号
        self.name = name          # 归一化名（NFKC）
        self.raw = raw            # 定义行原始文本
        self.body = body          # '=' 右侧源码（仍是 raw 的子串视图）
        self.items = None         # 解析成功后的 AST
        self.error = None         # 本行自身语法诊断（不影响未引用的正常行）
        self.counts = None        # 解析成功时先占位，引用校验后填消耗/产出
        self.used = False         # 是否被某个编织行（传递）引用


def parse_definitions(lines):
    """把定义区源码解析为有序的 _Def 列表（空行跳过，不占行号）。

    每行形如  名称 = 针法序列 。本行自身的问题（无 '='、名称非法、重复、
    语法错）都只记录在该定义上；是否影响编织行，取决于它是否被引用。
    """
    defs = []
    seen = {}
    for idx, raw in enumerate(lines):
        line_no = idx + 1
        if not raw.strip():
            continue

        eq_pos = None
        for j, c in enumerate(raw):
            if _norm_char(c) == '=':
                eq_pos = j
                break
        if eq_pos is None:
            # 缺少 '='：若行里有非空白内容，定位到第一个非法字符处
            col = next((j + 1 for j, c in enumerate(raw)
                        if not _norm_char(c).isspace()), len(raw) + 1)
            d = _Def(line_no, '', raw, '')
            d.error = KnitError(
                line_no, col,
                "片段定义应写成『名称 = 针法序列』，当前行缺少 '='")
            defs.append(d)
            continue

        body = raw[eq_pos + 1:]
        name_start = next((k for k, c in enumerate(raw)
                           if not _norm_char(c).isspace()), 0)
        # 名称合法性：连续的名称字符（中间不允许空白，避免 “a b = …” 歧义），
        # 名称前后可有空白；首字符须为名称起始符。
        raw_name = ''.join(_norm_char(c) for c in raw[name_start:eq_pos]
                           if not _norm_char(c).isspace())
        name_chars = []
        invalid_col = None
        j = name_start
        while j < eq_pos:
            nc = _norm_char(raw[j])
            if not name_chars:
                if not _is_name_start(nc):
                    invalid_col = j
                    break
                name_chars.append(nc)
            else:
                if nc.isspace():
                    break
                if not _is_name_part(nc):
                    invalid_col = j
                    break
                name_chars.append(nc)
            j += 1
        if invalid_col is None and name_chars:
            # 名称段之后到 '=' 之间只能有空白
            tail = j
            while tail < eq_pos and _norm_char(raw[tail]).isspace():
                tail += 1
            if tail != eq_pos:
                invalid_col = tail
        name = ''.join(name_chars)
        if invalid_col is not None:
            d = _Def(line_no, raw_name, raw, body)
            d.error = KnitError(
                line_no, invalid_col + 1,
                "片段名称 %r 不合法：名称需连续（中间不能有空白或其他字符），"
                "以字母或下划线开头，只含字母/数字/下划线" % raw_name)
            defs.append(d)
            continue
        if not name:
            d = _Def(line_no, '', raw, body)
            d.error = KnitError(line_no, 1, "'=' 前缺少片段名称")
            defs.append(d)
            continue

        if name in seen:
            d = _Def(line_no, name, raw, body)
            d.error = KnitError(
                line_no, name_start + 1,
                "片段 %r 重复定义（第 %d 行已定义过，本行被忽略）"
                % (name, seen[name]))
            defs.append(d)
            continue

        d = _Def(line_no, name, raw, body)
        try:
            # 片段内的列号基于整行：body 在 raw 中起点 = eq_pos + 1
            d.items = parse_def_body(body, line_no, eq_pos + 1)
        except KnitError as err:
            d.error = err
        defs.append(d)
        seen[name] = line_no
    return defs


class SnippetTable:
    """片段表：持有定义、做引用校验（缺失/循环），并供展开器查询。"""

    def __init__(self, defs):
        self.defs = defs
        self.by_name = {d.name: d for d in defs
                        if d.name and d.items is not None}
        self._checked = set()

    # -- 定义侧诊断输出 -----------------------------------------------------
    def definition_dicts(self):
        out = []
        for d in self.defs:
            rec = {
                'line': d.line, 'name': d.name, 'used': d.used,
                'status': 'error' if d.error is not None else 'ok',
            }
            if d.error is not None:
                e = d.error
                rec['error'] = {
                    'line': e.row, 'col': e.col, 'message': e.message,
                }
            out.append(rec)
        return out

    # -- 引用校验：从一行用到的顶层引用出发做 DFS ---------------------------
    def validate_row(self, items, row):
        """检查本行（传递）引用的全部片段。

        任一缺失引用 / 循环引用 / 被引用片段自身语法错，都抛 KnitError，
        行侧定位在“本行最外层 @引用”处，并在信息中给出引用链与定义。
        """
        for it in self._top_refs(items):
            self._validate_ref(it, row, stack=[])

    @staticmethod
    def _top_refs(items):
        refs = []
        for it in items:
            if it['kind'] == 'ref':
                refs.append(it)
            elif it['kind'] in ('grp', 'star'):
                refs.extend(SnippetTable._top_refs(it['items']))
        return refs

    def _validate_ref(self, ref, row, stack):
        name = ref['name']
        chain = stack + [ref]

        def site_ref():
            # 行侧主定位：本行最外层 @引用（stack 为空时即 ref 自身）；
            # ref 可能位于某片段定义体内，其列不能当作行内列。
            return stack[0] if stack else ref

        d = self.by_name.get(name)
        if d is None:
            # 可能是有定义但自身语法错（未进 by_name），也算引用失败
            bad = next((x for x in self.defs if x.name == name), None)
            if bad is not None and bad.error is not None:
                bad.used = True
                raise self._def_syntax_error(bad, chain, row, site_ref())
            site = site_ref()
            loc = None
            if stack:
                # 坏引用写在父片段定义体内：定位到该定义行的相应列
                parent = stack[-1]
                parent_def = self.by_name.get(parent['name'])
                if parent_def is not None:
                    loc = {'source': 'dref', 'name': parent['name'],
                           'line': parent_def.line, 'col': ref['s'] + 1}
            raise KnitError(
                row, site['s'] + 1,
                "引用了未定义的片段 @%s%s；请在定义区编写『%s = …』"
                % (name, self._chain_tail(stack), name),
                loc=loc)
        d.used = True  # 被本行（传递）引用到
        if d.items is None and d.error is not None:
            raise self._def_syntax_error(d, chain, row, site_ref())
        if name in {r['name'] for r in stack}:
            cycle = [r['name'] for r in chain]
            involved = '、'.join(sorted({n for n in cycle}))
            site = site_ref()
            raise KnitError(
                row, site['s'] + 1,
                "片段循环引用：%s（@%s 处又引用回到自身；涉及定义：%s）"
                % (' → '.join('@' + n for n in cycle), name, involved),
                loc={'source': 'row', 'row': row, 'col': site['s'] + 1})
        if name in self._checked:
            return

        for inner in self._top_refs(d.items):
            self._validate_ref(inner, row, stack=chain)
        # 引用全部有效后才算其静态针数（含片段引用）
        d.counts = _static_count(d.items, self._count_map())
        self._checked.add(name)

    def _count_map(self):
        return {n: x.counts for n, x in self.by_name.items()
                if x.counts is not None}

    @staticmethod
    def _chain_tail(stack):
        if not stack:
            return ''
        return '（经 %s 引用）' % ' → '.join('@' + r['name'] for r in stack)

    def _def_syntax_error(self, d, chain, row, ref):
        e = d.error
        return KnitError(
            row, ref['s'] + 1,
            "片段 @%s 的定义（第 %d 行第 %d 列）有语法错误：%s%s"
            % (d.name, e.row, e.col, e.message,
               self._chain_tail(chain[:-1])),
            loc={'source': 'def', 'name': d.name, 'col': e.col,
                 'line': e.row})

    # -- 展开期查询 ---------------------------------------------------------
    def get(self, name):
        return self.by_name[name]


# ---- 行展开 ---------------------------------------------------------------

def expand(items, available, row, defs=None):
    """把 AST 展开为针法列表；available = 本行可消耗针数（上一行产出）。

    defs 为 SnippetTable 时启用片段展开（调用前应先 defs.validate_row）。
    返回 (stitches, consumed, produced)。stitches 中每一项：
        {'sym', 's', 'e',
         'rs','re': 行内本次 @引用 的源码区间（无引用时等于 s/e）,
         'def':    {'name','s','e'} 实际针法所在定义区间，或 None}
    消耗必须恰好等于 available，否则抛 KnitError（余针 / 针数不足）。
    """
    table = defs
    out = []
    consumed = 0
    # stack: 自外向内的 (引用AST, 定义 _Def)；顶层为空
    stack = []

    def current_ref():
        return stack[0][0] if stack else None

    def chain_text():
        if not stack:
            return ''
        return '（引用链 %s）' % ' → '.join(
            '@%s' % f['name'] for f, _d in stack)

    def loc_for(st):
        if not stack:
            return None
        ref_ast, d = stack[-1]
        return {'name': d.name, 's': st['s'], 'e': st['e'],
                'line': d.line}

    def row_span(st):
        ref = current_ref()
        if ref is None:
            return st['s'], st['e']
        return ref['s'], ref['e']

    def fail(col, message, remaining=None, def_loc=None):
        ref = current_ref()
        if ref is None:
            raise KnitError(row, col, message, remaining=remaining,
                            loc=def_loc)
        loc = def_loc or {'source': 'row', 'row': row, 'col': ref['s'] + 1}
        raise KnitError(row, ref['s'] + 1, message,
                        remaining=remaining, loc=loc)

    def emit(st):
        nonlocal consumed
        d_st = STITCHES[st['sym']]
        c, p = d_st['consume'], d_st['produce']
        remaining = available - consumed
        if c > remaining:
            ref = current_ref()
            if ref is None:
                raise KnitError(
                    row, st['s'] + 1,
                    "针数不足：%s 需消耗 %d 针，但本行只剩 %d 针"
                    % (d_st['name'], c, remaining),
                    remaining=remaining)
            dfn = stack[-1][1]
            fail(st['s'] + 1,
                 "针数不足：片段 @%s 中的 %s（定义第 %d 行第 %d 列）"
                 "需消耗 %d 针，但本行只剩 %d 针%s"
                 % (dfn.name, d_st['name'], dfn.line, st['s'] + 1,
                    c, remaining, chain_text()),
                 remaining=remaining,
                 def_loc={'source': 'def', 'name': dfn.name,
                          'col': st['s'] + 1, 'line': dfn.line})
        consumed += c
        rs, re_ = row_span(st)
        out.append({'sym': st['sym'], 's': st['s'], 'e': st['e'],
                    'rs': rs, 're': re_, 'def': loc_for(st)})
        if len(out) > MAX_STITCHES_PER_ROW:
            fail(st['s'] + 1,
                 "展开针数超过上限 %d（检查 xN 是否过大或片段重复过多）"
                 % MAX_STITCHES_PER_ROW)

    def count_of(seq):
        return _static_count(seq,
                             {n: x.counts for n, x in table.by_name.items()
                              if x.counts is not None}
                             if table is not None else None)

    def walk(seq):
        for it in seq:
            if it['kind'] == 'st':
                emit(it)
            elif it['kind'] == 'grp':
                for _ in range(it['repeat']):
                    walk(it['items'])
            elif it['kind'] == 'ref':
                if table is None:
                    raise KnitError(row, it['s'] + 1,
                                    "未提供片段表，无法展开 @%s" % it['name'])
                d = table.get(it['name'])
                stack.append((it, d))
                walk(d.items)
                stack.pop()
            else:  # star：只允许在顶层且位于末尾
                once_c, _once_p = count_of(it['items'])
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
                        "；不得截断或补针（已按片段实际消耗计算%s）"
                        % (once_c, remain, remain, once_c, remain % once_c,
                           chain_text()),
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

def compile_rows(cast_on, sources, def_lines=None):
    """sources: 每行源码字符串列表；def_lines: 片段定义区各行。

    返回可直接 JSON 序列化的结果：
      {'castOn', 'rows': [...], 'defs': [{line,name,used,status,error?}]}
    """
    if not isinstance(cast_on, int) or isinstance(cast_on, bool):
        raise ValueError('castOn 必须是整数')
    if not (1 <= cast_on <= MAX_CAST_ON):
        raise ValueError('起针数必须在 1..%d 之间' % MAX_CAST_ON)
    if not isinstance(sources, (list, tuple)) or not all(
            isinstance(s, str) for s in sources):
        raise ValueError('rows 必须是字符串列表')
    if len(sources) > MAX_ROWS:
        raise ValueError('行数超过上限 %d' % MAX_ROWS)
    if def_lines is None:
        def_lines = []
    if not isinstance(def_lines, (list, tuple)) or not all(
            isinstance(s, str) for s in def_lines):
        raise ValueError('definitions 必须是字符串列表')
    if len(def_lines) > MAX_DEF_LINES:
        raise ValueError('片段定义行数超过上限 %d' % MAX_DEF_LINES)
    for i, s in enumerate(def_lines):
        if len(s) > MAX_LINE_LEN:
            raise ValueError('片段定义第 %d 行超过 %d 字符上限'
                             % (i + 1, MAX_LINE_LEN))

    table = SnippetTable(parse_definitions(def_lines))

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
            # 先校验全部（传递）引用：缺失 / 循环 / 定义语法错在此定位到本行
            table.validate_row(ast, row_no)
            stitches, consumed, produced = expand(
                ast, available, row_no, defs=table)
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

    return {
        'castOn': cast_on,
        'rows': result_rows,
        'defs': table.definition_dicts(),
    }
