/* 针织行式编译台 —— 前端逻辑（原生 JS + SVG） */
'use strict';

const srcEl = document.getElementById('src');
const defEl = document.getElementById('defSrc');
const castEl = document.getElementById('castOn');
const gutterEl = document.getElementById('gutter');
const defGutterEl = document.getElementById('defGutter');
const rowsEl = document.getElementById('rows');
const statusEl = document.getElementById('status');
const chartEl = document.getElementById('chart');

const SVGNS = 'http://www.w3.org/2000/svg';
const LINE_H = 20;          // 与 CSS 中 textarea 行高一致
const MAX_CHIPS = 400;      // 展开结果中最多渲染的针法标记数
const MAX_CELLS = 1000;     // 图解中每行最多渲染的格数（超出画省略号）

const SYM_NAME = { k: 'k 下针', p: 'p 上针', yo: 'yo 挂线', k2tog: 'k2tog 二并一' };

// 示例：片段可多行复用、相互引用与前向引用；
// broken 未被任何行引用，它的语法错误只在定义区标红，不影响正常行。
// （edge 耗2产2；pair = yo,k2tog 耗2产2；body = edge,(pair)x2 耗6产6）
const DEFAULT_DEFS = [
  'edge = k k',
  'pair = yo, k2tog',
  'body = @edge, (@pair)x2',
  'broken = (k p',          // 未被引用：写错也不影响行
].join('\n');

const DEFAULT_SOURCE = [
  '(@body)x4',                    // 耗 6x4 = 24 → 出 24
  'k k k k, *@pair*',             // 前 4 针后余 20，@pair 单次耗 2 → 10 次
  '*p p, k k*',
  '(@edge)x4, (@pair)x8',         // 耗 2x4 + 2x8 = 24
  '*k k, p p*',
].join('\n');

let lineStarts = [0];
let defLineStarts = [0];
let lastData = null;
let reqSeq = 0;

/* ---------- 工具 ---------- */

function computeLineStarts(text) {
  const arr = [0];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n') arr.push(i + 1);
  }
  return arr;
}

function rangeIn(starts, text, row) { // row 为 1 基
  const start = starts[row - 1] ?? 0;
  const next = starts[row] ?? (text.length + 1);
  return { start, len: Math.max(0, next - 1 - start) };
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else node[k] = v;
  }
  for (const c of children) node.append(c);
  return node;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  return node;
}

/* ---------- 编译 ---------- */

let timer = null;
function scheduleCompile() {
  clearTimeout(timer);
  timer = setTimeout(doCompile, 200);
}

function splitLines(text) {
  const rows = text.split('\n');
  // 末尾单个换行不产生幻影空行（中间的空白行仍视为有效行）
  if (rows.length > 1 && rows[rows.length - 1] === '') rows.pop();
  return rows;
}

async function doCompile() {
  const text = srcEl.value;
  const defText = defEl.value;
  lineStarts = computeLineStarts(text);
  defLineStarts = computeLineStarts(defText);
  const rows = splitLines(text);
  const definitions = splitLines(defText);
  const castOn = parseInt(castEl.value, 10);

  const my = ++reqSeq;
  let data;
  try {
    const res = await fetch('/api/compile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ castOn, rows, definitions }),
    });
    data = await res.json();
    if (!res.ok) data = { requestError: data.error || ('HTTP ' + res.status) };
  } catch (err) {
    data = { requestError: '无法连接服务：' + err.message };
  }
  if (my !== reqSeq) return; // 已有更新的请求发出，丢弃过期响应（旧请求结果仍已生成，不再渲染）

  lastData = data;
  renderGutter(rows.length, data);
  renderDefGutter(definitions, data);
  renderRows(data);
  renderChart(data);
  renderStatus(data);
}

/* ---------- 状态栏 ---------- */

function renderStatus(data) {
  statusEl.className = 'status';
  if (data.requestError) {
    statusEl.textContent = '⚠ ' + data.requestError;
    statusEl.classList.add('err');
    return;
  }
  const errRow = data.rows.find(r => r.status === 'error');
  if (errRow) {
    const e = errRow.error;
    statusEl.textContent =
      `第 ${e.row} 行第 ${e.col} 列出错：${e.message}` +
      (e.remaining != null ? `（余 ${e.remaining} 针）` : '');
    statusEl.classList.add('err');
    return;
  }
  const unusedBad = (data.defs || []).filter(d => d.status === 'error');
  const okRows = data.rows.filter(r => r.status === 'ok');
  const last = okRows[okRows.length - 1];
  statusEl.textContent = last
    ? `✓ 共 ${data.rows.length} 行全部通过 · 末行针数 ${last.out}`
    : `✓ 起针 ${data.castOn} 针`;
  if (unusedBad.length) {
    statusEl.textContent += `（另有 ${unusedBad.length} 个未引用片段定义有问题，不影响编织行）`;
  }
  statusEl.classList.add('ok');
}

/* ---------- 行号栏 ---------- */

function renderGutter(rowCount, data) {
  const statusByRow = {};
  if (!data.requestError) {
    for (const r of data.rows) statusByRow[r.row] = r.status;
  }
  gutterEl.textContent = '';
  for (let i = 1; i <= rowCount; i++) {
    const st = statusByRow[i];
    const cls = st === 'error' ? 'g-err' : st === 'blocked' ? 'g-blk' : '';
    gutterEl.append(el('div', { class: cls }, String(i)));
  }
}

/* 片段定义区行号：空行也占位（保持行号对齐）。错误行点击定位；未引用灰显。 */
function renderDefGutter(defLines, data) {
  const byLine = {};
  if (!data.requestError) {
    for (const d of (data.defs || [])) byLine[d.line] = d;
  }
  defGutterEl.textContent = '';
  defLines.forEach((text, idx) => {
    const lineNo = idx + 1;
    if (!text.trim()) {
      defGutterEl.append(el('div', {}, ''));
      return;
    }
    const info = byLine[lineNo];
    let cls = '';
    let title = null;
    if (info && info.status === 'error') {
      cls = 'g-def-err';
      title = `第 ${info.error.line} 行第 ${info.error.col} 列：${info.error.message}（点击定位）`;
    } else if (info && !info.used) {
      cls = 'g-def-unused';
      title = `片段 @${info.name} 未被任何编织行引用`;
    }
    const node = el('div', { class: cls, title },
      info && info.status === 'error' ? '⚠' : String(lineNo));
    defGutterEl.append(node);
  });
}

/* ---------- 展开结果 ---------- */

function renderRows(data) {
  rowsEl.textContent = '';
  if (data.requestError) {
    rowsEl.append(el('div', { class: 'errbox' }, '⚠ ' + data.requestError));
    return;
  }
  for (const row of data.rows) {
    rowsEl.append(renderRowCard(row));
  }
}

function chainText(st) {
  // 后端 def 指向“实际针法所在定义”；这里只展示它与行内引用两端
  if (!st.def) return null;
  return `来自片段 @${st.def.name}（定义第 ${st.def.line || '?'} 行第 ` +
    `${st.def.s + 1}–${st.def.e} 列）· 行内引用第 ${st.rs + 1}–${st.re} 列`;
}

function renderRowCard(row) {
  const card = el('div', { class: 'rowcard', 'data-row': row.row });

  const head = el('div', { class: 'rowhead' },
    el('span', { class: 'no' }, `第 ${row.row} 行`));

  if (row.status === 'ok') {
    head.append(
      el('span', { class: 'badge ok' }, '通过'),
      el('span', { class: 'io' }, `入 ${row.in} → 出 ${row.out}`));
    const chips = el('div', { class: 'chips' });
    const list = row.stitches.slice(0, MAX_CHIPS);
    list.forEach(st => {
      const fromDef = !!st.def;
      // 默认定位：实际针法所在定义；角标 ⌖ 定位：行内本次引用
      const defLoc = fromDef
        ? { editor: 'def', row: st.def.line || 1, s: st.def.s, e: st.def.e }
        : { editor: 'src', row: row.row, s: st.s, e: st.e };
      const chip = el('span', {
        class: 'chip ' + st.sym + (fromDef ? ' fromdef' : ''),
        title: fromDef
          ? `${SYM_NAME[st.sym]} · ${chainText(st)}（点击定位定义，⌖ 定位行内引用）`
          : `${SYM_NAME[st.sym]} · 源第 ${st.s + 1}–${st.e} 列`,
        'data-loc': JSON.stringify(defLoc),
      }, st.sym);
      if (fromDef) {
        chip.append(el('span', {
          class: 'refmark', title: '定位行内 @' + st.def.name + ' 引用（第 '
            + (st.rs + 1) + '–' + st.re + ' 列）',
          'data-loc': JSON.stringify(
            { editor: 'src', row: row.row, s: st.rs, e: st.re }),
        }, '⌖'));
      }
      chips.append(chip);
    });
    if (row.stitches.length > MAX_CHIPS) {
      chips.append(el('span', { class: 'chip more' },
        `… 共 ${row.stitches.length} 针`));
    }
    card.append(head, chips);

    // 本行涉及哪些片段（按行内引用区间去重，多次引用分别保留）
    const refs = new Map();
    row.stitches.forEach(st => {
      if (st.def) {
        const key = st.rs + ':' + st.re;
        if (!refs.has(key)) refs.set(key, { name: st.def.name, rs: st.rs, re: st.re });
      }
    });
    if (refs.size) {
      const chain = el('div', { class: 'chainline' }, '片段引用：');
      [...refs.values()].forEach(r => {
        chain.append(' ', el('code', {
          style: 'cursor:pointer',
          title: `定位行内 @${r.name}（第 ${r.rs + 1}–${r.re} 列）`,
          'data-loc': JSON.stringify(
            { editor: 'src', row: row.row, s: r.rs, e: r.re }),
        }, '@' + r.name));
      });
      card.append(chain);
    }
  } else if (row.status === 'error') {
    const e = row.error;
    head.append(
      el('span', { class: 'badge error' }, '错误'),
      el('span', { class: 'io' }, `入 ${row.in}`));
    const primaryLoc = { editor: 'src', row: e.row, s: e.col - 1, e: e.col };
    const box = el('div', { class: 'errbox' },
      `第 ${e.row} 行 · 第 ${e.col} 列 · ${e.message}` +
      (e.remaining != null ? ` ｜ 余针 ${e.remaining}` : ' '),
      el('button', {
        class: 'locate', type: 'button',
        'data-loc': JSON.stringify(primaryLoc),
      }, '定位引用'));
    if (e.loc) {
      if (e.loc.source === 'def') {
        box.append(el('button', {
          class: 'locate', type: 'button',
          title: `定位片段 @${e.loc.name} 的定义（第 ${e.loc.line} 行第 ${e.loc.col} 列）`,
          'data-loc': JSON.stringify(
            { editor: 'def', row: e.loc.line, s: e.loc.col - 1, e: e.loc.col }),
        }, `定位定义 @${e.loc.name}`));
      } else if (e.loc.source === 'dref') {
        box.append(el('button', {
          class: 'locate', type: 'button',
          title: `定位定义 @${e.loc.name} 中出错的引用（第 ${e.loc.line} 行第 ${e.loc.col} 列）`,
          'data-loc': JSON.stringify(
            { editor: 'def', row: e.loc.line, s: e.loc.col - 1, e: e.loc.col }),
        }, `定位定义中的引用 @${e.loc.name}`));
      } else if (e.loc.source === 'row' &&
                 (e.loc.row !== e.row || e.loc.col !== e.col)) {
        box.append(el('button', {
          class: 'locate', type: 'button',
          title: `定位循环回到的行内引用（第 ${e.loc.row} 行第 ${e.loc.col} 列）`,
          'data-loc': JSON.stringify(
            { editor: 'src', row: e.loc.row, s: e.loc.col - 1, e: e.loc.col }),
        }, '定位闭环引用'));
      }
    }
    card.append(head, box);
  } else { // blocked
    head.append(el('span', { class: 'badge blocked' }, '未计算'));
    card.append(head,
      el('div', { class: 'blocked-note' },
        '因前面行存在错误，本行针数无法推导（修正后自动重算）'));
  }
  return card;
}

/* ---------- 源码定位（可在行源码与片段定义两个编辑器间切换） ---------- */

function revealTarget(loc) {
  if (loc.editor === 'def') {
    revealIn(defEl, defGutterEl, defEl.value, defLineStarts, loc.row, loc.s, loc.e);
  } else {
    revealIn(srcEl, gutterEl, srcEl.value, lineStarts, loc.row, loc.s, loc.e);
    const card = rowsEl.querySelector(`.rowcard[data-row="${loc.row}"]`);
    if (card) {
      card.scrollIntoView({ block: 'nearest' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 900);
    }
    markActiveRow(loc.row);
  }
}

function revealIn(editor, gutter, text, starts, row, s, e) {
  const { start, len } = rangeIn(starts, text, row);
  const a = start + Math.min(Math.max(0, s), len);
  const b = start + Math.min(Math.max(0, e), len);
  editor.focus();
  editor.setSelectionRange(a, b);
  editor.scrollTop = Math.max(0, (row - 2) * LINE_H - LINE_H);
  gutter.scrollTop = editor.scrollTop;
}

/* 光标所在行 ↔ 行卡片/图解行 联动高亮 */
function markActiveRow(row) {
  rowsEl.querySelectorAll('.rowcard.active')
    .forEach(c => c.classList.toggle('active', +c.dataset.row === row));
  chartEl.querySelectorAll('text.rowlabel')
    .forEach(t => t.classList.toggle('active', +t.dataset.row === row));
}

function cursorRowIn(editor) {
  const pos = editor.selectionStart;
  const text = editor.value;
  let row = 1;
  for (let i = 0; i < pos; i++) if (text[i] === '\n') row++;
  return row;
}

/* ---------- SVG 图解 ---------- */

function renderChart(data) {
  chartEl.textContent = '';
  if (data.requestError) return;

  const rows = data.rows;
  const n = rows.length;
  const maxOut = Math.max(data.castOn, 1,
    ...rows.map(r => (r.status === 'ok' ? r.stitches.length : 0)));
  const cell = maxOut > 300 ? 10 : maxOut > 120 ? 14 : 22;
  const LEFT = 46, RIGHT = 70, TOP = 8, BOTTOM = 30;
  const width = LEFT + maxOut * cell + RIGHT;
  const height = TOP + n * cell + BOTTOM;

  chartEl.setAttribute('width', width);
  chartEl.setAttribute('height', height);
  chartEl.style.minWidth = width + 'px';

  const yOf = i => TOP + (n - 1 - i) * cell; // 第 1 行在底部

  rows.forEach((row, i) => {
    const y = yOf(i);

    // 左侧行号（点击选中整行源码）
    const rowLen = rangeIn(lineStarts, srcEl.value, row.row).len;
    const lab = svgEl('text', {
      x: LEFT - 8, y: y + cell / 2, 'text-anchor': 'end',
      'dominant-baseline': 'central', 'font-size': 11,
      fill: row.status === 'error' ? '#c62828' : '#8a8378',
      class: 'rowlabel', cursor: 'pointer',
      'data-loc': JSON.stringify(
        { editor: 'src', row: row.row, s: 0, e: rowLen }),
    });
    lab.textContent = `行${row.row}`;
    chartEl.append(lab);

    if (row.status === 'ok') {
      const shown = Math.min(row.stitches.length, MAX_CELLS);
      for (let j = 0; j < shown; j++) {
        chartEl.append(makeCell(row.row, j, row.stitches[j],
          LEFT + j * cell, y, cell));
      }
      if (row.stitches.length > MAX_CELLS) {
        const t = svgEl('text', {
          x: LEFT + shown * cell + 6, y: y + cell / 2,
          'dominant-baseline': 'central', 'font-size': 11, fill: '#8a8378',
        });
        t.textContent = `… 共 ${row.stitches.length} 针`;
        chartEl.append(t);
      }
      // 右侧产出针数
      const outLab = svgEl('text', {
        x: LEFT + Math.min(row.stitches.length, MAX_CELLS) * cell + 8,
        y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#8a8378',
      });
      outLab.textContent = `→${row.out}`;
      chartEl.append(outLab);
    } else if (row.status === 'error') {
      const band = svgEl('rect', {
        x: LEFT, y: y + 1, width: maxOut * cell, height: cell - 2,
        fill: '#fbe4e4', stroke: '#e0a3a0', rx: 3,
      });
      const t = svgEl('text', {
        x: LEFT + 6, y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#c62828',
      });
      t.textContent = `⚠ 第 ${row.error.col} 列：${row.error.message}`;
      chartEl.append(band, t);
    } else {
      const band = svgEl('rect', {
        x: LEFT, y: y + 1, width: maxOut * cell, height: cell - 2,
        fill: '#f2f2f2', stroke: '#d5d5d5', 'stroke-dasharray': '4 3', rx: 3,
      });
      const t = svgEl('text', {
        x: LEFT + 6, y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#9e9e9e',
      });
      t.textContent = '未计算（上行出错）';
      chartEl.append(band, t);
    }
  });

  // 底部起针标注
  const co = svgEl('text', {
    x: LEFT, y: TOP + n * cell + BOTTOM / 2,
    'dominant-baseline': 'central', 'font-size': 11, fill: '#8a8378',
  });
  co.textContent = `起针 ${data.castOn} 针（第 1 行消耗这些针）`;
  chartEl.append(co);
}

function makeCell(rowNo, idx, st, x, y, cell) {
  // 格体：定位实际针法所在定义（无片段则定位行源码）
  const defLoc = st.def
    ? { editor: 'def', row: st.def.line || 1, s: st.def.s, e: st.def.e }
    : { editor: 'src', row: rowNo, s: st.s, e: st.e };
  const g = svgEl('g', {
    class: 'cell', 'data-loc': JSON.stringify(defLoc),
  });
  const fills = { k: '#eef6ee', p: '#e8f1fb', yo: '#fdf1e3', k2tog: '#f3e9f7' };
  const inks = { k: '#2e7d32', p: '#1565c0', yo: '#b3541e', k2tog: '#6a1b9a' };

  g.append(svgEl('rect', {
    class: 'bg', x: x + .5, y: y + .5, width: cell - 1, height: cell - 1,
    fill: fills[st.sym], stroke: '#cfc8bb', 'stroke-width': 1,
  }));

  const ink = inks[st.sym];
  const pad = Math.max(3, cell * 0.22);
  if (st.sym === 'p') {
    g.append(svgEl('line', {
      x1: x + pad, y1: y + cell / 2, x2: x + cell - pad, y2: y + cell / 2,
      stroke: ink, 'stroke-width': 1.6,
    }));
  } else if (st.sym === 'yo') {
    g.append(svgEl('circle', {
      cx: x + cell / 2, cy: y + cell / 2, r: (cell - 2 * pad) / 2,
      fill: 'none', stroke: ink, 'stroke-width': 1.6,
    }));
  } else if (st.sym === 'k2tog') {
    g.append(svgEl('line', {
      x1: x + pad, y1: y + cell - pad, x2: x + cell - pad, y2: y + pad,
      stroke: ink, 'stroke-width': 1.6,
    }));
  } else { // k：竖线
    g.append(svgEl('line', {
      x1: x + cell / 2, y1: y + pad, x2: x + cell / 2, y2: y + cell - pad,
      stroke: ink, 'stroke-width': 1.4,
    }));
  }

  // 角标 ⌖：定位行内本次 @引用（同一片段多次引用各归各位）
  if (st.def) {
    const refLoc = { editor: 'src', row: rowNo, s: st.rs, e: st.re };
    const markSize = Math.max(8, Math.round(cell * 0.42));
    g.append(svgEl('rect', {
      class: 'refcorner',
      x: x + cell - markSize - .5, y: y + .5,
      width: markSize, height: markSize, rx: 2,
      'data-loc': JSON.stringify(refLoc),
    }));
    const t = svgEl('text', {
      class: 'refcorner',
      x: x + cell - markSize / 2 - .5, y: y + markSize / 2 + 1,
      'text-anchor': 'middle', 'font-size': Math.max(6, markSize - 3),
      'data-loc': JSON.stringify(refLoc),
    });
    t.textContent = '⌖';
    g.append(t);
  }

  const title = svgEl('title');
  title.textContent = st.def
    ? `第 ${rowNo} 行 · 第 ${idx + 1} 针 · ${SYM_NAME[st.sym]}` +
      `\n实际定义：@${st.def.name} 第 ${st.def.line || '?'} 行第 ${st.def.s + 1}–${st.def.e} 列` +
      `\n行内引用：第 ${st.rs + 1}–${st.re} 列（点击格体定位定义，⌖ 定位引用）`
    : `第 ${rowNo} 行 · 第 ${idx + 1} 针 · ${SYM_NAME[st.sym]}` +
      ` · 源第 ${st.s + 1}–${st.e} 列`;
  g.append(title);
  return g;
}

/* ---------- 事件 ---------- */

srcEl.addEventListener('input', scheduleCompile);
defEl.addEventListener('input', scheduleCompile);
castEl.addEventListener('input', scheduleCompile);
srcEl.addEventListener('scroll', () => { gutterEl.scrollTop = srcEl.scrollTop; });
defEl.addEventListener('scroll', () => { defGutterEl.scrollTop = defEl.scrollTop; });
srcEl.addEventListener('keyup', () => markActiveRow(cursorRowIn(srcEl)));
srcEl.addEventListener('click', () => markActiveRow(cursorRowIn(srcEl)));
defEl.addEventListener('keyup', () => markActiveRow(0));
defEl.addEventListener('click', () => markActiveRow(0));

// 行卡片 / 图解 / 定义行号中的点击 → 按 data-loc 定位（事件委托）
document.addEventListener('click', ev => {
  const t = ev.target.closest('[data-loc]');
  if (!t) return;
  try {
    revealTarget(JSON.parse(t.dataset.loc));
  } catch (_) { /* 忽略坏属性 */ }
});

// 定义区行号栏点击：直接定位到对应定义行的错误列
defGutterEl.addEventListener('click', ev => {
  const t = ev.target.closest('.g-def-err');
  if (!t || !lastData || lastData.requestError) return;
  const idx = [...defGutterEl.children].indexOf(t);
  const info = (lastData.defs || []).find(d => d.line === idx + 1);
  if (info && info.error) {
    revealTarget({ editor: 'def', row: info.error.line,
      s: info.error.col - 1, e: info.error.col });
  }
});

/* ---------- 启动 ---------- */

srcEl.value = DEFAULT_SOURCE;
defEl.value = DEFAULT_DEFS;
doCompile();
