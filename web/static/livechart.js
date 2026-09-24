/*
 * The live chart: one feed's candles with every other feed's close drawn
 * over them, each session's trades marked on the bars they happened in, and
 * under it a strip with the skew of every feed against the reference.
 *
 * Hand drawn on a canvas for the reasons app.js gives, with the same
 * gestures - wheel zooms on the bar under the pointer, drag pans, hover puts
 * the numbers in the readout rather than in a tooltip over the candles.
 *
 * The horizontal axis is the union of every feed's bar times, not one feed's
 * bars: a broker that has not served the last bar yet still lines up with the
 * reference that has, and what it is missing shows as a gap and not a shift.
 *
 * Nothing here knows the page: live.js hands the payloads to setData() and
 * asks for draw(). The one control read directly is the "segui" checkbox,
 * because it is the gestures here that untick it.
 *
 * Every time on the wire is epoch milliseconds of a naive UTC instant and is
 * formatted with the getUTC* accessors only - see app.js.
 */
(function () {
'use strict';

const $ = (id) => document.getElementById(id);
const MIN_BARS = 8;        // the closest the wheel will zoom
const ZOOM_STEP = 1.25;    // bars gained or lost per notch of the wheel
const PAN_SLOP = 4;        // pixels of drag that stop counting as a click
const PADDING_BARS = 12;   // bars kept either side of a zoomed trade
const AXIS = { left: 66, right: 14, top: 12, bottom: 26 };
// the strip shares the chart's margins: a bar of one sits over the same bar
// of the other or the strip is worse than nothing
const STRIP = { left: AXIS.left, right: AXIS.right, top: 14, bottom: 6 };
const FONT = '11px ui-monospace, Menlo, Consolas, monospace';

const chart = $('live-chart'), strip = $('live-skew');
const ctx = chart.getContext('2d'), sctx = strip.getContext('2d');

const state = {
  bars: [],         // the sorted union of every feed's bar times
  feeds: [],        // [{feed, provider, account, at: Map t -> candle row}]
  reference: null,  // the feed the others are measured against
  pick: null,       // the feed drawn as candles
  skew: [],         // [{feed, provider, at: Map t -> dClose}] from the skew payload
  trades: [],       // [{feed, provider, isRef, signal, entryTime, exitTime, entry, exit, units, pl}]
  pairs: [],        // [{time, entryDiff, provider}] the paired trades, for the strip
  view: null,       // {from, to} indices into bars, or null for all of them
  hover: null,      // bar index under the pointer
  decimals: 5,
  pip: 0.0001,
};

/* ------------------------------------------------------------- helpers */

const css = () => getComputedStyle(document.documentElement);
function colour(provider) {
  // one colour per provider, from the stylesheet so the tables can use the
  // same ones; a provider nobody named there is drawn dim rather than black
  return css().getPropertyValue('--feed-' + provider).trim() || css().getPropertyValue('--dim').trim();
}
const price = (v) => (v === null || v === undefined) ? '' : Number(v).toFixed(state.decimals);
const signed = (v) => (v > 0 ? '+' : '') + v.toFixed(1);
const p2 = (n) => String(n).padStart(2, '0');
function stamp(ms, withDate) {
  const d = new Date(ms);
  const time = `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
  return withDate ? `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())} ${time}` : time;
}

// the bar containing ms: the last one that opened at or before it
function barAt(ms) {
  const list = state.bars;
  if (!list.length || ms === null || ms === undefined || ms < list[0]) return null;
  let low = 0, high = list.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (list[mid] <= ms) low = mid; else high = mid - 1;
  }
  return low;
}

function visible() {
  const count = state.bars.length;
  if (!state.view) return { from: 0, to: count - 1 };
  return { from: Math.max(0, state.view.from), to: Math.min(count - 1, state.view.to) };
}

function fit(cv, context, height) {
  const ratio = window.devicePixelRatio || 1;
  const width = cv.clientWidth || cv.parentElement.clientWidth;
  cv.width = Math.floor(width * ratio);
  cv.height = Math.floor(height * ratio);
  cv.style.height = height + 'px';
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function decimalsOf() {
  // read off the closes, as app.js does: a chart that rounds harder than the
  // data hides the tick two feeds differ by
  let d = 0;
  for (const f of state.feeds) for (const c of f.at.values()) {
    const text = String(c[4]), dot = text.indexOf('.');
    if (dot >= 0) d = Math.max(d, text.length - dot - 1);
    if (d >= 6) return d;
  }
  return Math.max(d, 1);
}

/* ---------------------------------------------------------------- data */

function setData({ candlesPayload, skewPayload, trades, pairs }) {
  const payload = candlesPayload || {};
  const times = new Set();
  state.feeds = (payload.feeds || []).map((f) => {
    const at = new Map();
    for (const c of f.candles || []) { at.set(c[0], c); times.add(c[0]); }
    return { feed: f.feed, provider: f.provider, account: f.account, at };
  });
  state.bars = [...times].sort((a, b) => a - b);
  state.reference = payload.reference || null;
  if (!state.feeds.some((f) => f.feed === state.pick)) {
    state.pick = state.reference || (state.feeds[0] || {}).feed || null;
  }
  state.skew = ((skewPayload || {}).feeds || [])
    .filter((f) => f.feed !== state.reference && (f.series || []).length)
    .map((f) => ({ feed: f.feed, provider: f.provider, at: new Map(f.series.map((s) => [s[0], s[1]])) }));
  state.trades = trades || [];
  state.pairs = pairs || [];
  state.decimals = decimalsOf();
  state.pip = (skewPayload && skewPayload.pip) || 1 / Math.pow(10, state.decimals - 1);

  const select = $('chart-feed');
  const names = state.feeds.map((f) => f.feed);
  if ([...select.options].map((o) => o.value).join('|') !== names.join('|')) {
    select.textContent = '';
    for (const name of names) select.add(new Option(name, name));
  }
  select.value = state.pick;
  if ($('chart-follow').checked) follow();
}

// "segui": the right edge sticks to the last bar, keeping the span chosen
function follow() {
  if (!state.view) return;
  const count = state.bars.length, span = state.view.to - state.view.from + 1;
  state.view = span >= count ? null : { from: count - span, to: count - 1 };
}

function zoomTo(from, span) {
  const count = state.bars.length;
  span = Math.max(MIN_BARS, Math.min(Math.round(span), count));
  from = Math.max(0, Math.min(Math.round(from), count - span));
  state.view = span >= count ? null : { from, to: from + span - 1 };
  draw();
}

function zoomToTrade(signal) {
  // the broker's copy first: it is the one that was placed, the reference's
  // is the simulation it is compared with
  const trade = state.trades.find((t) => t.signal === signal && !t.isRef)
    || state.trades.find((t) => t.signal === signal);
  if (!trade) return;
  const a = barAt(trade.entryTime);
  if (a === null) return;
  const b = trade.exitTime ? (barAt(trade.exitTime) ?? state.bars.length - 1) : state.bars.length - 1;
  $('chart-follow').checked = false;
  zoomTo(a - PADDING_BARS, b - a + 1 + 2 * PADDING_BARS);
}

/* ---------------------------------------------------------------- draw */

function draw() {
  const { width, height } = fit(chart, ctx, 380);
  ctx.clearRect(0, 0, width, height);
  if (!state.bars.length) {
    fit(strip, sctx, 120);   // sizing it clears it
    ctx.fillStyle = colour('');
    ctx.font = '12px ui-monospace, monospace';
    ctx.fillText('nessuna candela ancora', 12, 24);
    return;
  }
  const view = visible();
  const n = view.to - view.from + 1;
  const plotW = width - AXIS.left - AXIS.right, plotH = height - AXIS.top - AXIS.bottom;
  const step = plotW / n;
  const x = (i) => AXIS.left + (i - view.from + 0.5) * step;
  const range = priceRange(view);
  const y = (p) => AXIS.top + (range.high - p) / (range.high - range.low) * plotH;

  grid(width, range, y);
  times(view, x, height, n, step);
  ctx.save();
  ctx.beginPath();
  ctx.rect(AXIS.left, AXIS.top, plotW, plotH);
  ctx.clip();
  candles(view, x, y, step);
  closes(view, x, y);
  markers(x, y, step);
  hairline(ctx, x, AXIS.top, AXIS.top + plotH);
  ctx.restore();
  drawStrip(view, x, step);
}

function priceRange(view) {
  const pick = state.feeds.find((f) => f.feed === state.pick);
  let high = -Infinity, low = Infinity;
  for (let i = view.from; i <= view.to; i++) {
    const t = state.bars[i];
    for (const f of state.feeds) {
      const c = f.at.get(t);
      if (!c) continue;
      // the drawn feed's whole bar, the others' closes only: their wicks are
      // not on the chart, and a range sized for them would flatten the candles
      const [h, l] = f === pick ? [c[2], c[3]] : [c[4], c[4]];
      high = Math.max(high, h); low = Math.min(low, l);
    }
  }
  // the trades' own levels join the range: an exit outside the window's
  // high and low is the case worth being able to see
  for (const t of state.trades) {
    for (const [ms, v] of [[t.entryTime, t.entry], [t.exitTime, t.exit]]) {
      const i = barAt(ms);
      if (i === null || i < view.from || i > view.to || v === null || v === undefined) continue;
      high = Math.max(high, v); low = Math.min(low, v);
    }
  }
  if (!isFinite(high)) { high = 1; low = 0; }
  if (high === low) { high += state.pip; low -= state.pip; }
  const pad = (high - low) * 0.04;
  return { high: high + pad, low: low - pad };
}

function grid(width, range, y) {
  const lines = 6;
  ctx.lineWidth = 1;
  ctx.font = FONT;
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

function times(view, x, height, n, step) {
  ctx.fillStyle = '#8b95a6';
  ctx.font = FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const wanted = Math.max(2, Math.floor((chart.clientWidth - AXIS.left) / 120));
  const every = Math.max(1, Math.ceil(n / wanted));
  let previous = '';
  for (let i = view.from; i <= view.to; i += every) {
    const ms = state.bars[i];
    const d = stamp(ms, true).slice(0, 10);
    ctx.fillText(d === previous ? stamp(ms, false) : d + ' ' + stamp(ms, false), x(i), height - AXIS.bottom + 6);
    previous = d;
  }
}

function candles(view, x, y, step) {
  const pick = state.feeds.find((f) => f.feed === state.pick);
  if (!pick) return;
  const bodyW = Math.max(1, Math.min(14, step * 0.7));
  const up = css().getPropertyValue('--up').trim(), down = css().getPropertyValue('--down').trim();
  for (let i = view.from; i <= view.to; i++) {
    const c = pick.at.get(state.bars[i]);
    if (!c) continue;
    const [, o, h, l, close] = c;
    const cx = x(i);
    ctx.strokeStyle = ctx.fillStyle = close >= o ? up : down;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, y(h));
    ctx.lineTo(Math.round(cx) + 0.5, y(l));
    ctx.stroke();
    const top = y(Math.max(o, close)), bottom = y(Math.min(o, close));
    ctx.fillRect(cx - bodyW / 2, top, bodyW, Math.max(1, bottom - top));
  }
}

// every other feed's close as a thin line on the same axis; a bar a feed
// has not served is a gap, not a line drawn through where it ought to be
function closes(view, x, y) {
  ctx.save();
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.9;
  for (const f of state.feeds) {
    if (f.feed === state.pick) continue;
    ctx.strokeStyle = colour(f.provider);
    ctx.beginPath();
    let drawing = false;
    for (let i = view.from; i <= view.to; i++) {
      const c = f.at.get(state.bars[i]);
      if (!c) { drawing = false; continue; }
      if (drawing) ctx.lineTo(x(i), y(c[4]));
      else { ctx.moveTo(x(i), y(c[4])); drawing = true; }
    }
    ctx.stroke();
  }
  ctx.restore();
}

/*
 * Each trade as one movement: a triangle at the entry on the side it was
 * taken from, a dot at the exit, a segment between them coloured by what it
 * made. The reference session's are hollow and dashed - simulated, not
 * placed - so the eye separates the two copies of the same signal.
 */
function markers(x, y, step) {
  const pick = state.feeds.find((f) => f.feed === state.pick);
  const up = css().getPropertyValue('--up').trim(), down = css().getPropertyValue('--down').trim();
  const size = step >= 4 ? 7 : 4;
  for (const t of state.trades) {
    const a = barAt(t.entryTime), b = barAt(t.exitTime);
    if (a === null) continue;
    const c = colour(t.provider);
    ctx.save();
    ctx.strokeStyle = ctx.fillStyle = c;
    ctx.lineWidth = 1.5;
    const shape = () => t.isRef ? ctx.stroke() : ctx.fill();

    const candle = pick && pick.at.get(state.bars[a]);
    const long = t.units > 0;
    const tip = candle ? (long ? y(candle[3]) + 5 : y(candle[2]) - 5) : y(t.entry);
    const base = long ? tip + size : tip - size;
    ctx.beginPath();
    ctx.moveTo(x(a), tip);
    ctx.lineTo(x(a) - size * 0.45, base);
    ctx.lineTo(x(a) + size * 0.45, base);
    ctx.closePath();
    shape();

    if (b !== null && t.exit !== null && t.exit !== undefined) {
      ctx.beginPath();
      ctx.arc(x(b), y(t.exit), 3.5, 0, Math.PI * 2);
      shape();
      if (t.entry !== null && t.entry !== undefined) {
        ctx.strokeStyle = t.pl > 0 ? up : t.pl < 0 ? down : colour('');
        if (t.isRef) ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(x(a), y(t.entry));
        ctx.lineTo(x(b), y(t.exit));
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}

function hairline(context, x, top, bottom) {
  if (state.hover === null) return;
  context.save();
  context.strokeStyle = 'rgba(223,228,236,.35)';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(Math.round(x(state.hover)) + 0.5, top);
  context.lineTo(Math.round(x(state.hover)) + 0.5, bottom);
  context.stroke();
  context.restore();
}

/* ------------------------------------------------------------- the strip */

function drawStrip(view, x, step) {
  const { width, height } = fit(strip, sctx, 120);
  sctx.clearRect(0, 0, width, height);
  const plotW = width - STRIP.left - STRIP.right, plotH = height - STRIP.top - STRIP.bottom;
  // zero always in range: the strip is read as "above or below the reference"
  let high = 0, low = 0;
  const inView = (i) => i !== null && i >= view.from && i <= view.to;
  for (let i = view.from; i <= view.to; i++) {
    for (const l of state.skew) {
      const v = l.at.get(state.bars[i]);
      if (v !== null && v !== undefined) { high = Math.max(high, v); low = Math.min(low, v); }
    }
  }
  for (const p of state.pairs) if (inView(barAt(p.time))) { high = Math.max(high, p.entryDiff); low = Math.min(low, p.entryDiff); }
  if (high === low) { high = 1; low = -1; }
  const pad = (high - low) * 0.1;
  high += pad; low -= pad;
  const y = (v) => STRIP.top + (high - v) / (high - low) * plotH;

  sctx.font = FONT;
  sctx.textBaseline = 'middle';
  sctx.textAlign = 'right';
  sctx.lineWidth = 1;
  for (const v of [high, 0, low]) {
    const py = Math.round(y(v)) + 0.5;
    sctx.strokeStyle = v === 0 ? '#3a4250' : '#232932';
    sctx.beginPath();
    sctx.moveTo(STRIP.left, py);
    sctx.lineTo(STRIP.left + plotW, py);
    sctx.stroke();
    sctx.fillStyle = '#8b95a6';
    sctx.fillText(signed(v), STRIP.left - 8, py);
  }

  sctx.save();
  sctx.beginPath();
  sctx.rect(STRIP.left, STRIP.top, plotW, plotH);
  sctx.clip();
  for (const l of state.skew) {
    sctx.strokeStyle = colour(l.provider);
    sctx.lineWidth = 1.25;
    sctx.beginPath();
    let drawing = false;
    for (let i = view.from; i <= view.to; i++) {
      const v = l.at.get(state.bars[i]);
      if (v === null || v === undefined) { drawing = false; continue; }
      if (drawing) sctx.lineTo(x(i), y(v));
      else { sctx.moveTo(x(i), y(v)); drawing = true; }
    }
    sctx.stroke();
  }
  // each paired trade: how far from the simulated entry the broker filled
  const barW = Math.max(2, Math.min(6, step * 0.5));
  sctx.globalAlpha = 0.85;
  for (const p of state.pairs) {
    const i = barAt(p.time);
    if (!inView(i)) continue;
    sctx.fillStyle = colour(p.provider);
    const top = Math.min(y(0), y(p.entryDiff));
    sctx.fillRect(x(i) - barW / 2, top, barW, Math.max(1, Math.abs(y(0) - y(p.entryDiff))));
  }
  sctx.globalAlpha = 1;
  hairline(sctx, x, STRIP.top, STRIP.top + plotH);
  sctx.restore();

  sctx.textAlign = 'left';
  sctx.textBaseline = 'top';
  let lx = STRIP.left + 6;
  const legend = (text, c) => {
    sctx.fillStyle = c;
    sctx.fillText(text, lx, 2);
    lx += sctx.measureText(text).width + 12;
  };
  legend(`Δ close vs ${state.reference || 'riferimento'} (pip)`, '#8b95a6');
  for (const l of state.skew) legend(l.feed, colour(l.provider));
  if (!state.skew.length) legend('nessuna serie di skew', '#8b95a6');
}

/* ------------------------------------------------------------ gestures */

function barUnder(cv, clientX) {
  const view = visible();
  const rect = cv.getBoundingClientRect();
  const step = (rect.width - AXIS.left - AXIS.right) / (view.to - view.from + 1);
  return view.from + (clientX - rect.left - AXIS.left) / step;
}

function readout(i) {
  const t = state.bars[i];
  const pick = state.feeds.find((f) => f.feed === state.pick);
  const pickClose = pick && pick.at.get(t) ? pick.at.get(t)[4] : null;
  const parts = [stamp(t, false) + ' UTC'];
  for (const f of [pick, ...state.feeds.filter((f) => f !== pick)]) {
    const c = f && f.at.get(t);
    if (!c) continue;
    const delta = f !== pick && pickClose !== null ? ` (Δ ${signed((c[4] - pickClose) / state.pip)} pip)` : '';
    parts.push(`${f.feed}: ${price(c[4])}${delta}`);
  }
  for (const tr of state.trades) {
    if (barAt(tr.entryTime) === i) parts.push(`${tr.provider} · entry · ${tr.signal ?? ''}`);
    if (tr.exitTime && barAt(tr.exitTime) === i) parts.push(`${tr.provider} · exit · ${tr.signal ?? ''}`);
  }
  $('chart-hover').textContent = parts.join(' · ');
}

let pan = null;
for (const cv of [chart, strip]) {
  cv.addEventListener('wheel', (event) => {
    if (!state.bars.length) return;
    event.preventDefault();
    $('chart-follow').checked = false;
    const view = visible();
    const span = view.to - view.from + 1;
    const pivot = barUnder(cv, event.clientX);
    const wanted = span * (event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
    // the bar under the pointer keeps its share of the window
    zoomTo(pivot - (pivot - view.from) / span * wanted, wanted);
  }, { passive: false });

  cv.addEventListener('mousedown', (event) => {
    if (!state.bars.length) return;
    event.preventDefault();
    cv.style.cursor = 'grabbing';
    const view = visible();
    pan = { cv, x: event.clientX, from: view.from, span: view.to - view.from + 1,
            bars: (cv.clientWidth - AXIS.left - AXIS.right) / (view.to - view.from + 1), moved: false };
  });

  cv.addEventListener('mousemove', (event) => {
    if (!state.bars.length) return;
    if (pan) {
      const moved = event.clientX - pan.x;
      if (Math.abs(moved) < PAN_SLOP && !pan.moved) return;
      pan.moved = true;
      $('chart-follow').checked = false;
      return zoomTo(pan.from - moved / pan.bars, pan.span);
    }
    const i = Math.floor(barUnder(cv, event.clientX));
    const view = visible();
    if (i < view.from || i > view.to) return;
    if (i !== state.hover) { state.hover = i; draw(); }
    readout(i);
  });

  cv.addEventListener('mouseleave', () => {
    if (state.hover === null) return;
    state.hover = null;
    $('chart-hover').textContent = '';
    draw();
  });
}
window.addEventListener('mouseup', () => {
  if (pan) pan.cv.style.cursor = '';
  pan = null;
});
$('chart-feed').addEventListener('change', () => { state.pick = $('chart-feed').value; draw(); });
$('chart-follow').addEventListener('change', () => { if ($('chart-follow').checked) { follow(); draw(); } });
window.addEventListener('resize', draw);

window.LiveChart = { setData, draw, zoomToTrade };
})();
