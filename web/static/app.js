/*
 * The chart, the trade list, and the one interaction that ties them together:
 * click a trade and the chart zooms onto it with its entry, exit, stop and
 * target drawn in.
 *
 * The chart is drawn by hand on a canvas. That is a deliberate choice and the
 * reasoning is in web/service.py: a CDN script would mean this page only works
 * with an internet connection, which is a strange property for a tool that
 * reads a local file, and a vendored minified library would be code nobody
 * here can review sitting next to code that is commented line by line. What
 * it costs is the hundred lines below that turn prices into pixels.
 *
 * Every timestamp on the wire is epoch milliseconds for a naive UTC instant,
 * and every one of them is formatted back with the getUTC* accessors. Using
 * the local ones would move every candle by the viewer's own offset - the
 * same mistake data/etoro.py documents having made for real.
 */

const PADDING_BARS = 12;   // bars kept either side of a zoomed trade
const AXIS = { left: 66, right: 14, top: 12, bottom: 26 };

const state = {
  data: null,        // the last backtest payload
  view: null,        // {from, to} indices into data.candles, or null for all
  selected: null,    // index into data.trades
  hover: null,       // index into data.candles
  decimals: 5,
  forms: {},         // strategy -> its parameter fields, from /api/stores
};

const $ = (id) => document.getElementById(id);
const canvas = $('chart');
const ctx = canvas.getContext('2d');

/* ------------------------------------------------------------- formatting */

function decimalsOf(candles) {
  // Read the precision off the data rather than off a table: the store holds
  // what the broker served, and a chart that rounds harder than the data is a
  // chart that hides the tick a level was reached by.
  let d = 0;
  for (let i = 0; i < candles.length && d < 6; i++) {
    const text = String(candles[i][4]);
    const dot = text.indexOf('.');
    if (dot >= 0) d = Math.max(d, text.length - dot - 1);
  }
  return Math.max(d, 1);
}

const price = (v) => (v === null || v === undefined) ? '' : v.toFixed(state.decimals);
const pl = (v) => (v === null || v === undefined) ? '' : v.toFixed(Math.min(state.decimals + 1, 8));

function stamp(ms, withDate = true) {
  if (ms === null || ms === undefined) return '';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  const time = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  if (!withDate) return time;
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${time}`;
}

const day = (ms) => stamp(ms).slice(0, 10);

function percent(v) {
  return (v === null || v === undefined) ? 'n/a' : (v * 100).toFixed(1) + '%';
}

/* ------------------------------------------------------------------ chart */

function visible() {
  const candles = state.data ? state.data.candles : [];
  if (!candles.length) return { candles: [], from: 0, to: 0 };
  const from = state.view ? Math.max(0, state.view.from) : 0;
  const to = state.view ? Math.min(candles.length - 1, state.view.to) : candles.length - 1;
  return { candles: candles.slice(from, to + 1), from, to };
}

function levels(trade) {
  // The prices a selected trade puts on the chart. They join the price range
  // so that a stop just outside the window's own high/low is still drawn -
  // a level you cannot see is a level you cannot check.
  if (!trade) return [];
  return [trade.entryPrice, trade.exitPrice, trade.stopLoss, trade.takeProfit]
    .filter((v) => v !== null && v !== undefined);
}

function scales(view, extra) {
  let high = -Infinity, low = Infinity;
  for (const c of view.candles) {
    high = Math.max(high, c[2], c[5]);
    low = Math.min(low, c[3], c[8]);
  }
  for (const v of extra) { high = Math.max(high, v); low = Math.min(low, v); }
  if (!isFinite(high) || !isFinite(low)) { high = 1; low = 0; }
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.06;
  return { high: high + pad, low: low - pad };
}

function resize() {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const height = 420;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  canvas.style.height = height + 'px';
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function draw() {
  const { width, height } = resize();
  ctx.clearRect(0, 0, width, height);
  if (!state.data || !state.data.candles.length) return;

  const view = visible();
  const trade = state.selected === null ? null : state.data.trades[state.selected];
  const zoomed = state.view !== null;
  const range = scales(view, zoomed ? levels(trade) : []);

  const plotW = width - AXIS.left - AXIS.right;
  const plotH = height - AXIS.top - AXIS.bottom;
  const n = view.candles.length;
  const step = plotW / n;
  const bodyW = Math.max(1, Math.min(14, step * 0.7));

  const y = (p) => AXIS.top + (range.high - p) / (range.high - range.low) * plotH;
  const x = (i) => AXIS.left + (i + 0.5) * step;

  grid(width, height, range, y);

  // the holding period, behind everything: the bars the trade was open for
  if (trade && trade.entryIndex !== null) {
    const a = Math.max(0, trade.entryIndex - view.from);
    const b = (trade.exitIndex === null ? n - 1 : trade.exitIndex - view.from);
    if (b >= 0 && a <= n) {
      ctx.fillStyle = 'rgba(88,166,255,.07)';
      const left = AXIS.left + Math.max(0, a) * step;
      const right = AXIS.left + Math.min(n, b + 1) * step;
      ctx.fillRect(left, AXIS.top, Math.max(1, right - left), plotH);
    }
  }

  candles(view, x, y, bodyW, zoomed);
  if (trade) overlay(trade, view, x, y, plotW, n, step);
  times(view, x, height, n, step);
}

function grid(width, height, range, y) {
  const lines = 6;
  ctx.lineWidth = 1;
  ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= lines; i++) {
    const p = range.low + (range.high - range.low) * (i / lines);
    const py = Math.round(y(p)) + 0.5;
    ctx.strokeStyle = '#232932';
    ctx.beginPath();
    ctx.moveTo(AXIS.left, py);
    ctx.lineTo(width - AXIS.right, py);
    ctx.stroke();
    ctx.fillStyle = '#8b95a6';
    ctx.fillText(price(p), AXIS.left - 8, py);
  }
}

function candles(view, x, y, bodyW, zoomed) {
  for (let i = 0; i < view.candles.length; i++) {
    const c = view.candles[i];
    const [, o, h, l, close, askH, askL, bidH, bidL] = c;
    const up = close >= o;
    const cx = x(i);

    // Zoomed in, the ask high and the bid low are drawn as faint whiskers.
    // They are the two series the fill rule actually reads - a long entry is
    // touched on the ask, its stop and target on the bid - so this is where
    // "the bar reached the level" can be checked rather than taken on trust.
    if (zoomed) {
      ctx.strokeStyle = 'rgba(139,149,166,.45)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, y(askH));
      ctx.lineTo(cx, y(bidL));
      ctx.stroke();
    }

    ctx.strokeStyle = ctx.fillStyle = up ? '#3fb68b' : '#e2555a';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, y(h));
    ctx.lineTo(Math.round(cx) + 0.5, y(l));
    ctx.stroke();

    const top = y(Math.max(o, close));
    const bottom = y(Math.min(o, close));
    ctx.fillRect(cx - bodyW / 2, top, bodyW, Math.max(1, bottom - top));
  }
}

function overlay(trade, view, x, y, plotW, n, step) {
  // The levels asked for are labelled on the right, the prices actually got
  // on the left. A trade that closed at its target has an exit and a target
  // at the same price, and two labels on the same side would sit on top of
  // each other - which is exactly the case worth being able to read.
  const line = (value, colour, dash, label, side) => {
    if (value === null || value === undefined) return;
    const py = Math.round(y(value)) + 0.5;
    ctx.save();
    ctx.setLineDash(dash);
    ctx.strokeStyle = colour;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(AXIS.left, py);
    ctx.lineTo(AXIS.left + plotW, py);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = colour;
    ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
    ctx.textBaseline = 'bottom';
    if (side === 'right') {
      ctx.textAlign = 'right';
      ctx.fillText(`${label} ${price(value)}`, AXIS.left + plotW - 4, py - 3);
    } else {
      ctx.textAlign = 'left';
      ctx.fillText(`${label} ${price(value)}`, AXIS.left + 4, py - 3);
    }
  };

  line(trade.takeProfit, '#3fb68b', [5, 4], 'target', 'right');
  line(trade.stopLoss, '#e2555a', [5, 4], 'stop', 'right');
  line(trade.entryPrice, '#58a6ff', [], 'entry', 'left');
  line(trade.exitPrice, '#c8a2ff', [], 'exit', 'left');

  const marker = (index, value, colour) => {
    if (index === null || index === undefined || value === null) return;
    const i = index - view.from;
    if (i < 0 || i >= n) return;
    const cx = x(i);
    const cy = y(value);
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = colour;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, AXIS.top);
    ctx.lineTo(Math.round(cx) + 0.5, AXIS.top + (canvas.clientHeight - AXIS.top - AXIS.bottom));
    ctx.stroke();
    ctx.globalAlpha = 1;
  };
  marker(trade.entryIndex, trade.entryPrice, '#58a6ff');
  marker(trade.exitIndex, trade.exitPrice, '#c8a2ff');
}

function times(view, x, height, n, step) {
  ctx.fillStyle = '#8b95a6';
  ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const wanted = Math.max(2, Math.floor((canvas.clientWidth - AXIS.left) / 120));
  const every = Math.max(1, Math.ceil(n / wanted));
  let previous = '';
  for (let i = 0; i < n; i += every) {
    const ms = view.candles[i][0];
    const d = day(ms);
    const label = d === previous ? stamp(ms, false) : d + ' ' + stamp(ms, false);
    previous = d;
    ctx.fillText(label, x(i), height - AXIS.bottom + 6);
  }
}

/* ------------------------------------------------------------------ table */

function outcomeCell(trade) {
  if (trade.outcome === 'TAKE_PROFIT_ORDER') return ['tp', 'target'];
  if (trade.outcome === 'STOP_LOSS_ORDER') return ['sl', 'stop'];
  return ['open', 'still open'];
}

function renderTrades() {
  const body = $('trade-rows');
  body.textContent = '';
  const trades = state.data ? state.data.trades : [];
  if (!trades.length) {
    const row = document.createElement('tr');
    row.className = 'empty';
    const cell = document.createElement('td');
    cell.colSpan = 12;
    cell.textContent = state.data
      ? 'this run entered no trades'
      : 'run a backtest to see its trades';
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }

  let cumulative = 0;
  trades.forEach((trade, i) => {
    if (trade.pl !== null) cumulative += trade.pl;
    const [kind, label] = outcomeCell(trade);
    const row = document.createElement('tr');
    row.dataset.index = String(i);
    if (state.selected === i) row.className = 'selected';

    const cells = [
      ['', String(trade.n)],
      ['', stamp(trade.signalTime)],
      ['side ' + trade.direction, trade.direction],
      ['', stamp(trade.entryTime)],
      ['num', price(trade.entryPrice)],
      ['', stamp(trade.exitTime)],
      ['num', price(trade.exitPrice)],
      ['num', price(trade.stopLoss)],
      ['num', price(trade.takeProfit)],
      ['outcome-cell', null],
      ['num pl ' + (trade.pl > 0 ? 'good' : trade.pl < 0 ? 'bad' : ''), pl(trade.pl)],
      ['num', trade.pl === null ? '' : pl(cumulative)],
    ];
    for (const [cls, text] of cells) {
      const cell = document.createElement('td');
      if (cls) cell.className = cls.replace('outcome-cell', '');
      if (text === null) {
        const badge = document.createElement('span');
        badge.className = 'outcome ' + kind;
        badge.textContent = label;
        cell.appendChild(badge);
      } else {
        cell.textContent = text;
      }
      row.appendChild(cell);
    }
    body.appendChild(row);
  });
}

function stat(label, value, tone) {
  const box = document.createElement('div');
  box.className = 'stat';
  const l = document.createElement('span');
  l.className = 'label';
  l.textContent = label;
  const v = document.createElement('span');
  v.className = 'value' + (tone ? ' ' + tone : '');
  v.textContent = value;
  box.append(l, v);
  return box;
}

function renderReport() {
  const panel = $('report-panel');
  if (!state.data) { panel.hidden = true; return; }
  panel.hidden = false;

  const r = state.data.report;
  const c = state.data.counts;
  const report = $('report');
  report.textContent = '';
  report.append(
    stat('trades', String(r.closedTrades)),
    stat('won', String(r.wins), 'good'),
    stat('lost', String(r.losses), 'bad'),
    stat('win rate', percent(r.winRate)),
    // P&L is price x units, not money: web/service.py and the report module
    // both say so, and the label says it here as well
    stat('net (price x units)', pl(r.net), r.net > 0 ? 'good' : r.net < 0 ? 'bad' : ''),
    stat('profit factor', r.profitFactor === null ? 'n/a' : r.profitFactor.toFixed(2)),
    stat('avg win', pl(r.averageWin)),
    stat('avg loss', pl(r.averageLoss)),
    stat('max drawdown', pl(r.maxDrawdown), 'bad'),
    stat('run of wins', String(r.maxConsecutiveWins)),
    stat('run of losses', String(r.maxConsecutiveLosses)),
  );

  const counts = $('counts');
  counts.textContent = '';
  counts.append(
    stat('signals', String(c.signals)),
    stat('entered', String(c.entered)),
    stat('never entered', String(c.neverEntered)),
    stat('still open', String(c.stillOpen)),
    stat('candles', String(c.candles)),
    stat('took', state.data.elapsed + 's'),
  );
}

/* ------------------------------------------------------------ interaction */

function select(index) {
  const trades = state.data ? state.data.trades : [];
  if (index === null || index < 0 || index >= trades.length) return;
  state.selected = index;
  const trade = trades[index];

  const last = state.data.candles.length - 1;
  const from = trade.entryIndex === null ? 0 : trade.entryIndex;
  const to = trade.exitIndex === null ? last : trade.exitIndex;
  state.view = {
    from: Math.max(0, from - PADDING_BARS),
    to: Math.min(last, to + PADDING_BARS),
  };

  $('reset').hidden = false;
  $('chart-zoom').textContent =
    `trade ${trade.n} · ${trade.direction} · ${stamp(trade.entryTime)}`
    + ` → ${trade.exitTime === null ? 'still open' : stamp(trade.exitTime)}`;

  renderTrades();
  draw();
  writeURL();
  const row = document.querySelector('tr.selected');
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function resetView() {
  state.view = null;
  state.selected = null;
  $('reset').hidden = true;
  $('chart-zoom').textContent = '';
  renderTrades();
  draw();
  writeURL();
}

function message(text, kind) {
  const box = $('message');
  if (!text) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = text;
  box.className = kind === 'info' ? 'info' : '';
}

/* ------------------------------------------------------------------ fetch */

async function ask(url) {
  // Relative for the same reason the asset tags are: the page has to work
  // both at the root of a local server and under whatever prefix a reverse
  // proxy puts it at, without either end being told which.
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

// The strategies the service will run, read from it rather than listed here:
// a list in this file is a list that is wrong the day one is added.
function fill(select, values) {
  select.textContent = '';
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
}

/*
 * Some strategies have free parameters of their own. The controls for them
 * are built from what the service sends rather than written out here: the
 * ranges live in the strategy's own configuration, and a second copy in this
 * file is a copy that is wrong the first time one of them changes. This page
 * does not know which strategy has parameters, or what any of them mean.
 */
function buildForms(forms) {
  state.forms = forms || {};
  onStrategy();
}

function currentForm() {
  return state.forms[$('strategy').value] || null;
}

function onStrategy() {
  const form = currentForm();
  const box = $('params');
  box.textContent = '';
  box.hidden = !form;
  if (!form) return;
  const legend = document.createElement('legend');
  legend.textContent = 'parameters';
  box.appendChild(legend);
  for (const field of form) box.appendChild(control(field));
}

/*
 * A menu when the strategy named the values it accepts, a number box when it
 * gave a range instead - a bit field with thirty-one useful values is a range,
 * and a menu of thirty-one entries is unreadable.
 */
function control(field) {
  const label = document.createElement('label');
  label.textContent = field.label + ' ';
  let input;
  if (field.choices) {
    input = document.createElement('select');
    fill(input, field.choices);
  } else {
    input = document.createElement('input');
    input.type = 'number';
    for (const bound of ['min', 'max', 'step']) {
      if (field[bound] !== undefined) input[bound] = String(field[bound]);
    }
  }
  input.id = field.name;
  input.value = String(field.value);
  label.appendChild(input);
  return label;
}

function formParams(params) {
  const form = currentForm();
  if (!form) return params;
  for (const field of form) params.set(field.name, $(field.name).value);
  return params;
}

async function loadStores() {
  const { instruments, strategies, params } = await ask('api/stores');
  fill($('strategy'), strategies || []);
  buildForms(params);
  const select = $('instrument');
  select.textContent = '';
  if (!instruments.length) {
    message(`No stores in the data directory. data/bulksaver.py fills it.`);
    return;
  }
  for (const row of instruments) {
    const option = document.createElement('option');
    option.value = row.instrument;
    option.textContent = row.instrument;
    option.dataset.granularities = JSON.stringify(row.granularities);
    select.appendChild(option);
  }
  select.onchange = onInstrument;
  onInstrument();
}

function onInstrument() {
  const option = $('instrument').selectedOptions[0];
  if (!option) return;
  const rows = JSON.parse(option.dataset.granularities);
  const select = $('granularity');
  select.textContent = '';
  for (const row of rows) {
    const item = document.createElement('option');
    item.value = row.granularity;
    item.textContent = `${row.granularity} (${row.bars} bars, ${day(row.from)} .. ${day(row.to)})`;
    item.dataset.from = row.from;
    item.dataset.to = row.to;
    select.appendChild(item);
  }
  // Prefer the coarsest series that is not a daily one: it is what a strategy
  // here signals on, and it is the one whose bar count fits a chart.
  const preferred = Array.from(select.options).find((o) => o.value === 'H1');
  if (preferred) preferred.selected = true;
  select.onchange = onGranularity;
  onGranularity();
}

function onGranularity() {
  const option = $('granularity').selectedOptions[0];
  if (!option) return;
  $('from').value = day(Number(option.dataset.from));
  $('to').value = day(Number(option.dataset.to));
}

async function run(event) {
  if (event) event.preventDefault();
  const params = formParams(new URLSearchParams({
    instrument: $('instrument').value,
    granularity: $('granularity').value,
    strategy: $('strategy').value,
    from: $('from').value,
    to: $('to').value,
    units: $('units').value,
  }));
  $('run').disabled = true;
  message('running the backtest…', 'info');
  try {
    const data = await ask('api/backtest?' + params.toString());
    state.data = data;
    state.decimals = decimalsOf(data.candles);
    state.view = null;
    state.selected = null;
    $('reset').hidden = true;
    $('chart-zoom').textContent = '';
    $('chart-title').textContent =
      `${data.strategy} on ${data.instrument} ${data.granularity}`
      + ` · ${day(data.from)} .. ${day(data.to)}`
      + ` · ${data.candles.length} candles`;
    message('');
    renderReport();
    renderTrades();
    draw();
    writeURL();
  } catch (error) {
    message(String(error.message || error));
  } finally {
    $('run').disabled = false;
  }
}

/* ------------------------------------------------------------- deep links */

/*
 * The address bar holds the backtest. Reloading the page runs the same one,
 * a link to it opens on the same trade, and the back button walks through
 * what was looked at - none of which is possible when the state lives only
 * in the form. It is also how the page can be driven headlessly, which is
 * how the chart is checked to actually draw something.
 */

function writeURL() {
  if (!state.data) return;
  const params = formParams(new URLSearchParams({
    instrument: state.data.instrument,
    granularity: state.data.granularity,
    // the payload's strategy carries a plugin's parameter label, which is
    // what the chart title wants and not what the form holds
    strategy: $('strategy').value,
    from: day(state.data.from),
    to: day(state.data.to),
    units: $('units').value,
  }));
  if (state.selected !== null) {
    params.set('trade', String(state.data.trades[state.selected].n));
  }
  history.replaceState(null, '', '?' + params.toString());
}

function fieldsFromURL() {
  const params = new URLSearchParams(location.search);
  if (!params.get('instrument')) return null;
  const set = (id, value) => {
    if (!value) return;
    const field = $(id);
    if (field.tagName === 'SELECT'
        && !Array.from(field.options).some((o) => o.value === value)) return;
    field.value = value;
  };
  set('instrument', params.get('instrument'));
  onInstrument();
  set('granularity', params.get('granularity'));
  onGranularity();
  set('strategy', params.get('strategy'));
  onStrategy();
  const form = currentForm();
  if (form) for (const field of form) set(field.name, params.get(field.name));
  set('from', params.get('from'));
  set('to', params.get('to'));
  set('units', params.get('units'));
  const trade = Number(params.get('trade'));
  return { trade: Number.isFinite(trade) && trade > 0 ? trade : null };
}

/* -------------------------------------------------------------- listeners */

$('controls').addEventListener('submit', run);
$('strategy').addEventListener('change', onStrategy);
$('reset').addEventListener('click', resetView);

$('trade-rows').addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-index]');
  if (row) select(Number(row.dataset.index));
});

document.addEventListener('keydown', (event) => {
  if (event.target.matches('input, select, button')) return;
  if (event.key === 'Escape') return resetView();
  if (!state.data || !state.data.trades.length) return;
  if (event.key === 'ArrowDown' || event.key === 'j') {
    event.preventDefault();
    select(state.selected === null ? 0 : state.selected + 1);
  }
  if (event.key === 'ArrowUp' || event.key === 'k') {
    event.preventDefault();
    select(state.selected === null ? 0 : state.selected - 1);
  }
});

// The hovered bar's own numbers, in the chart's header rather than in a
// floating tooltip: a tooltip over a candle covers the candles next to it,
// which are the ones being compared with it.
canvas.addEventListener('mousemove', (event) => {
  if (!state.data || !state.data.candles.length) return;
  const view = visible();
  const rect = canvas.getBoundingClientRect();
  const step = (rect.width - AXIS.left - AXIS.right) / view.candles.length;
  const i = Math.floor((event.clientX - rect.left - AXIS.left) / step);
  const candle = view.candles[i];
  if (!candle) return;
  $('chart-zoom').textContent =
    `${stamp(candle[0])}  O ${price(candle[1])}  H ${price(candle[2])}`
    + `  L ${price(candle[3])}  C ${price(candle[4])}`;
});

canvas.addEventListener('mouseleave', () => {
  if (state.selected === null) { $('chart-zoom').textContent = ''; return; }
  const trade = state.data.trades[state.selected];
  $('chart-zoom').textContent =
    `trade ${trade.n} · ${trade.direction} · ${stamp(trade.entryTime)}`
    + ` → ${trade.exitTime === null ? 'still open' : stamp(trade.exitTime)}`;
});

window.addEventListener('resize', draw);

async function start() {
  await loadStores();
  const wanted = fieldsFromURL();
  if (!wanted) return;
  await run();
  if (wanted.trade !== null && state.data) {
    const index = state.data.trades.findIndex((t) => t.n === wanted.trade);
    if (index >= 0) select(index);
  }
  // said out loud, so a link to a trade that a re-run no longer produces does
  // not silently show the whole range as though nothing was asked for
  if (wanted.trade !== null && state.selected === null) {
    message(`this run has no trade ${wanted.trade}`, 'info');
  }
}

start().catch((error) => message(String(error.message || error)));
