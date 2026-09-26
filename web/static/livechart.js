/*
 * The live chart: one feed's candles with every other feed's close drawn
 * over them, each session's trades marked on the bars they happened in, and
 * under it a strip with the skew of every feed against the reference.
 *
 * Hand drawn on a canvas for the reasons app.js gives, with the same
 * gestures - wheel zooms on the bar under the pointer, drag pans, an axis
 * dragged stretches, shift and a drag measures, ctrl and a drag zooms onto a
 * box, the keys of chartKey() in menu.js, and hover puts the numbers in the
 * readout rather than in a tooltip over the candles.
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
const AXIS_DRAG = 150;     // pixels of drag on an axis that stretch it e-fold
const BOX_MIN = 8;         // pixels a zoom box needs either way to count
const CHART_H = 380;       // the chart's height, which the layer over it shares
const PADDING_BARS = 12;   // bars kept either side of a zoomed trade
const AXIS = { left: 66, right: 14, top: 12, bottom: 26 };
// the strip shares the chart's margins: a bar of one sits over the same bar
// of the other or the strip is worse than nothing
const STRIP = { left: AXIS.left, right: AXIS.right, top: 14, bottom: 6 };
// a feed is told apart by a fixed dash, not a colour of its own: the data has
// three colours and a fourth does not pass (web/DESIGN.md § 3)
const PROVIDER_DASH = { twelvedata: DASHES[0], ig: DASHES[1], capital: DASHES[2], etoro: DASHES[3],
                        mt5: DASHES[4], ib: DASHES[5], oanda: DASHES[6] };
const dashOf = (provider) => PROVIDER_DASH[provider] || DASHES[7];

const chart = $('live-chart'), strip = $('live-skew');
const ctx = chart.getContext('2d'), sctx = strip.getContext('2d');
// the crosshair's level and tags, a measure and a zoom box, on a canvas of
// their own over the chart: following the pointer redraws only that
const over = $('live-over'), octx = over.getContext('2d');

const state = {
  bars: [],         // the sorted union of every feed's bar times
  feeds: [],        // [{feed, provider, account, at: Map t -> candle row}]
  reference: null,  // the feed the others are measured against
  pick: null,       // the feed drawn as candles
  skew: [],         // [{feed, provider, at: Map t -> dClose}] from the skew payload
  trades: [],       // [{feed, provider, isRef, signal, entryTime, exitTime, entry, exit, units, pl}]
  pairs: [],        // [{time, entryDiff, provider}] the paired trades, for the strip
  view: null,       // {from, to} indices into bars, or null for all of them
  scale: null,      // {high, low} once the prices are stretched by hand, or null to fit the bars
  range: null,      // the prices last drawn: where a stretch or a pan starts from
  pointer: null,    // {x, y} in the chart's own pixels while the pointer is over the plot
  measure: null,    // {a, b}, each {ms, price}, while a shift-drag measure is on show
  hover: null,      // bar index under the pointer
  decimals: 5,
  pip: 0.0001,
};

/* ------------------------------------------------------------- helpers */

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
  const pal = palette();
  const { width, height } = fit(chart, ctx, CHART_H);
  fit(over, octx, CHART_H);
  ctx.clearRect(0, 0, width, height);
  if (!state.bars.length) {
    fit(strip, sctx, 120);   // sizing it clears it
    ctx.fillStyle = pal.text3;
    ctx.font = '12px ' + pal.mono;
    ctx.fillText('nessuna candela ancora', 12, 24);
    return;
  }
  const view = visible();
  const n = view.to - view.from + 1;
  const plotW = width - AXIS.left - AXIS.right, plotH = height - AXIS.top - AXIS.bottom;
  const step = plotW / n;
  const x = (i) => AXIS.left + (i - view.from + 0.5) * step;
  const range = state.scale || priceRange(view);
  state.range = range;
  const y = (p) => AXIS.top + (range.high - p) / (range.high - range.low) * plotH;

  grid(pal, width, range, y);
  times(pal, view, x, height, n, step);
  ctx.save();
  ctx.beginPath();
  ctx.rect(AXIS.left, AXIS.top, plotW, plotH);
  ctx.clip();
  candles(pal, view, x, y, step);
  closes(pal, view, x, y);
  markers(pal, x, y, step);
  hairline(pal, ctx, x, AXIS.top, AXIS.top + plotH);
  ctx.restore();
  drawStrip(pal, view, x, step);
  drawOver();
}

/* ------------------------------------------------------ over the chart */

// the plot as draw() last laid it out, for the pointer's side of things
function plot() {
  const view = visible(), r = state.range;
  const plotW = chart.clientWidth - AXIS.left - AXIS.right, plotH = CHART_H - AXIS.top - AXIS.bottom;
  const step = plotW / (view.to - view.from + 1);
  return { view, r, plotW, plotH, step,
           y: (v) => AXIS.top + (r.high - v) / (r.high - r.low) * plotH,
           priceAt: (py) => r.high - (py - AXIS.top) / plotH * (r.high - r.low),
           indexAt: (px) => view.from + (px - AXIS.left) / step };
}

// the pointer in the chart's own pixels (see local() in app.js)
function local(event) {
  const rect = chart.getBoundingClientRect();
  return { x: event.clientX - rect.left - chart.clientLeft,
           y: (event.clientY - rect.top - chart.clientTop) * CHART_H / chart.clientHeight };
}

// the bar and the price under the pointer, kept by time and by price
function dataAt(event) {
  const g = plot(), at = local(event);
  const i = Math.max(g.view.from, Math.min(g.view.to, Math.floor(g.indexAt(at.x))));
  return { ms: state.bars[i], price: g.priceAt(at.y) };
}

function measureOf(m) {
  return measured(m.a, m.b, state.pip, Math.abs((barAt(m.b.ms) ?? 0) - (barAt(m.a.ms) ?? 0)), null);
}

// The layer over the chart: the level under the pointer with its price and
// time tagged on the axes (the upright is hairline()'s), a measure, a zoom box
function drawOver() {
  octx.clearRect(0, 0, chart.clientWidth, CHART_H);
  if (!state.bars.length || !state.range) return;
  const pal = palette(), g = plot();
  const xAt = (ms) => AXIS.left + ((barAt(ms) ?? -1) - g.view.from + 0.5) * g.step;
  const m = state.measure, box = pan && pan.box,
    // a pointer that is not a number (no layout yet) draws no crosshair
    at = state.pointer && isFinite(state.pointer.x) && isFinite(state.pointer.y) ? state.pointer : null;
  const colour = m && m.b.price < m.a.price ? pal.down : pal.up;
  octx.save();
  octx.beginPath();
  octx.rect(AXIS.left, AXIS.top, g.plotW, g.plotH);
  octx.clip();
  if (m) {
    const x1 = xAt(m.a.ms), x2 = xAt(m.b.ms), y1 = g.y(m.a.price), y2 = g.y(m.b.price);
    octx.fillStyle = octx.strokeStyle = colour;
    octx.globalAlpha = 0.12;
    octx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.max(1, Math.abs(x2 - x1)), Math.max(1, Math.abs(y2 - y1)));
    octx.globalAlpha = 0.8;
    octx.lineWidth = 1;
    octx.beginPath();
    octx.moveTo(x1, y1);
    octx.lineTo(x2, y2);
    octx.stroke();
  }
  if (box) {
    octx.strokeStyle = octx.fillStyle = pal.text;
    octx.globalAlpha = 0.06;
    octx.fillRect(Math.min(box.x0, box.x1), Math.min(box.y0, box.y1), Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
    octx.globalAlpha = 0.7;
    octx.setLineDash([4, 3]);
    octx.strokeRect(Math.min(box.x0, box.x1) + 0.5, Math.min(box.y0, box.y1) + 0.5,
                    Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
  }
  if (at) {
    octx.strokeStyle = pal.text;
    octx.globalAlpha = 0.45;
    octx.lineWidth = 1;
    octx.setLineDash([3, 3]);
    octx.beginPath();
    octx.moveTo(AXIS.left, Math.round(at.y) + 0.5);
    octx.lineTo(AXIS.left + g.plotW, Math.round(at.y) + 0.5);
    octx.stroke();
  }
  octx.restore();
  if (at) {
    const i = Math.max(g.view.from, Math.min(g.view.to, Math.floor(g.indexAt(at.x))));
    axisTag(octx, pal, price(g.priceAt(at.y)), AXIS.left - 2, at.y, 'left');
    axisTag(octx, pal, stamp(state.bars[i], true), AXIS.left + (i - g.view.from + 0.5) * g.step,
            CHART_H - AXIS.bottom + 3, 'bottom');
  }
  if (m) axisTag(octx, pal, measureOf(m), xAt(m.b.ms) + 10, g.y(m.b.price) + 8, 'at', colour);
}

// a zoom box let go of: its bars and its prices, the prices set by hand; one
// too small to have been meant is dropped
function boxZoom(box) {
  pan.box = null;
  if (Math.abs(box.x1 - box.x0) < BOX_MIN || Math.abs(box.y1 - box.y0) < BOX_MIN) return;
  const g = plot();
  const a = g.indexAt(Math.min(box.x0, box.x1)), b = g.indexAt(Math.max(box.x0, box.x1));
  state.scale = { high: g.priceAt(Math.min(box.y0, box.y1)), low: g.priceAt(Math.max(box.y0, box.y1)) };
  $('chart-fit').hidden = false;
  $('chart-follow').checked = false;
  zoomTo(a, b - a);
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

function grid(pal, width, range, y) {
  const lines = 6;
  ctx.lineWidth = 1;
  ctx.font = '11px ' + pal.mono;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= lines; i++) {
    const p = range.low + (range.high - range.low) * (i / lines);
    const py = Math.round(y(p)) + 0.5;
    ctx.strokeStyle = pal.grid;
    ctx.beginPath();
    ctx.moveTo(AXIS.left, py);
    ctx.lineTo(width - AXIS.right, py);
    ctx.stroke();
    ctx.fillStyle = pal.text3;
    ctx.fillText(price(p), AXIS.left - 8, py);
  }
}

function times(pal, view, x, height, n, step) {
  ctx.fillStyle = pal.text3;
  ctx.font = '11px ' + pal.mono;
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

function candles(pal, view, x, y, step) {
  const pick = state.feeds.find((f) => f.feed === state.pick);
  if (!pick) return;
  const bodyW = Math.max(1, Math.min(14, step * 0.7));
  for (let i = view.from; i <= view.to; i++) {
    const c = pick.at.get(state.bars[i]);
    if (!c) continue;
    const [, o, h, l, close] = c;
    const cx = x(i);
    ctx.strokeStyle = ctx.fillStyle = close >= o ? pal.up : pal.down;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, y(h));
    ctx.lineTo(Math.round(cx) + 0.5, y(l));
    ctx.stroke();
    const top = y(Math.max(o, close)), bottom = y(Math.min(o, close));
    ctx.fillRect(cx - bodyW / 2, top, bodyW, Math.max(1, bottom - top));
  }
}

// every other feed's close as a thin line on the same axis, told apart by its
// dash and not a colour of its own; a bar a feed has not served is a gap, not
// a line drawn through where it ought to be
function closes(pal, view, x, y) {
  ctx.save();
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.9;
  ctx.strokeStyle = pal.text3;
  for (const f of state.feeds) {
    if (f.feed === state.pick) continue;
    ctx.setLineDash(dashOf(f.provider));
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
function markers(pal, x, y, step) {
  const pick = state.feeds.find((f) => f.feed === state.pick);
  const size = step >= 4 ? 7 : 4;
  for (const t of state.trades) {
    const a = barAt(t.entryTime), b = barAt(t.exitTime);
    if (a === null) continue;
    // the marker cannot carry a dash (it is filled, not a line), so it is
    // coloured by direction instead - the triangle already points that way
    const long = t.units > 0;
    const c = long ? pal.up : pal.down;
    ctx.save();
    ctx.strokeStyle = ctx.fillStyle = c;
    ctx.lineWidth = 1.5;
    const shape = () => t.isRef ? ctx.stroke() : ctx.fill();

    const candle = pick && pick.at.get(state.bars[a]);
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
        ctx.strokeStyle = t.pl > 0 ? pal.up : t.pl < 0 ? pal.down : pal.text3;
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

function hairline(pal, context, x, top, bottom) {
  if (state.hover === null) return;
  context.save();
  context.strokeStyle = pal.text;
  context.globalAlpha = 0.35;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(Math.round(x(state.hover)) + 0.5, top);
  context.lineTo(Math.round(x(state.hover)) + 0.5, bottom);
  context.stroke();
  context.restore();
}

/* ------------------------------------------------------------- the strip */

function drawStrip(pal, view, x, step) {
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

  sctx.font = '11px ' + pal.mono;
  sctx.textBaseline = 'middle';
  sctx.textAlign = 'right';
  sctx.lineWidth = 1;
  for (const v of [high, 0, low]) {
    const py = Math.round(y(v)) + 0.5;
    sctx.strokeStyle = v === 0 ? pal.border : pal.grid;
    sctx.beginPath();
    sctx.moveTo(STRIP.left, py);
    sctx.lineTo(STRIP.left + plotW, py);
    sctx.stroke();
    sctx.fillStyle = pal.text3;
    sctx.fillText(signed(v), STRIP.left - 8, py);
  }

  sctx.save();
  sctx.beginPath();
  sctx.rect(STRIP.left, STRIP.top, plotW, plotH);
  sctx.clip();
  for (const l of state.skew) {
    sctx.strokeStyle = pal.text3;
    sctx.lineWidth = 1.25;
    sctx.setLineDash(dashOf(l.provider));
    sctx.beginPath();
    let drawing = false;
    for (let i = view.from; i <= view.to; i++) {
      const v = l.at.get(state.bars[i]);
      if (v === null || v === undefined) { drawing = false; continue; }
      if (drawing) sctx.lineTo(x(i), y(v));
      else { sctx.moveTo(x(i), y(v)); drawing = true; }
    }
    sctx.stroke();
    sctx.setLineDash([]);
  }
  // each paired trade: how far from the simulated entry the broker filled -
  // a filled bar cannot carry a dash, and the sign is not long/short, so it
  // stays neutral (web/DESIGN.md § 3)
  const barW = Math.max(2, Math.min(6, step * 0.5));
  sctx.globalAlpha = 0.85;
  sctx.fillStyle = pal.text3;
  for (const p of state.pairs) {
    const i = barAt(p.time);
    if (!inView(i)) continue;
    const top = Math.min(y(0), y(p.entryDiff));
    sctx.fillRect(x(i) - barW / 2, top, barW, Math.max(1, Math.abs(y(0) - y(p.entryDiff))));
  }
  sctx.globalAlpha = 1;
  hairline(pal, sctx, x, STRIP.top, STRIP.top + plotH);
  sctx.restore();

  sctx.textAlign = 'left';
  sctx.textBaseline = 'top';
  let lx = STRIP.left + 6;
  // a feed's key here is its dash, drawn as a short sample line, same as its
  // trace above - there is no DOM legend on this canvas to carry a dashSample
  const label = (text) => {
    sctx.fillStyle = pal.text3;
    sctx.fillText(text, lx, 2);
    lx += sctx.measureText(text).width + 12;
  };
  const legend = (text, dash) => {
    sctx.strokeStyle = pal.text3;
    sctx.lineWidth = 1.5;
    sctx.setLineDash(dash);
    sctx.beginPath();
    sctx.moveTo(lx, 6);
    sctx.lineTo(lx + 16, 6);
    sctx.stroke();
    sctx.setLineDash([]);
    lx += 20;
    label(text);
  };
  label(`Δ close vs ${state.reference || 'riferimento'} (pip)`);
  for (const l of state.skew) legend(l.feed, dashOf(l.provider));
  if (!state.skew.length) label('nessuna serie di skew');
}

/* ------------------------------------------------------------ gestures */

// the prices stretched by hand, kept while new bars come in, until a double
// click on the price axis or "fit prices" gives them back to the bars
function setScale(scale) {
  state.scale = scale;
  $('chart-fit').hidden = !scale;
  draw();
}

// which part of the chart a pointer is on: the price axis down the left, the
// time axis along the bottom, or the plot. The strip below has only a plot
function zone(cv, event) {
  if (cv !== chart) return 'plot';
  const rect = cv.getBoundingClientRect();
  if (event.clientX - rect.left < AXIS.left) return 'price';
  if (event.clientY - rect.top > rect.height - AXIS.bottom) return 'time';
  return 'plot';
}

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
    // over the price axis, or with shift: the prices stretch about the one
    // under the pointer and the bars stay (and so does "segui")
    if (cv === chart && (event.shiftKey || zone(cv, event) === 'price')) {
      const r = state.range, rect = cv.getBoundingClientRect();
      const at = r.high - (event.clientY - rect.top - AXIS.top)
        / (rect.height - AXIS.top - AXIS.bottom) * (r.high - r.low);
      const k = (event.deltaY || event.deltaX) > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      return setScale({ high: at + (r.high - at) * k, low: at - (at - r.low) * k });
    }
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
    const view = visible(), where = zone(cv, event);
    // on the chart's plot, shift measures and ctrl (cmd on a Mac) boxes a stretch to zoom onto
    const mode = cv !== chart || where !== 'plot' ? where : event.shiftKey ? 'measure'
      : (event.ctrlKey || event.metaKey) ? 'box' : 'plot';
    pan = { cv, where: mode, x: event.clientX, y: event.clientY, range: state.range,
            start: cv === chart ? local(event) : null,
            from: view.from, span: view.to - view.from + 1,
            bars: (cv.clientWidth - AXIS.left - AXIS.right) / (view.to - view.from + 1),
            plotH: chart.clientHeight - AXIS.top - AXIS.bottom, moved: false };
    // a measure on show goes at the next press, whatever the press is for
    state.measure = mode === 'measure' ? { a: dataAt(event), b: dataAt(event) } : null;
    if (mode !== 'measure' && mode !== 'box') state.pointer = null;
    if (mode === 'plot') cv.style.cursor = 'grabbing';
    drawOver();
  });

  cv.addEventListener('mousemove', (event) => {
    if (!state.bars.length) return;
    if (pan) {
      const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < PAN_SLOP && !pan.moved) return;
      pan.moved = true;
      if (pan.where === 'measure') {
        state.measure.b = dataAt(event);
        state.pointer = local(event);
        $('chart-hover').textContent = measureOf(state.measure);
        // the upright is hairline()'s, on the chart itself: it follows too
        state.hover = Math.floor(barUnder(cv, event.clientX));
        return draw();
      }
      if (pan.where === 'box') {
        const at = local(event);
        pan.box = { x0: pan.start.x, y0: pan.start.y, x1: at.x, y1: at.y };
        state.pointer = at;
        state.hover = Math.floor(barUnder(cv, event.clientX));
        return draw();
      }
      const r = pan.range;
      if (pan.where === 'price') {
        // down squeezes the prices together, up pulls them apart, about the middle
        const mid = (r.high + r.low) / 2, half = (r.high - r.low) / 2 * Math.exp(dy / AXIS_DRAG);
        return setScale({ high: mid + half, low: mid - half });
      }
      if (pan.where === 'time') {
        // right fewer bars, left more, the last one on the chart staying put:
        // "segui" stays as it was
        const span = pan.span * Math.exp(-dx / AXIS_DRAG);
        return zoomTo(pan.from + pan.span - span, span);
      }
      $('chart-follow').checked = false;
      if (state.scale && pan.cv === chart) {
        const shift = dy / pan.plotH * (r.high - r.low);
        state.scale = { high: r.high + shift, low: r.low + shift };
      }
      return zoomTo(pan.from - dx / pan.bars, pan.span);
    }
    cv.style.cursor = { price: 'ns-resize', time: 'ew-resize' }[zone(cv, event)] || '';
    if (cv === chart) {
      state.pointer = zone(cv, event) === 'plot' ? local(event) : null;
      drawOver();
    }
    const i = Math.floor(barUnder(cv, event.clientX));
    const view = visible();
    if (i < view.from || i > view.to) return;
    if (i !== state.hover) { state.hover = i; draw(); }
    readout(i);
  });

  cv.addEventListener('mouseleave', () => {
    state.pointer = null;
    drawOver();
    if (state.hover === null) return;
    state.hover = null;
    $('chart-hover').textContent = '';
    draw();
  });
}
window.addEventListener('mouseup', () => {
  if (pan && pan.box) boxZoom(pan.box);
  // a shift-click that never moved measured nothing
  if (pan && pan.where === 'measure' && !pan.moved) state.measure = null;
  if (pan) pan.cv.style.cursor = '';
  pan = null;
  drawOver();
});
// ctrl and a click is the context menu on a Mac, and here the start of a box
chart.addEventListener('contextmenu', (event) => { if (event.ctrlKey) event.preventDefault(); });

// The keys of chartKey() in menu.js. Following, a zoom keeps the last bar the
// last one; End goes back to following, a pan or Home stops it. Esc lets go
// of a box half drawn, or else of a measure and of prices set by hand.
document.addEventListener('keydown', (event) => {
  if (!state.bars.length) return;
  const follows = $('chart-follow');
  if (event.key === 'Escape') {
    if (document.querySelector('dialog[open]')) return;
    if (pan && pan.where === 'box') { pan = null; return drawOver(); }
    state.measure = null;
    return setScale(null);
  }
  const key = chartKey(event);
  if (!key) return;
  event.preventDefault();
  const view = visible(), span = view.to - view.from + 1, count = state.bars.length;
  if (key === 'left' || key === 'right') {
    follows.checked = false;
    return zoomTo(view.from + (key === 'left' ? -span : span) / 4, span);
  }
  if (key === 'in' || key === 'out') {
    const wanted = span * (key === 'out' ? ZOOM_STEP : 1 / ZOOM_STEP);
    return zoomTo(follows.checked ? count - wanted : view.from + (span - wanted) / 2, wanted);
  }
  if (key === 'home') { follows.checked = false; return zoomTo(0, span); }
  if (key === 'end') { follows.checked = true; return zoomTo(count - span, span); }
  if (key === 'fit') setScale(null);
});

// the last hour, four, a day, or everything, following
const RANGES = { '1h': 36e5, '4h': 4 * 36e5, '1D': 864e5 };
$('chart-ranges').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-range]');
  if (!button || !state.bars.length) return;
  const count = state.bars.length;
  $('chart-follow').checked = true;
  if (button.dataset.range === 'all') return zoomTo(0, count);
  const from = (barAt(state.bars[count - 1] - RANGES[button.dataset.range]) ?? -1) + 1;
  zoomTo(from, count - from);
});
chart.addEventListener('dblclick', (event) => {
  if (zone(chart, event) === 'price' && state.scale) setScale(null);
});
$('chart-fit').addEventListener('click', () => setScale(null));
$('chart-feed').addEventListener('change', () => { state.pick = $('chart-feed').value; draw(); });
$('chart-follow').addEventListener('change', () => { if ($('chart-follow').checked) { follow(); draw(); } });
window.addEventListener('resize', draw);

window.LiveChart = { setData, draw, zoomToTrade };
})();
