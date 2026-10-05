/* 针织行式编译台 —— 前端逻辑（原生 JS + SVG） */
'use strict';

const srcEl = document.getElementById('src');
const castEl = document.getElementById('castOn');
const gutterEl = document.getElementById('gutter');
const rowsEl = document.getElementById('rows');
const statusEl = document.getElementById('status');
const chartEl = document.getElementById('chart');

const SVGNS = 'http://www.w3.org/2000/svg';
const LINE_H = 20;          // 与 CSS 中 textarea 行高一致
const MAX_CHIPS = 400;      // 展开结果中最多渲染的针法标记数
const MAX_CELLS = 1000;     // 图解中每行最多渲染的格数（超出画省略号）

const SYM_NAME = { k: 'k 下针', p: 'p 上针', yo: 'yo 挂线', k2tog: 'k2tog 二并一' };

// 示例：起针 24，含嵌套分组与行末重复
const DEFAULT_SOURCE = [
  '(k k, p p)x6',
  'k k k k, *yo, k2tog*',
  '*p p, k k*',
  '(k, yo, k2tog)x8',
  '(k k, (p, yo)x2, k2tog)x4',
  '*k k, p p*',
].join('\n');

let lineStarts = [0];
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

function lineRange(row) { // row 为 1 基
  const start = lineStarts[row - 1] ?? 0;
  const next = lineStarts[row] ?? (srcEl.value.length + 1);
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

async function doCompile() {
  const text = srcEl.value;
  lineStarts = computeLineStarts(text);
  const rows = text.split('\n');
  // 末尾单个换行不产生幻影空行（中间的空白行仍视为有效行）
  if (rows.length > 1 && rows[rows.length - 1] === '') rows.pop();
  const castOn = parseInt(castEl.value, 10);

  const my = ++reqSeq;
  let data;
  try {
    const res = await fetch('/api/compile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ castOn, rows }),
    });
    data = await res.json();
    if (!res.ok) data = { requestError: data.error || ('HTTP ' + res.status) };
  } catch (err) {
    data = { requestError: '无法连接服务：' + err.message };
  }
  if (my !== reqSeq) return; // 已有更新的请求发出，丢弃过期响应

  lastData = data;
  renderGutter(rows.length, data);
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
  const okRows = data.rows.filter(r => r.status === 'ok');
  const last = okRows[okRows.length - 1];
  statusEl.textContent = last
    ? `✓ 共 ${data.rows.length} 行全部通过 · 末行针数 ${last.out}`
    : `✓ 起针 ${data.castOn} 针`;
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
      chips.append(el('span', {
        class: 'chip ' + st.sym,
        title: `${SYM_NAME[st.sym]} · 源第 ${st.s + 1}–${st.e} 列`,
        'data-row': row.row, 'data-s': st.s, 'data-e': st.e,
      }, st.sym));
    });
    if (row.stitches.length > MAX_CHIPS) {
      chips.append(el('span', { class: 'chip more' },
        `… 共 ${row.stitches.length} 针`));
    }
    card.append(head, chips);
  } else if (row.status === 'error') {
    const e = row.error;
    head.append(
      el('span', { class: 'badge error' }, '错误'),
      el('span', { class: 'io' }, `入 ${row.in}`));
    const box = el('div', { class: 'errbox' },
      `第 ${e.row} 行 · 第 ${e.col} 列 · ${e.message}` +
      (e.remaining != null ? ` ｜ 余针 ${e.remaining}` : ''),
      el('button', {
        class: 'locate', type: 'button',
        'data-row': e.row, 'data-s': e.col - 1, 'data-e': e.col,
      }, '定位'));
    card.append(head, box);
  } else { // blocked
    head.append(el('span', { class: 'badge blocked' }, '未计算'));
    card.append(head,
      el('div', { class: 'blocked-note' },
        '因前面行存在错误，本行针数无法推导（修正后自动重算）'));
  }
  return card;
}

/* ---------- 源码定位 ---------- */

function revealSource(row, s, e) {
  const { start, len } = lineRange(row);
  const a = start + Math.min(Math.max(0, s), len);
  const b = start + Math.min(Math.max(0, e), len);
  srcEl.focus();
  srcEl.setSelectionRange(a, b);
  srcEl.scrollTop = Math.max(0, (row - 2) * LINE_H - LINE_H);
  gutterEl.scrollTop = srcEl.scrollTop;

  const card = rowsEl.querySelector(`.rowcard[data-row="${row}"]`);
  if (card) {
    card.scrollIntoView({ block: 'nearest' });
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 900);
  }
  markActiveRow(row);
}

/* 光标所在行 ↔ 行卡片/图解行 联动高亮 */
function markActiveRow(row) {
  rowsEl.querySelectorAll('.rowcard.active')
    .forEach(c => c.classList.toggle('active', +c.dataset.row === row));
  chartEl.querySelectorAll('text.rowlabel')
    .forEach(t => t.classList.toggle('active', +t.dataset.row === row));
}

function cursorRow() {
  const pos = srcEl.selectionStart;
  const text = srcEl.value;
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
    const lab = svgEl('text', {
      x: LEFT - 8, y: y + cell / 2, 'text-anchor': 'end',
      'dominant-baseline': 'central', 'font-size': 11,
      fill: row.status === 'error' ? '#c62828' : '#8a8378',
      class: 'rowlabel', 'data-row': row.row, 'data-s': 0,
      'data-e': lineRange(row.row).len, cursor: 'pointer',
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
  const g = svgEl('g', {
    class: 'cell', 'data-row': rowNo, 'data-s': st.s, 'data-e': st.e,
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

  const title = svgEl('title');
  title.textContent =
    `第 ${rowNo} 行 · 第 ${idx + 1} 针 · ${SYM_NAME[st.sym]}` +
    ` · 源第 ${st.s + 1}–${st.e} 列`;
  g.append(title);
  return g;
}

/* ---------- 事件 ---------- */

srcEl.addEventListener('input', scheduleCompile);
castEl.addEventListener('input', scheduleCompile);
srcEl.addEventListener('scroll', () => { gutterEl.scrollTop = srcEl.scrollTop; });
srcEl.addEventListener('keyup', () => markActiveRow(cursorRow()));
srcEl.addEventListener('click', () => markActiveRow(cursorRow()));

// 行卡片 / 图解中的点击 → 回到源码（事件委托）
document.addEventListener('click', ev => {
  const t = ev.target.closest('[data-row][data-s]');
  if (!t) return;
  revealSource(+t.dataset.row, +t.dataset.s, +t.dataset.e);
});

/* ---------- 启动 ---------- */

srcEl.value = DEFAULT_SOURCE;
doCompile();
