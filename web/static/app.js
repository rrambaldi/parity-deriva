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
const SWING_BARS = 5;      // bars either side that confirm a swing high or low
const MAX_LEVELS = 12;     // swing lines drawn at once, most tested first
const LEVEL_NEAR = 0.01;   // share of the run's range two swings share a line at
const MIN_BARS = 8;        // the closest the wheel will zoom
const ZOOM_STEP = 1.25;    // bars gained or lost per notch of the wheel
const PAN_SLOP = 4;        // pixels of drag that stop counting as a click
const AXIS = { left: 66, right: 14, top: 12, bottom: 26 };
// the service's own ceiling on a window (web/service.py MAX_CANDLES): asking
// for more is refused, so the ladder does not ask
const MAX_FETCH = 5000;

const state = {
  data: null,        // the last backtest payload
  view: null,        // {from, to} indices into data.candles, or null for all
  selected: null,    // index into data.trades
  hover: null,       // index into data.candles
  decimals: 5,
  forms: {},         // strategy -> its parameter fields, from /api/stores
  about: {},         // strategy -> what it says it does, from /api/stores
  defaults: {},      // strategy -> {instrument, granularity} it is written for
  levels: true,      // draw the swing levels, toggled by the button
  slope: 0,          // 0 = no shading, else the threshold's place in the list
  detail: 'auto',    // the granularity the chart draws at, or 'auto'
  series: null,      // {granularity, candles, from, to} when it is not the run's own
  fetching: false,   // one request at a time: the wheel would queue a dozen
  tuning: null,      // the timer that lets the wheel settle before fetching
  swings: [],        // the levels of the current payload, see levelsOf()
  swingsFor: null,   // the payload they were computed from
  runId: null,       // the saved run on show, if it is one
  sweepRef: null,    // {sweep, run} when embedded from the simulate page
  favourites: [],    // the forms starred to trade live, from /api/favourites
};

const $ = (id) => document.getElementById(id);

/*
 * The results alone, for the simulate page: it opens one run of a sweep in a
 * full page dialog with this page inside it (?embed=1&<fields>), so the
 * chart, the report and the trades are one mask and not two copies of it.
 * The form is still here - hidden - because it is what a run is made from.
 */
const EMBED = new URLSearchParams(location.search).has('embed');
if (EMBED) document.documentElement.classList.add('embed');
const canvas = $('chart');
const ctx = canvas.getContext('2d');

// The capital chart's own axis. A wider left margin than the price chart's:
// a balance carries its whole starting figure plus the decimals the moves
// happen in, so the labels are longer than a price. The two canvases
// therefore do not line up pixel for pixel, which they are not meant to -
// this one always shows the whole run while the other one zooms.
const EQ_AXIS = { left: 92, right: 14, top: 12, bottom: 22 };
const equityCanvas = $('equity');
const ectx = equityCanvas.getContext('2d');

// The levels panel keeps a wide right margin: every line is labelled with its
// price and its count out there, which is the whole reason to have a second
// chart of the same thing rather than more ink on the first.
const LV_AXIS = { left: 66, right: 96, top: 12, bottom: 22 };
const levelsCanvas = $('levels-chart');
const lctx = levelsCanvas.getContext('2d');

// The strip under the chart, for the curves that are not on the price axis.
// Its left and right margins are the price chart's, because the two are read
// together: a bar of one sits over the same bar of the other, or the strip is
// worse than nothing.
const PN_AXIS = { left: AXIS.left, right: AXIS.right, top: 10, bottom: 16 };
const panelCanvas = $('curve-chart');
const pctx = panelCanvas.getContext('2d');

/* ------------------------------------------------------------- formatting */

/*
 * What one pip of the instrument on screen is, as a price difference.
 *
 * Ten ticks, which is the rule lib/utils.pipSize uses on the other side - and
 * read off the same precision the prices are drawn with, so the page needs no
 * table of its own to fall out of step with.
 */
function pipSize() {
  // a division rather than a negative power: Math.pow(10, -4) is
  // 0.00009999999999999999, and a pip that is not the number it is named
  // after turns every count into a rounding story
  return 1 / Math.pow(10, state.decimals - 1);
}

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

// A position size. Whole units read as whole units; a fractional one keeps
// the two decimals the money manager rounds to.
const size = (v) => (v === null || v === undefined) ? ''
  : Math.abs(v).toFixed(Number.isInteger(v) ? 0 : 2);

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

/* ------------------------------------------------------------ timeframe */

/*
 * The chart's own granularity, which is not the run's.
 *
 * A strategy signals on the bars it was written for and the run is those bars:
 * the trades, the report, the levels and the slope reading all belong to them.
 * What this changes is the *drawing* - zoom far enough into an H4 chart and
 * the four hour candle is opened up into the M5 bars it was made of, which is
 * where the order actually rested. Nothing is re-run and no trade moves: the
 * marks are placed by time, so a trade sits on whichever bar of whatever
 * series covers the instant it was filled.
 *
 * The ladder is the store's, read from /api/stores, and never coarser than
 * the run itself while it is on auto: a chart that quietly showed daily
 * candles for an H4 strategy would be showing bars the rule never saw.
 */
const MINUTES = { M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240, D: 1440, W: 10080 };
const TARGET_PX = 8;   // the candle width the auto ladder aims at
const WIDEST_PX = 24;  // wider than this and the chart is being asked for detail
const PAD_SPANS = 1;   // windows fetched either side of the view, for panning

function bars() {
  if (state.series) return state.series.candles;
  return state.data ? state.data.candles : [];
}

function drawnAt() {
  if (state.series) return state.series.granularity;
  return state.data ? state.data.granularity : null;
}

function runAt() { return state.data ? state.data.granularity : null; }

/* The granularities this instrument can be drawn at, finest first. */
function ladder() {
  const name = state.data && state.data.instrument;
  const row = (state.instruments || []).find((r) => r.instrument === name);
  return (row ? row.granularities : [])
    .filter((g) => MINUTES[g.granularity])
    .sort((a, b) => MINUTES[a.granularity] - MINUTES[b.granularity]);
}

/*
 * Bars per millisecond, from the store's own index rather than from the
 * interval: a market shut at the weekend has fewer bars than arithmetic
 * predicts, and picking a granularity off arithmetic picks the wrong one
 * every Friday night.
 */
function density(row) { return row.bars / Math.max(1, row.to - row.from); }

/* The bar of the drawn series covering an instant, or null if it is outside. */
function barAt(ms, list) {
  list = list || bars();
  if (!list.length || ms === null || ms === undefined) return null;
  if (ms < list[0][0]) return null;
  let low = 0, high = list.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (list[mid][0] <= ms) low = mid; else high = mid - 1;
  }
  return low;
}

/* The same question of the run's own bars, which is where the payload's
   per-bar readings are indexed. */
function runBarAt(ms) {
  return state.data ? barAt(ms, state.data.candles) : null;
}

/*
 * A drawn bar -> the run's bar covering it.
 *
 * Everything the payload carries per bar - the curves a strategy declared, the
 * slope reading - is indexed by the run's own candles. Drawn against a finer
 * series those readings become step lines, which is what they are: an SMA of
 * four hour closes does not acquire five minute detail because the chart did.
 */
function runIndex(i) {
  if (!state.series) return i;
  const list = bars();
  return (i >= 0 && i < list.length) ? runBarAt(list[i][0]) : null;
}

/* A trade's marks are placed by time, so they land on whatever is drawn. */
function tradeBar(trade, which) {
  const index = trade[which + 'Index'];
  if (!state.series) return (index === undefined ? null : index);
  return barAt(trade[which + 'Time']);
}

function viewTimes() {
  const view = visible();
  if (!view.candles.length) return null;
  return [view.candles[0][0], view.candles[view.candles.length - 1][0]];
}

function setViewByTime(fromMs, toMs) {
  const list = bars();
  if (!list.length) { state.view = null; return; }
  const from = barAt(fromMs) === null ? 0 : barAt(fromMs);
  const to = barAt(toMs) === null ? list.length - 1 : barAt(toMs);
  state.view = (from <= 0 && to >= list.length - 1)
    ? null : { from, to: Math.max(from + MIN_BARS - 1, to) };
  $('reset').hidden = state.view === null && !state.series;
}

/*
 * Which granularity the visible stretch of time wants.
 *
 * The run's own until its candles are fatter than WIDEST_PX - a chart of
 * eleven bars is a chart asking to be opened up - and then whichever series
 * comes closest to filling the plot at TARGET_PX a candle. Closest in ratio
 * and not in difference: between 80 bars and 320 for a target of 175, the
 * difference says 80 and the eye says 320.
 */
function autoPick(win) {
  const rows = ladder();
  const run = rows.find((r) => r.granularity === runAt());
  if (!run) return runAt();
  const plotW = (canvas.clientWidth || 900) - AXIS.left - AXIS.right;
  const span = Math.max(1, win[1] - win[0]);
  if (span * density(run) >= plotW / WIDEST_PX) return runAt();

  const wanted = plotW / TARGET_PX;
  let best = run, score = Infinity;
  for (const row of rows) {
    if (MINUTES[row.granularity] > MINUTES[runAt()]) continue;
    const count = span * density(row);
    if (count < 2 || count > MAX_FETCH) continue;
    const off = Math.abs(Math.log(count / wanted));
    if (off < score) { score = off; best = row; }
  }
  return best.granularity;
}

/*
 * Ask for the bars, once the wheel has stopped moving.
 *
 * Debounced because a wheel sends a dozen events a second and each one would
 * otherwise be a request for a series; and one at a time, because the answer
 * to the last one is the only one worth drawing.
 */
function retune(now) {
  if (!state.data || !state.data.candles.length) return;
  clearTimeout(state.tuning);
  state.tuning = setTimeout(tune, now ? 0 : 140);
}

async function tune() {
  const win = viewTimes();
  if (!win || state.fetching) return;
  const wanted = state.detail === 'auto' ? autoPick(win) : state.detail;

  if (wanted === runAt()) {
    if (state.series) { state.series = null; setViewByTime(win[0], win[1]); renderDetail(); draw(); }
    return;
  }
  const have = state.series;
  if (have && have.granularity === wanted
      && win[0] >= have.from && win[1] <= have.to) return;

  const row = ladder().find((r) => r.granularity === wanted);
  if (!row) return;
  const span = Math.max(1, win[1] - win[0]);
  // as much padding either side as the ceiling allows, so panning a little
  // does not go back to the store for every drag
  let pad = PAD_SPANS * span;
  while (pad > 0 && (span + 2 * pad) * density(row) > MAX_FETCH) pad = Math.floor(pad / 2);

  state.fetching = true;
  try {
    const query = new URLSearchParams({
      instrument: state.data.instrument, granularity: wanted,
      from: iso(win[0] - pad), to: iso(win[1] + pad) });
    const answer = await ask('api/candles?' + query.toString());
    if (!answer.candles.length) return;
    state.series = { granularity: wanted, candles: answer.candles,
                     from: answer.candles[0][0],
                     to: answer.candles[answer.candles.length - 1][0] };
    setViewByTime(win[0], win[1]);
    renderDetail();
    draw();
  } catch (error) {
    // the ceiling is the likely one, and it is an answer rather than a fault:
    // the chart stays on the bars it has and says why
    message(`${wanted}: ${error.message}`, 'info');
  } finally {
    state.fetching = false;
  }
}

function iso(ms) { return new Date(ms).toISOString().slice(0, 19); }

function renderDetail() {
  const select = $('detail');
  const rows = ladder();
  const run = runAt();
  const wanted = state.detail;
  select.textContent = '';
  const auto = document.createElement('option');
  auto.value = 'auto';
  auto.textContent = `auto (${drawnAt() || run || '-'})`;
  select.appendChild(auto);
  for (const row of rows) {
    const option = document.createElement('option');
    option.value = row.granularity;
    option.textContent = row.granularity === run
      ? `${row.granularity} - the bars the run read`
      : row.granularity;
    select.appendChild(option);
  }
  select.value = wanted;
  $('detail-box').hidden = rows.length < 2;
}

function visible() {
  const candles = bars();
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
  return [trade.entryPrice, trade.exitPrice, trade.stopLoss, trade.takeProfit,
          trade.stopFinal]
    .filter((v) => v !== null && v !== undefined);
}

/*
 * RG2 - the slope shading.
 *
 * The measure and the percentiles come from the service (web/service.py), so
 * this file decides nothing about them: it picks one of the thresholds the
 * payload offers and colours the bars each side of it. `state.slope` is a
 * place in that list and not a number, because a number typed here would be
 * a threshold chosen by the viewer.
 */
const SLOPE_UP = 'rgba(60,163,112,.16)';
const SLOPE_DOWN = 'rgba(200,85,61,.16)';
const SLOPE_FLAT = 'rgba(125,133,144,.10)';

function slopeLevel() {
  const info = state.data && state.data.slope;
  if (!info || !state.slope) return null;
  return info.thresholds[state.slope - 1] || null;
}

function slopeSide(value, level) {
  if (value === null || value === undefined) return null;
  if (value >= level) return 1;
  if (value <= -level) return -1;
  return 0;
}

/*
 * A column behind each bar, and the count of what was coloured. Drawn under
 * the candles and over the grid: the shading answers "was this bar
 * directional", and a bar it hides is a bar it cannot answer for.
 */
function shade(view, plotH, step) {
  const chosen = slopeLevel();
  if (!chosen) { $('slope-note').textContent = ''; return; }
  const values = state.data.slope.values;
  const counts = { 1: 0, 0: 0, '-1': 0 };
  let known = 0, flips = 0, was = null;
  for (let i = 0; i < view.candles.length; i++) {
    const at = runIndex(view.from + i);
    const side = at === null ? null : slopeSide(values[at], chosen.value);
    if (side === null) continue;
    known++;
    counts[side]++;
    if (was !== null && side !== was) flips++;
    was = side;
    ctx.fillStyle = side > 0 ? SLOPE_UP : side < 0 ? SLOPE_DOWN : SLOPE_FLAT;
    ctx.fillRect(AXIS.left + i * step, AXIS.top, Math.max(1, step), plotH);
  }
  const moving = counts[1] + counts['-1'];
  $('slope-note').textContent = known
    ? `slope \u2265 ${chosen.value} \u00b7 ${moving} of ${known} bars `
      + `directional (${(100 * moving / known).toFixed(1)}%), `
      + `${counts[1]} up, ${counts['-1']} down \u00b7 ${flips} changes`
    : 'slope: no warm bar in view';
}

/*
 * The curves a strategy declared, as flat arrays the length of the whole
 * candle list. Computed by the service from the strategy's own declaration -
 * see web/service.py - so this file knows how to draw a line and nothing
 * about what any of them mean.
 */
const CURVE_COLOURS = ['#e8a33d', '#7ee787', '#79c0ff', '#ff7b72'];

function flatten(wanted) {
  // every line of one axis, flattened: a Bollinger band is three of them, and
  // the band between the outer two is drawn separately. The colour is taken
  // from the curve's place in the declared list and not from its place here,
  // so it matches the name the legend prints.
  const out = [];
  const list = (state.data && state.data.indicators) || [];
  list.forEach((curve, i) => {
    if (!!curve.panel !== wanted) return;
    const colour = CURVE_COLOURS[i % CURVE_COLOURS.length];
    if (curve.kind === 'bollinger') {
      out.push({ values: curve.upper, colour, dash: [4, 3] },
               { values: curve.middle, colour, dash: [] },
               { values: curve.lower, colour, dash: [4, 3] });
    } else {
      out.push({ values: curve.values, colour, dash: [], label: curve.label });
    }
  });
  return out;
}

/* The curves on the price axis, which are the ones drawn over the candles. */
function indicatorSeries() { return flatten(false); }

/*
 * The ones that are not - an ATR, whose numbers are a fraction of a price and
 * would drag the whole scale to zero. They are drawn in the strip below by
 * drawPanel(), which is what the payload's `panel` flag is for.
 */
function panelSeries() { return flatten(true); }

function scales(view, extra) {
  let high = -Infinity, low = Infinity;
  for (const c of view.candles) {
    high = Math.max(high, c[2], c[5]);
    low = Math.min(low, c[3], c[8]);
  }
  // A band drawn outside the range would be clipped at the edge of the plot
  // and read as a line that goes flat, which is worse than not drawing it.
  for (const line of indicatorSeries()) {
    for (let i = view.from; i <= view.to; i++) {
      const at = runIndex(i);
      const v = at === null ? null : line.values[at];
      if (v === null || v === undefined) continue;
      high = Math.max(high, v);
      low = Math.min(low, v);
    }
  }
  for (const v of extra) { high = Math.max(high, v); low = Math.min(low, v); }
  if (!isFinite(high) || !isFinite(low)) { high = 1; low = 0; }
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.06;
  return { high: high + pad, low: low - pad };
}

function fitCanvas(cv, context, height) {
  const ratio = window.devicePixelRatio || 1;
  const width = cv.clientWidth || cv.parentElement.clientWidth;
  cv.width = Math.floor(width * ratio);
  cv.height = Math.floor(height * ratio);
  cv.style.height = height + 'px';
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function resize() {
  return fitCanvas(canvas, ctx, 420);
}

function draw() {
  const { width, height } = resize();
  ctx.clearRect(0, 0, width, height);
  if (!state.data || !state.data.candles.length) return;

  const view = visible();
  const trade = state.selected === null ? null : state.data.trades[state.selected];
  const zoomed = state.view !== null;
  // the selected trade's own levels join the range whether or not the chart
  // is zoomed. They used to join it only when it was, so a target outside the
  // whole run's high and low - which is every target that was never reached -
  // was drawn off the canvas and read as a target the page had not drawn
  const range = scales(view, levels(trade));

  const plotW = width - AXIS.left - AXIS.right;
  const plotH = height - AXIS.top - AXIS.bottom;
  const n = view.candles.length;
  const step = plotW / n;
  const bodyW = Math.max(1, Math.min(14, step * 0.7));

  const y = (p) => AXIS.top + (range.high - p) / (range.high - range.low) * plotH;
  const x = (i) => AXIS.left + (i + 0.5) * step;

  drawPanel();

  grid(width, height, range, y);
  shade(view, plotH, step);
  curves(view, x, y);
  if (state.levels) supports(view, x, y, plotW, n, range);

  // the holding period, behind everything: the bars the trade was open for
  const held = trade ? tradeBar(trade, 'entry') : null;
  if (trade && held !== null && held !== undefined) {
    const out = tradeBar(trade, 'exit');
    const a = Math.max(0, held - view.from);
    const b = (out === null || out === undefined ? n - 1 : out - view.from);
    if (b >= 0 && a <= n) {
      ctx.fillStyle = 'rgba(88,166,255,.07)';
      const left = AXIS.left + Math.max(0, a) * step;
      const right = AXIS.left + Math.min(n, b + 1) * step;
      ctx.fillRect(left, AXIS.top, Math.max(1, right - left), plotH);
    }
  }

  candles(view, x, y, bodyW, zoomed);
  if (trade) setupBox(trade, view, x, y, n, step);
  arrows(view, x, y, step);
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

/*
 * The declared curves, under the candles so a bar is never hidden by a line.
 *
 * A gap is drawn as a gap: until a curve is warm its values are null, and
 * joining across them would draw a straight line through the stretch the
 * indicator had nothing to say about - which is exactly the stretch somebody
 * checking an early trade is looking at.
 */
function curves(view, x, y) {
  for (const line of indicatorSeries()) {
    if (!line.values) continue;
    ctx.save();
    ctx.strokeStyle = line.colour;
    ctx.lineWidth = 1.25;
    ctx.setLineDash(line.dash);
    ctx.beginPath();
    let drawing = false;
    for (let i = view.from; i <= view.to; i++) {
      const at = runIndex(i);
      const v = at === null ? null : line.values[at];
      if (v === null || v === undefined) { drawing = false; continue; }
      const px = x(i - view.from), py = y(v);
      if (drawing) ctx.lineTo(px, py);
      else { ctx.moveTo(px, py); drawing = true; }
    }
    ctx.stroke();
    ctx.restore();
  }
}

/*
 * Support and resistance: the confirmed swing highs and lows of the whole
 * run, merged into one line per price.
 *
 * Three rules, and the reason for each.
 *
 * **A level is confirmed, not spotted.** A high is only known to be a high
 * once SWING_BARS have printed to its right, so its line starts there and
 * never at the swing itself. Drawing it from the swing would put a level on
 * the chart days before anyone could have traded off it, which is the same
 * look-ahead the strategies are careful about.
 *
 * **One line per price, not per swing.** The same level tested three times is
 * one level; three lines a third of a pip apart are one thick line that says
 * nothing about how often it held. Two swings within LEVEL_NEAR of the run's
 * own range are the same line, and it starts at the earlier of them - which
 * is what makes a level found early run the width of the chart.
 *
 * **The ones that were tested win.** Every swing over ten years is a grid,
 * so MAX_LEVELS of them are drawn and the ones kept are the ones price came
 * back to. Recency alone would put all twelve at the right-hand edge.
 *
 * Drawn from the candles the payload already carries rather than from a
 * levels module: this is a reading aid on a chart, not a signal, and nothing
 * on this page feeds anything that trades.
 */
function findLevels() {
  const bars = state.data ? state.data.candles : [];
  if (bars.length < SWING_BARS * 2 + 1) return [];
  let top = -Infinity, bottom = Infinity;
  for (const c of bars) { top = Math.max(top, c[2]); bottom = Math.min(bottom, c[3]); }
  // 1% of the range, and never more than half a typical bar: over ten years
  // of H1 1% is sixty pips, a band that covers the whole chart once zoomed in
  const ranges = bars.map((c) => c[2] - c[3]).sort((a, b) => a - b);
  const typical = ranges[Math.floor(ranges.length / 2)] / 2;
  const near = typical > 0 ? Math.min((top - bottom) * LEVEL_NEAR, typical)
    : (top - bottom) * LEVEL_NEAR;

  const found = [];
  for (let i = bars.length - SWING_BARS - 1; i >= SWING_BARS; i--) {
    for (const field of [2, 3]) {
      const up = field === 2;
      const level = bars[i][field];
      let swing = true;
      for (let j = i - SWING_BARS; j <= i + SWING_BARS && swing; j++) {
        if (j !== i && (up ? bars[j][2] >= level : bars[j][3] <= level)) swing = false;
      }
      if (!swing) continue;
      const same = found.find((l) => l.up === up && Math.abs(l.price - level) <= near);
      // the earlier confirmation, because that is when the level became
      // knowable; the scan runs backwards, so the one found later is earlier
      if (same) {
        same.at = i + SWING_BARS;
        same.when = bars[same.at][0];
        same.swings++;
        continue;
      }
      // `near` rides along: it is the width the level is judged by - two
      // swings inside it are one level, a bar inside it has touched it - so
      // it is what the chart has to draw. A hairline would be a picture of
      // something the code never reasons about.
      found.push({ price: level, up, at: i + SWING_BARS,
                   // and when that was, because the chart it is drawn on may
                   // be a finer series where that bar has another number
                   when: bars[i + SWING_BARS][0],
                   near, swings: 1, touches: 0 });
    }
  }

  // How many times price came back to it after it was confirmed - visits,
  // not bars. Counting bars counts a fortnight spent sitting on a level as
  // fourteen tests of it, which ranks whichever level the middle of the range
  // happened to run through above every level that was actually defended.
  for (const l of found) {
    let inside = false;
    for (let i = l.at; i < bars.length; i++) {
      const here = bars[i][3] - near <= l.price && l.price <= bars[i][2] + near;
      if (here && !inside) l.touches++;
      inside = here;
    }
  }
  // One level per band of the run's range. Ranking by touches alone puts all
  // twelve lines in whichever stretch of price the market spent longest in -
  // on ten years of EUR_USD that is a quarter of the chart carrying every
  // line and the other three quarters carrying none. A band each spreads
  // them over the range, and inside a band the most tested one wins.
  const band = (top - bottom) / MAX_LEVELS;
  const best = [];
  for (const l of found) {
    const slot = Math.min(MAX_LEVELS - 1, Math.floor((l.price - bottom) / band));
    if (!best[slot] || l.touches > best[slot].touches) best[slot] = l;
  }
  return best.filter(Boolean);
}

function levelsOf() {
  // once per payload: it is a pass over every bar for every level, and the
  // wheel redraws the chart many times a second
  if (state.swingsFor !== state.data) {
    state.swingsFor = state.data;
    state.swings = findLevels();
  }
  return state.swings;
}

function supports(view, x, y, plotW, n, range) {
  ctx.save();
  ctx.lineWidth = 1;
  for (const l of levelsOf()) {
    if (l.price > range.high || l.price < range.low) continue;
    const born = state.series ? barAt(l.when) : l.at;
    if (born === null) continue;
    const at = Math.max(0, born - view.from);
    if (at >= n) continue;
    const left = x(at) - 0.5;
    const width = AXIS.left + plotW - left;
    band(ctx, l, left, width, y);
  }
  ctx.restore();
}

/*
 * One level, drawn as what it is: a band of price with a line through the
 * middle of it.
 *
 * The width is the level's own `near` - the same figure that decides whether
 * two swings are one level and whether a bar touched it. Drawing a hairline
 * instead would be a picture of something the code never reasons about, and
 * it would read as "price stopped here, to the pip", which is not what a
 * swing high says.
 */
function band(context, l, left, width, y) {
  const top = y(l.price + l.near), bottom = y(l.price - l.near);
  context.fillStyle = l.up ? 'rgba(226,85,90,.13)' : 'rgba(63,182,139,.13)';
  context.fillRect(left, top, width, Math.max(1, bottom - top));
  context.strokeStyle = l.up ? 'rgba(226,85,90,.75)' : 'rgba(63,182,139,.75)';
  context.setLineDash([3, 3]);
  const py = Math.round(y(l.price)) + 0.5;
  context.beginPath();
  context.moveTo(left, py);
  context.lineTo(left + width, py);
  context.stroke();
  context.setLineDash([]);
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

/*
 * The candles the entry rule read, boxed.
 *
 * How many of them is the strategy's own SETUP_BARS - see web/service.py -
 * because a box the page sized itself would be the page inventing what the
 * rule looked at. A strategy that does not say gets no box.
 *
 * It ends on the signal bar and not on the entry: a pending order can be
 * filled days after the decision was made, and the candles worth looking at
 * are the ones up to the decision.
 */
function setupBox(trade, view, x, y, n, step) {
  const wide = state.data.setupBars;
  if (!wide) return;
  const at = trade.signalIndex;
  if (at === null || at === undefined) return;
  const bars = state.data.candles;
  const first = Math.max(0, at - wide + 1);

  let high = -Infinity, low = Infinity;
  for (let i = first; i <= at; i++) {
    high = Math.max(high, bars[i][2]);
    low = Math.min(low, bars[i][3]);
  }

  // the box is a stretch of time - so many of the strategy's bars back from
  // the signal - and it stays that stretch whatever the chart is drawn at
  const left_ = state.series ? barAt(bars[first][0]) : first;
  const right_ = state.series ? barAt(bars[at][0]) : at;
  if (left_ === null || right_ === null) return;
  const a = left_ - view.from, b = right_ - view.from;
  if (b < 0 || a >= n) return;
  const left = AXIS.left + Math.max(0, a) * step;
  const right = AXIS.left + Math.min(n, b + 1) * step;
  const top = y(high), bottom = y(low);
  const pad = 4;

  ctx.save();
  ctx.strokeStyle = 'rgba(230,237,243,.6)';
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.strokeRect(Math.round(left) + 0.5, Math.round(top - pad) + 0.5,
                 Math.round(right - left), Math.round(bottom - top + pad * 2));
  ctx.restore();
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
  // Where a walking stop ended up, drawn only when it is not where it was
  // ordered. Usually it is also the exit - but not when the bar gapped
  // through it, and that is the case worth being able to see: the fill is
  // past the level, never short of it.
  if (trade.stopFinal !== null && trade.stopFinal !== undefined
      && trade.stopFinal !== trade.stopLoss) {
    line(trade.stopFinal, '#ff9f45', [2, 3], 'stop moved to', 'right');
  }
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
  marker(tradeBar(trade, 'entry'), trade.entryPrice, '#58a6ff');
  marker(tradeBar(trade, 'exit'), trade.exitPrice, '#c8a2ff');

  // Entry to exit, so the trade is one movement across the chart rather than
  // two dots the eye has to join. Green or red by what it made, because the
  // slope does not say on its own: a short that fell is an arrow pointing
  // down and a trade that won.
  const a = tradeBar(trade, 'entry') - view.from,
        b = tradeBar(trade, 'exit') - view.from;
  if (trade.exitPrice === null || trade.exitPrice === undefined) return;
  if (a < 0 || a >= n || b < 0 || b >= n) return;
  const fromX = x(a), fromY = y(trade.entryPrice);
  const toX = x(b), toY = y(trade.exitPrice);
  ctx.save();
  ctx.strokeStyle = ctx.fillStyle =
    trade.pl > 0 ? '#3fb68b' : trade.pl < 0 ? '#e2555a' : '#8b95a6';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(fromX, fromY);
  ctx.lineTo(toX, toY);
  ctx.stroke();
  const angle = Math.atan2(toY - fromY, toX - fromX);
  const head = 9;
  ctx.beginPath();
  ctx.moveTo(toX, toY);
  ctx.lineTo(toX - head * Math.cos(angle - 0.4), toY - head * Math.sin(angle - 0.4));
  ctx.lineTo(toX - head * Math.cos(angle + 0.4), toY - head * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/*
 * Which way each trade was taken: a triangle at its entry, pointing up and
 * sitting under the bar for a buy, pointing down and sitting over it for a
 * sell.
 *
 * Every trade, always: the whole range is where you go looking for the one
 * you want, and a trade that is not drawn is a trade you cannot click. What
 * changes with the room available is the size - ten years of daily candles is
 * a third of a pixel per bar, so the arrows shrink to marks rather than
 * disappearing, and the selected one stays the largest of them.
 */
function arrows(view, x, y, step) {
  const trades = state.data.trades;
  const room = step >= 4;
  for (let i = 0; i < trades.length; i++) {
    const trade = trades[i];
    const entry = tradeBar(trade, 'entry');
    if (entry === null || entry === undefined) continue;
    const at = entry - view.from;
    if (at < 0 || at >= view.candles.length) continue;

    const long = trade.direction === 'long';
    const candle = view.candles[at];
    const size = i === state.selected ? 10 : (room ? 7 : 4);
    // clear of the bar it belongs to, on the side the trade was taken from
    const tip = long ? y(candle[3]) + 5 : y(candle[2]) - 5;
    const base = long ? tip + size : tip - size;
    const cx = x(at);

    ctx.beginPath();
    ctx.moveTo(cx, tip);
    ctx.lineTo(cx - size * 0.45, base);
    ctx.lineTo(cx + size * 0.45, base);
    ctx.closePath();
    ctx.fillStyle = long ? '#58a6ff' : '#e8a33d';
    ctx.fill();
    if (i === state.selected) {
      ctx.strokeStyle = '#e6edf3';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }
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


/* ------------------------------------------------------------ the capital */

/*
 * The account balance after each trade that closed, in the order the closes
 * happened.
 *
 * Read off the trades rather than off report.equity, which is a running total
 * in the order trades *opened* and starts from zero. Two trades that overlap
 * close in the other order, and a curve that draws them in entry order shows
 * a balance the account never held.
 *
 * x is the candle index the exit landed on, so the curve runs on the same
 * horizontal domain as the price chart above it: weekends take no width in
 * either, which they would in a chart drawn against the clock.
 */
function equityPoints() {
  const data = state.data;
  if (!data) return [];
  const closed = data.trades
    .filter((t) => t.exitIndex !== null && t.exitIndex !== undefined
                   && t.balance !== null && t.balance !== undefined)
    .sort((a, b) => a.exitIndex - b.exitIndex);
  // a strategy with its own engine (AB) reports each trade's balance but not
  // the capital it started from: that is the first close less what it made
  let start = data.balance;
  if ((start === null || start === undefined) && closed.length) {
    start = closed[0].balance - (closed[0].pl || 0);
  }
  if (start === null || start === undefined) return [];
  const points = [[0, start]];
  for (const t of closed) points.push([t.exitIndex, t.balance]);
  return points;
}

function amount(v, decimals) {
  return (v === null || v === undefined) ? '' : v.toFixed(decimals);
}

/*
 * How many decimals the axis needs to tell its own gridlines apart.
 *
 * P&L here is price x units, not money (see performance/report.py), so on a
 * one-unit forex run the whole curve moves in the fourth decimal of a balance
 * that starts at a hundred thousand. Rounding those labels to the two
 * decimals money would want prints six identical numbers up the axis.
 */
function amountDecimals(span) {
  if (!(span > 0)) return 2;
  return Math.min(8, Math.max(2, Math.ceil(-Math.log10(span)) + 2));
}

function drawEquity() {
  const panel = $('equity-panel');
  const points = equityPoints();
  panel.hidden = points.length < 2;
  if (panel.hidden) { $('equity-note').textContent = ''; return; }

  const { width, height } = fitCanvas(equityCanvas, ectx, 200);
  ectx.clearRect(0, 0, width, height);

  const start = points[0][1];
  const last = points[points.length - 1][1];
  const values = points.map((p) => p[1]);
  let high = Math.max(...values), low = Math.min(...values);
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.08;
  high += pad; low -= pad;
  const decimals = amountDecimals(high - low);

  const bars = state.data.candles.length;
  const plotW = width - EQ_AXIS.left - EQ_AXIS.right;
  const plotH = height - EQ_AXIS.top - EQ_AXIS.bottom;
  const step = plotW / bars;
  const x = (i) => EQ_AXIS.left + (i + 0.5) * step;
  const y = (v) => EQ_AXIS.top + (high - v) / (high - low) * plotH;

  ectx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  ectx.textAlign = 'right';
  ectx.textBaseline = 'middle';
  ectx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    ectx.strokeStyle = '#232932';
    ectx.beginPath();
    ectx.moveTo(EQ_AXIS.left, py);
    ectx.lineTo(width - EQ_AXIS.right, py);
    ectx.stroke();
    ectx.fillStyle = '#8b95a6';
    ectx.fillText(amount(v, decimals), EQ_AXIS.left - 8, py);
  }

  // where it started, so profit and loss are read against a line rather than
  // against the axis labels
  ectx.strokeStyle = '#8b95a6';
  ectx.setLineDash([4, 4]);
  ectx.beginPath();
  ectx.moveTo(EQ_AXIS.left, Math.round(y(start)) + 0.5);
  ectx.lineTo(width - EQ_AXIS.right, Math.round(y(start)) + 0.5);
  ectx.stroke();
  ectx.setLineDash([]);

  // A step, not a slope: the realised balance does not drift between closes,
  // it sits still and then jumps. Drawing it as a slope would invent a
  // reading for every bar in between.
  ectx.strokeStyle = last >= start ? '#3fb68b' : '#e2555a';
  ectx.lineWidth = 1.5;
  ectx.beginPath();
  ectx.moveTo(x(points[0][0]), y(points[0][1]));
  for (let i = 1; i < points.length; i++) {
    ectx.lineTo(x(points[i][0]), y(points[i - 1][1]));
    ectx.lineTo(x(points[i][0]), y(points[i][1]));
  }
  ectx.lineTo(width - EQ_AXIS.right, y(last));
  ectx.stroke();

  // Where the selected trade opened, on the level the curve was at then.
  //
  // At the entry and not at the close: the curve steps when a trade closes,
  // so the close is where its result is already in - and what somebody
  // following a trade wants marked is where it began, with the step it caused
  // sitting just to the right of the dot.
  const trade = state.selected === null ? null : state.data.trades[state.selected];
  if (trade && trade.entryIndex !== null && trade.entryIndex !== undefined) {
    let level = points[0][1];
    for (const p of points) if (p[0] <= trade.entryIndex) level = p[1];
    ectx.fillStyle = '#58a6ff';
    ectx.beginPath();
    ectx.arc(x(trade.entryIndex), y(level), 3.5, 0, Math.PI * 2);
    ectx.fill();
  }

  const moved = last - start;
  $('equity-note').textContent =
    `${amount(start, decimals)} \u2192 ${amount(last, decimals)}`
    + `  (${moved >= 0 ? '+' : ''}${amount(moved, decimals)}`
    + `, ${percent(moved / start)})`
    + `  \u00b7 ${points.length - 1} closed trades`
    + (state.data.risk
        // sized off the account, so the curve is in the instrument's quote
        // currency - and only that, since no conversion to the account's own
        // currency is modelled anywhere here
        ? `  \u00b7 ${(state.data.risk * 100).toFixed(2)}% risked per trade,`
          + ' reviewed monthly \u00b7 quote currency, unconverted'
        : '  \u00b7 price x units, not money');
}

/* ------------------------------------------------------------ the levels */

/*
 * The same levels again, on their own and over the whole run.
 *
 * The chart above draws them among the candles, where a level is read against
 * the bar that is testing it. This one answers the other question - where the
 * levels of this run are, how long each has been there and how often price
 * came back - and answers it without candles on top. Closed until asked for.
 */
/*
 * The strip under the chart: the declared curves that are not on the price
 * axis, over the bars the chart is showing.
 *
 * It follows the zoom and the pan, which is the only reason it is worth
 * drawing at all - an ATR read against a stretch of candles that is not the
 * stretch on screen answers a question nobody asked. Its scale is the range
 * of what is visible and not of the whole run, for the same reason.
 */
function drawPanel() {
  const section = $('curve-panel');
  const lines = panelSeries();
  section.hidden = !state.data || !state.data.candles.length || !lines.length;
  if (section.hidden || !$('curve-box').open) return;

  const { width, height } = fitCanvas(panelCanvas, pctx, 110);
  pctx.clearRect(0, 0, width, height);

  const view = visible();
  let high = -Infinity, low = Infinity;
  for (const line of lines) {
    for (let i = view.from; i <= view.to; i++) {
      const at = runIndex(i);
      const v = at === null ? null : line.values[at];
      if (v === null || v === undefined) continue;
      high = Math.max(high, v);
      low = Math.min(low, v);
    }
  }
  // nothing warm in this window: the curves are all still in their warm-up,
  // and an empty strip says that better than a flat line at an invented level
  if (!isFinite(high)) { $('curve-note').textContent = 'warming up'; return; }
  if (high === low) { high += Math.abs(high) || 1; low -= Math.abs(low) || 1; }

  const plotW = width - PN_AXIS.left - PN_AXIS.right;
  const plotH = height - PN_AXIS.top - PN_AXIS.bottom;
  const step = plotW / view.candles.length;
  const x = (i) => PN_AXIS.left + (i + 0.5) * step;
  const y = (v) => PN_AXIS.top + (high - v) / (high - low) * plotH;

  pctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  pctx.textBaseline = 'middle';
  pctx.textAlign = 'right';
  pctx.lineWidth = 1;
  // two labels and no more: this strip is read for its shape and for the
  // number at the top, and a grid of five would be most of its height
  for (const v of [high, low]) {
    const py = Math.round(y(v)) + 0.5;
    pctx.strokeStyle = '#232932';
    pctx.beginPath();
    pctx.moveTo(PN_AXIS.left, py);
    pctx.lineTo(PN_AXIS.left + plotW, py);
    pctx.stroke();
    pctx.fillStyle = '#8b95a6';
    pctx.fillText(price(v), PN_AXIS.left - 8, py);
  }

  for (const line of lines) {
    pctx.strokeStyle = line.colour;
    pctx.lineWidth = 1.25;
    pctx.beginPath();
    let drawing = false;
    for (let i = view.from; i <= view.to; i++) {
      const at = runIndex(i);
      const v = at === null ? null : line.values[at];
      if (v === null || v === undefined) { drawing = false; continue; }
      const px = x(i - view.from), py = y(v);
      if (drawing) pctx.lineTo(px, py);
      else { pctx.moveTo(px, py); drawing = true; }
    }
    pctx.stroke();
  }

  const last = lines[0].values[runIndex(view.to)];
  $('curve-note').textContent = lines.map((l) => l.label).join(', ')
    + (last === null || last === undefined ? '' : ` \u00b7 last ${price(last)}`);
}

function drawLevels() {
  const panel = $('levels-panel');
  const list = levelsOf();
  panel.hidden = !state.data || !list.length;
  if (panel.hidden) { $('levels-note').textContent = ''; return; }

  const { width, height } = fitCanvas(levelsCanvas, lctx, 240);
  lctx.clearRect(0, 0, width, height);

  const bars = state.data.candles;
  let high = -Infinity, low = Infinity;
  for (const c of bars) { high = Math.max(high, c[2]); low = Math.min(low, c[3]); }
  const pad = (high - low) * 0.04;
  high += pad; low -= pad;

  const plotW = width - LV_AXIS.left - LV_AXIS.right;
  const plotH = height - LV_AXIS.top - LV_AXIS.bottom;
  const step = plotW / bars.length;
  const x = (i) => LV_AXIS.left + (i + 0.5) * step;
  const y = (p) => LV_AXIS.top + (high - p) / (high - low) * plotH;

  lctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  lctx.textBaseline = 'middle';
  lctx.lineWidth = 1;
  lctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    lctx.strokeStyle = '#232932';
    lctx.beginPath();
    lctx.moveTo(LV_AXIS.left, py);
    lctx.lineTo(LV_AXIS.left + plotW, py);
    lctx.stroke();
    lctx.fillStyle = '#8b95a6';
    lctx.fillText(price(v), LV_AXIS.left - 8, py);
  }

  // the closes, thin and grey: a level means nothing without the price that
  // made it, and a line is all the context this panel needs
  lctx.strokeStyle = '#4a545f';
  lctx.beginPath();
  for (let i = 0; i < bars.length; i++) {
    const py = y(bars[i][4]);
    if (i) lctx.lineTo(x(i), py); else lctx.moveTo(x(i), py);
  }
  lctx.stroke();

  for (const l of list) {
    const left = x(l.at);
    band(lctx, l, left, LV_AXIS.left + plotW - left, y);
    lctx.fillStyle = l.up ? '#e2555a' : '#3fb68b';
    lctx.textAlign = 'left';
    lctx.fillText(`${price(l.price)} \u00d7${l.touches}`,
                  LV_AXIS.left + plotW + 6, Math.round(y(l.price)) + 0.5);
  }

  $('levels-note').textContent =
    `${list.length} bands, \u00b1${price(list[0].near)} wide \u00b7 each drawn`
    + ` from the bar that confirmed it, ${SWING_BARS} after the swing`
    + ` \u00b7 \u00d7n is how many times price came back into it`;
}

/* ------------------------------------------------------------------ table */

/*
 * One trade as a line of text: the same figures its row in the table carries,
 * over the chart they are drawn on.
 *
 * Built from the same helpers the table cells use, so the two cannot start
 * disagreeing about what a price or a size looks like.
 */
/* What an open trade is: not a result, the data running out under it. */
function openAtEnd() {
  return `open when the run ended, ${day(state.data.to)}`;
}

function tradeLine(trade) {
  const [, outcome] = outcomeCell(trade);
  return [
    `#${trade.n}`,
    trade.direction,
    `${size(trade.units)} units`,
    `signal ${stamp(trade.signalTime)}`,
    `in ${stamp(trade.entryTime)} @ ${price(trade.entryPrice)}`,
    trade.exitTime === null || trade.exitTime === undefined
      ? openAtEnd()
      : `out ${stamp(trade.exitTime)} @ ${price(trade.exitPrice)}`,
    `stop ${stopCell(trade)}`,
    `target ${price(trade.takeProfit)}`,
    outcome,
    `P&L ${pl(trade.pl)}`,
  ].join('  \u00b7  ');
}

function stopCell(trade) {
  // Both stops when they differ: the one it was ordered with, which is what
  // the size was worked out from, and the one that was standing when it
  // exited, which is what explains the exit.
  const ordered = price(trade.stopLoss);
  if (trade.stopFinal === null || trade.stopFinal === undefined
      || trade.stopFinal === trade.stopLoss) return ordered;
  return ordered + ' \u2192 ' + price(trade.stopFinal);
}

/*
 * How a trade ended, as a badge.
 *
 * Was: anything that was not one of the two names backtest/ledger.py uses
 *      fell through to "still open". A strategy with its own engine has its
 *      own vocabulary - ftw_ab closes on OPPOSITE_SIGNAL, STEP_EXIT and
 *      BREAKEVEN as well - so 271 of its 360 trades were labelled as running
 *      when one of them was, and the page was reporting the opposite of what
 *      the payload said.
 * Now: "still open" is read off the trade rather than guessed from the name.
 *      A trade with no exit is open; a trade with one closed, whatever it
 *      closed on, and an outcome this page has no styling for is still shown
 *      by name rather than replaced by a wrong one.
 */
function outcomeCell(trade) {
  if (trade.exitTime === null || trade.exitTime === undefined) {
    return ['open', 'open at end'];
  }
  if (trade.outcome === 'TAKE_PROFIT_ORDER') return ['tp', 'target'];
  if (trade.outcome === 'STOP_LOSS_ORDER') return ['sl', 'stop'];
  // STEP_EXIT -> "step exit". The name as the engine spells it, lowercased,
  // because a word nobody invented here cannot mean something else.
  return ['other', String(trade.outcome || 'closed').toLowerCase().replace(/_/g, ' ')];
}

const PAGE_SIZE = 100;   // trade rows drawn at once

function renderTrades() {
  const body = $('trade-rows');
  body.textContent = '';
  const trades = state.data ? state.data.trades : [];
  const pages = Math.max(1, Math.ceil(trades.length / PAGE_SIZE));
  state.page = Math.min(Math.max(0, state.page || 0), pages - 1);
  const first = state.page * PAGE_SIZE;
  const last = Math.min(trades.length, first + PAGE_SIZE);
  $('trade-pager').hidden = pages < 2;
  $('page-info').textContent =
    `trades ${first + 1}\u2013${last} of ${trades.length} \u00b7 page ${state.page + 1} of ${pages}`;
  $('page-prev').disabled = state.page === 0;
  $('page-next').disabled = state.page === pages - 1;
  $('trade-prev').hidden = $('trade-next').hidden = !trades.length;
  $('trade-prev').disabled = !trades.length || state.selected === 0;
  $('trade-next').disabled = !trades.length || state.selected === trades.length - 1;
  if (!trades.length) {
    const row = document.createElement('tr');
    row.className = 'empty';
    const cell = document.createElement('td');
    cell.colSpan = 13;
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
    // the running total counts every trade, the rows only this page's
    if (i < first || i >= last) return;
    const [kind, label] = outcomeCell(trade);
    const row = document.createElement('tr');
    row.dataset.index = String(i);
    if (state.selected === i) row.className = 'selected';

    const cells = [
      ['', String(trade.n)],
      ['', stamp(trade.signalTime)],
      ['side ' + trade.direction, trade.direction],
      // the size, because under a risk rule it is worked out per trade from
      // the distance to the stop rather than being the same every time, and
      // a number nobody can see is a number nobody can check
      ['num', size(trade.units)],
      ['', stamp(trade.entryTime)],
      ['num', price(trade.entryPrice)],
      ['', stamp(trade.exitTime)],
      ['num', price(trade.exitPrice)],
      ['num', stopCell(trade)],
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

function renderSlope() {
  // The label carries the threshold and what it would leave directional, so
  // the choice is made against a number and not against a letter.
  const button = $('slope');
  const info = state.data && state.data.slope;
  button.hidden = !info;
  if (!info) { $('slope-note').textContent = ''; return; }
  const chosen = slopeLevel();
  button.setAttribute('aria-pressed', String(!!chosen));
  button.textContent = chosen
    ? `slope p${chosen.percentile} \u00b7 ${chosen.value}`
    : 'slope: off';
  button.title = `(SMA${info.period}(t) - SMA${info.period}(t-${info.window}))`
    + ` / (${info.window} x ATR${info.atr}(t)), the move of the slow average`
    + ` per bar in ATR. The thresholds are percentiles of |slope| over the`
    + ` first ${info.trained} bars of the run and over no others.`;
}

function renderCurves() {
  // Named on the page rather than only in the strategy, because a line whose
  // period nobody can read is a line nobody can check against the rule it
  // came from.
  const box = $('curves');
  box.textContent = '';
  const list = (state.data && state.data.indicators) || [];
  box.hidden = !list.length;
  list.forEach((curve, i) => {
    const tag = document.createElement('span');
    tag.className = 'k curve';
    tag.style.color = CURVE_COLOURS[i % CURVE_COLOURS.length];
    tag.textContent = curve.label;
    box.appendChild(tag);
  });
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
    // P&L is price x units: web/service.py and the report module both say
    // so, and the label says it here as well. Sized off the account it is
    // the quote currency - still not the account's own, since nothing here
    // converts one into the other.
    stat(state.data.risk ? 'net (quote ccy)' : 'net (price x units)',
         pl(r.net), r.net > 0 ? 'good' : r.net < 0 ? 'bad' : ''),
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
    stat('open at end', String(c.stillOpen)),
    stat('candles', String(c.candles)),
    stat('took', state.data.elapsed + 's'),
  );
}

/* ------------------------------------------------------------ interaction */

function select(index) {
  const trades = state.data ? state.data.trades : [];
  if (index === null || index < 0 || index >= trades.length) return;
  state.selected = index;
  state.page = Math.floor(index / PAGE_SIZE);
  const trade = trades[index];

  const last = bars().length - 1;
  const entry = tradeBar(trade, 'entry');
  const exit_ = tradeBar(trade, 'exit');
  const from = (entry === null || entry === undefined) ? 0 : entry;
  const to = (exit_ === null || exit_ === undefined) ? last : exit_;
  state.view = {
    from: Math.max(0, from - PADDING_BARS),
    to: Math.min(last, to + PADDING_BARS),
  };

  $('reset').hidden = false;
  $('chart-trade').textContent = tradeLine(trade);
  $('chart-trade').hidden = false;

  renderTrades();
  draw();
  drawEquity();
  saveRun();
  // and the page stays where it is. It used to scroll the table's row into
  // view, which took the chart that had just zoomed onto the trade off the
  // screen - the row is marked where it is, and the figures are over the
  // chart now.
}

/*
 * The zoom: the wheel over the chart changes how many bars are on it, keeping
 * whichever bar is under the pointer where it is, and dragging sideways moves
 * the window. There is nothing native to reuse - the chart is a canvas - but
 * it is these twenty lines rather than a charting library, and "show the
 * whole range" is still the way back.
 *
 * Zooming does not change which trade is selected: the two are separate
 * questions, and losing the trade's levels because the wheel moved is the
 * opposite of what the wheel was turned for.
 */
function barUnder(clientX) {
  const view = visible();
  const rect = canvas.getBoundingClientRect();
  const step = (rect.width - AXIS.left - AXIS.right) / view.candles.length;
  return view.from + (clientX - rect.left - AXIS.left) / step;
}

function zoomTo(from, span) {
  const count = bars().length;
  span = Math.max(MIN_BARS, Math.min(Math.round(span), count));
  from = Math.max(0, Math.min(Math.round(from), count - span));
  state.view = span >= count ? null : { from, to: from + span - 1 };
  $('reset').hidden = state.view === null && !state.series;
  draw();
  // and then, once the wheel stops, the bars themselves may change: see
  // retune(). The chart is redrawn first, so the zoom never waits on a
  // request to feel like it happened
  retune();
}

function resetView() {
  state.view = null;
  state.selected = null;
  // back to the bars the strategy read. "The whole range" at five minute
  // detail is not the whole range, it is a refusal from the service
  state.series = null;
  state.detail = 'auto';
  renderDetail();
  $('reset').hidden = true;
  $('chart-zoom').textContent = '';
  $('chart-trade').hidden = true;
  renderTrades();
  draw();
  drawEquity();
  saveRun();
}

function message(text, kind) {
  const box = $('message');
  if (!text) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = text;
  box.className = kind === 'info' ? 'info' : '';
}

/*
 * While a run is running.
 *
 * A backtest over eleven years fills its orders on a million M5 bars and
 * takes minutes. A page that says "running the backtest" for all of them is
 * indistinguishable from a page that has hung, so it asks the service where
 * it is - which bar, and what the account is worth at that bar - and says so.
 * The poll is fire and forget: a missed one is a line not updated, and the
 * run is what matters.
 */
const PROGRESS_MS = 600;

/*
 * Runs worth warning about before they are started.
 *
 * Counted in bars the simulator will walk and not in candles the chart will
 * draw: a year of H4 is 1 616 candles and 75 000 M5 bars under them, and it
 * is the second number that decides whether the answer is back in ten
 * seconds or in ten minutes. Twenty-five thousand of them is a few seconds
 * today, which is about where a wait stops being free.
 */
const TICK_WARNING = 25000;

function howLong(seconds) {
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`;
  return `${(seconds / 3600).toFixed(1)} hours`;
}

function estimateQuestion(ahead) {
  const chart = ahead.limit && ahead.bars > ahead.limit
    ? `\n\nThe chart will hold ${ahead.bars.toLocaleString()} ${ahead.granularity}`
      + ` candles, over the ${ahead.limit.toLocaleString()} it is meant for:`
      + ` the page will be slow to load and to draw.`
    : '';
  return `This run walks ${ahead.ticks.toLocaleString()} bars`
    + (ahead.fine
        ? ` - ${ahead.bars.toLocaleString()} ${ahead.granularity} candles and the`
          + ` ${ahead.fine} bars its orders rest on`
        : ` of ${ahead.granularity}`)
    + `.\n\nAt the ${ahead.rate.toLocaleString()} bars a second the last run`
    + ` managed, that is about ${howLong(ahead.seconds)}.`
    + chart
    + `\n\nRun it anyway?`;
}

/*
 * A yes or no asked on the page. confirm() is the browser's own box, which
 * cannot be styled, blocks the tab, and some browsers offer to silence.
 */
function askUser(text) {
  const dialog = $('ask-dialog');
  $('ask-text').textContent = text;
  dialog.returnValue = '';
  dialog.showModal();
  $('ask-yes').focus();
  return new Promise((resolve) => dialog.addEventListener('close',
    () => resolve(dialog.returnValue === 'yes'), { once: true }));
}

function progressLine(p) {
  const share = p.total ? Math.min(100, 100 * p.bars / p.total) : null;
  if (p.loading) {
    // the reading has its own count, and it is not the run's: it is rows out
    // of a series, and the fine series is the one that takes the minute
    const read = p.toRead
      ? ` \u00b7 ${Math.floor(100 * p.read / p.toRead)}% of ${p.toRead}` : '';
    return `${p.stage || 'reading the candles'}${read}`
      + ` \u00b7 ${p.total} bars to simulate`;
  }
  return `simulating ${p.strategy} on ${p.instrument} ${p.granularity}`
    + (p.at ? ` \u00b7 ${stamp(p.at)}` : '')
    + (share === null ? '' : ` \u00b7 ${share.toFixed(0)}% of ${p.total} bars`)
    + (p.balance === null || p.balance === undefined
        ? '' : ` \u00b7 capital ${p.balance.toFixed(2)}`)
    // the trades so far, counted the way the report at the end counts them.
    // A run whose capital has not moved and whose trade count has is a run
    // doing something, which the capital alone does not say
    + (p.trades === undefined
        ? '' : ` \u00b7 ${p.trades} trades, ${p.won} won, ${p.lost} lost`);
}

function watchProgress() {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      const where = await ask('api/progress');
      if (!stopped && where.running) message(progressLine(where), 'info');
    } catch (error) { /* a poll that missed says nothing, which is right */ }
    if (!stopped) setTimeout(tick, PROGRESS_MS);
  };
  setTimeout(tick, PROGRESS_MS);
  return () => { stopped = true; };
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

function strategyDefault() {
  return state.defaults[$('strategy').value] || {};
}

// Picking a strategy selects the instrument it is written for, when there is
// a store for it, and onInstrument() then selects its timeframe.
function applyDefaults() {
  const select = $('instrument');
  const { instrument } = strategyDefault();
  if (instrument && Array.from(select.options).some((o) => o.value === instrument)) {
    select.value = instrument;
  }
  onInstrument();
}

function onStrategy() {
  // what the strategy says it does, from /api/stores. Shown on the menu
  // rather than on the run, so it can be read before deciding to run it.
  const about = $('chart-about');
  const text = state.about[$('strategy').value] || '';
  about.textContent = text;
  about.hidden = !text;

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
  const { instruments, strategies, params, equity, descriptions, defaults }
    = await ask('api/stores');
  state.instruments = instruments;
  if (equity !== undefined && equity !== null) $('balance').value = equity;
  state.about = descriptions || {};
  state.defaults = defaults || {};
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
  applyDefaults();
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
    item.textContent = `${row.granularity} (${row.bars} bars, ${day(row.from)} .. ${day(row.to)}${row.derivedFrom ? `, built from ${row.derivedFrom}` : ''})`;
    item.dataset.from = row.from;
    item.dataset.to = row.to;
    select.appendChild(item);
  }
  // The timeframe the strategy is written for when this store offers it,
  // H1 otherwise: the coarsest series that is not a daily one, whose bar
  // count fits a chart.
  const options = Array.from(select.options);
  const preferred = options.find((o) => o.value === strategyDefault().granularity)
    || options.find((o) => o.value === 'H1');
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

/* The form as the service reads it, plugin parameters included. */
function formFields() {
  return Object.fromEntries(formParams(new URLSearchParams({
    instrument: $('instrument').value,
    granularity: $('granularity').value,
    strategy: $('strategy').value,
    from: $('from').value,
    to: $('to').value,
    risk: $('risk').value,
    balance: $('balance').value,
    maxStop: $('maxStop').value,
    maxBars: $('maxBars').value,
    slScale: $('slScale').value,
    tpScale: $('tpScale').value,
    // when this account trades: the hours a signal may be taken in, whether
    // it holds overnight, and how wide a hole the calendar makes
    session: $('session').value,
    intraday: $('intraday').checked ? '1' : '',
    newsBefore: $('newsBefore').value,
    newsAfter: $('newsAfter').value,
    newsImpacts: $('newsImpacts').value,
  })));
}

async function run(event, extra) {
  if (event) event.preventDefault();
  const fields = formFields();
  // how big the job is, before anybody waits for it. A failure here is not
  // a reason not to run: the estimate is a courtesy, the run is the point.
  // Embedded, the sweep already asked and ran it, so it is not asked again
  if (EMBED) fields.confirmed = true;
  else try {
    const ahead = await ask('api/estimate?' + new URLSearchParams({
      instrument: fields.instrument, granularity: fields.granularity,
      from: fields.from, to: fields.to }).toString());
    const wide = ahead.limit && ahead.bars > ahead.limit;
    if (ahead.ticks > TICK_WARNING || wide) {
      if (!await askUser(estimateQuestion(ahead))) { message(''); return; }
      // said yes to the size, so the service is not to refuse it after all
      if (wide) fields.confirmed = true;
    }
  } catch (error) { /* no estimate, no warning, still a run */ }

  $('run').disabled = true;
  $('stop').hidden = false;
  $('stop').disabled = false;
  message('running the backtest…', 'info');
  const stop = watchProgress();
  try {
    // extra: a run of a set being made again, see start()
    show(await post('api/backtest', JSON.stringify({ ...fields, ...extra })), fields);
    message('');
  } catch (error) {
    message(String(error.message || error));
  } finally {
    stop();
    $('stop').hidden = true;
    $('run').disabled = false;
  }
}

/* A backtest's payload on the page, whether just run or redrawn. */
function show(data, fields) {
  state.data = data;
  state.fields = fields;
  state.decimals = decimalsOf(data.candles);
  state.view = null;
  state.selected = null;
  state.page = 0;
  $('reset').hidden = true;
  $('chart-zoom').textContent = '';
  $('chart-trade').hidden = true;
  // the run's id: a set's run as set/number, any other as the saved run's
  const where = new URLSearchParams(location.search);
  const id = EMBED && where.get('sweep') ? `${where.get('sweep')}/${where.get('run')}` : data.runId;
  state.runId = data.runId || null;
  state.sweepRef = EMBED && where.get('sweep')
    ? { sweep: where.get('sweep'), run: Number(where.get('run')) } : null;
  renderStar();
  $('chart-title').textContent = (id ? `[${id}] ` : '')
    + `${data.strategy} on ${data.instrument} ${data.granularity}`
    // which bars the orders rested on, when they were not these ones: a run
    // that resolved its exits a minute at a time is a different reading of
    // the same strategy, and the title is where somebody notices
    + (data.fine ? ` (filled on ${data.fine})` : '')
    + ` · ${day(data.from)} .. ${day(data.to)}`
    + ` · ${data.candles.length} candles`;
  state.series = null;
  state.detail = 'auto';
  renderCurves();
  renderSlope();
  renderDetail();
  renderReport();
  renderTrades();
  draw();
  drawEquity();
  drawLevels();
  saveRun();
}

/* ---------------------------------------------------------------- reload */

/*
 * The last run lives in this tab's sessionStorage, not in the address bar.
 * A reload refills the form with it and asks the service for the payload it
 * still has cached (cachedOnly), so a refresh redraws the run and never
 * starts one. Storage can be missing or refuse (a private window): then a
 * reload is an empty page, which is what it was before there was a run.
 */
const RUN_KEY = 'parity-deriva.run';

function saveRun() {
  if (!state.data || !state.fields) return;
  const trade = state.selected === null ? null : state.data.trades[state.selected].n;
  // embedded, this tab's saved form belongs to the page around it
  if (EMBED) return;
  try {
    sessionStorage.setItem(RUN_KEY, JSON.stringify({ fields: state.fields, trade }));
  } catch (error) { /* no storage, no reload - the run itself is unaffected */ }
}

function savedRun() {
  if (EMBED) {
    const params = new URLSearchParams(location.search);
    const { sweep, run } = Object.fromEntries(params);
    for (const key of ['embed', 'sweep', 'run']) params.delete(key);
    return { fields: Object.fromEntries(params), trade: null, sweep, run };
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(RUN_KEY) || 'null');
    if (saved && saved.fields && saved.fields.instrument) return saved;
  } catch (error) { /* nothing usable */ }
  // a link from before the page kept its state here: read once, then the
  // address bar is cleaned so the next reload does not see it again
  const params = new URLSearchParams(location.search);
  if (!params.get('instrument')) return null;
  history.replaceState(null, '', location.pathname);
  const trade = Number(params.get('trade'));
  params.delete('trade');
  return { fields: Object.fromEntries(params), trade: trade > 0 ? trade : null };
}

/* The form, refilled in its own order: the strategy's defaults first, then
   the saved values instead. */
function fillForm(fields) {
  const set = (id, value) => {
    if (!value) return;
    const field = $(id);
    if (field.tagName === 'SELECT'
        && !Array.from(field.options).some((o) => o.value === value)) return;
    field.value = value;
  };
  set('strategy', fields.strategy);
  onStrategy();
  applyDefaults();
  set('instrument', fields.instrument);
  onInstrument();
  set('granularity', fields.granularity);
  onGranularity();
  const form = currentForm();
  if (form) for (const field of form) set(field.name, fields[field.name]);
  for (const id of ['from', 'to', 'risk', 'balance', 'maxStop', 'maxBars', 'slScale', 'tpScale', 'session',
                    'newsBefore', 'newsAfter', 'newsImpacts']) {
    set(id, fields[id]);
  }
  // a checkbox is not a value: a field that is absent is off, which is what
  // a run that was never asked for it has to come back as
  $('intraday').checked = fields.intraday === '1' || fields.intraday === true;
}

/* -------------------------------------------------------------- listeners */

$('controls').addEventListener('submit', run);
$('strategy').addEventListener('change', () => { onStrategy(); applyDefaults(); });
$('reset').addEventListener('click', resetView);

// set by a drag that moved the chart, read and cleared by the click that
// follows it: a mouseup after a pan still fires a click, and without this
// every pan would also select whatever trade it finished over
let dragged = false;

$('page-prev').addEventListener('click', () => { state.page--; renderTrades(); });
$('page-next').addEventListener('click', () => { state.page++; renderTrades(); });
// from nothing selected, next starts at the first trade and prev at the last
$('trade-next').addEventListener('click', () =>
  select(state.selected === null ? 0 : state.selected + 1));
$('trade-prev').addEventListener('click', () =>
  select(state.selected === null ? state.data.trades.length - 1 : state.selected - 1));

$('trade-rows').addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-index]');
  if (row) select(Number(row.dataset.index));
});

/*
 * The chart picks trades too, and a click anywhere on it goes to the nearest
 * trade's entry.
 *
 * It used to ask for a click within twelve pixels of the arrow, which made a
 * mark four pixels wide the target: a click that missed did nothing, and
 * nothing is indistinguishable from a page that ignored you. The nearest
 * entry is what somebody clicking near a trade meant, and on a chart already
 * zoomed onto one it is that one.
 *
 * Measured in bars and not in pixels, so it is the same click at every zoom,
 * and over every trade and not only the visible ones - a click at the edge of
 * a zoomed chart then walks to the next trade along.
 */
canvas.addEventListener('click', (event) => {
  if (dragged) { dragged = false; return; }
  if (!state.data || !state.data.trades.length) return;
  const bar = barUnder(event.clientX);
  let best = null, distance = Infinity;
  state.data.trades.forEach((trade, i) => {
    const entry = tradeBar(trade, 'entry');
    if (entry === null || entry === undefined) return;
    const d = Math.abs(entry - bar);
    if (d < distance) { distance = d; best = i; }
  });
  if (best !== null) select(best);
});

/*
 * The capital curve picks trades too: a click on it goes to the trade that
 * made that step.
 *
 * Its x axis is the whole run in candles, so the click is turned into a bar
 * and the nearest close wins - the same arithmetic the price chart does with
 * entries, on the other end of the trade. This is the way round somebody
 * actually reads the curve: a drop is noticed first and the trade that caused
 * it is the question.
 */
equityCanvas.addEventListener('click', (event) => {
  if (!state.data || !state.data.trades.length) return;
  const rect = equityCanvas.getBoundingClientRect();
  const step = (rect.width - EQ_AXIS.left - EQ_AXIS.right)
    / state.data.candles.length;
  const bar = (event.clientX - rect.left - EQ_AXIS.left) / step - 0.5;
  let best = null, distance = Infinity;
  state.data.trades.forEach((trade, i) => {
    if (trade.exitIndex === null || trade.exitIndex === undefined) return;
    const d = Math.abs(trade.exitIndex - bar);
    if (d < distance) { distance = d; best = i; }
  });
  if (best !== null) select(best);
});

// A canvas measured while <details> is closed has no width, so the curve is
// drawn when it opens rather than being drawn into nothing and left blank.
$('equity-box').addEventListener('toggle', drawEquity);
$('levels-box').addEventListener('toggle', drawLevels);
$('curve-box').addEventListener('toggle', drawPanel);

$('slope').addEventListener('click', () => {
  // off -> p75 -> p90 -> p95 -> off. One button rather than four radios: the
  // point is to flip between them on the same bars and see what moves.
  const info = state.data && state.data.slope;
  const many = info ? info.thresholds.length : 0;
  state.slope = many ? (state.slope + 1) % (many + 1) : 0;
  renderSlope();
  draw();
});

$('detail').addEventListener('change', () => {
  // 'auto' is the ladder; anything else is being asked for by name, and it is
  // honoured even where the ladder would not have gone - including coarser
  // than the run, which is a legitimate way to look at where you are
  state.detail = $('detail').value;
  retune(true);
});

$('levels').addEventListener('click', () => {
  state.levels = !state.levels;
  $('levels').setAttribute('aria-pressed', String(state.levels));
  draw();
});

canvas.addEventListener('wheel', (event) => {
  if (!state.data || !state.data.candles.length) return;
  event.preventDefault();
  const view = visible();
  const span = view.to - view.from + 1;
  const pivot = barUnder(event.clientX);
  const wanted = span * (event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
  // the bar under the pointer keeps its share of the window, which is what
  // makes the wheel feel like it is zooming on something rather than on the
  // middle of the chart
  zoomTo(pivot - (pivot - view.from) / span * wanted, wanted);
}, { passive: false });

// Dragging sideways moves the window. A drag under PAN_SLOP pixels is a click
// and is left to the handler above, which is what picks a trade.
let pan = null;
canvas.addEventListener('mousedown', (event) => {
  if (!state.data || !state.data.candles.length) return;
  // otherwise the browser starts its own drag - a text selection that runs
  // out of the canvas and swallows the mousemove the pan is made of
  event.preventDefault();
  canvas.style.cursor = 'grabbing';
  const view = visible();
  pan = { x: event.clientX, from: view.from, span: view.to - view.from + 1,
          bars: (canvas.clientWidth - AXIS.left - AXIS.right)
                / view.candles.length, moved: false };
});
window.addEventListener('mouseup', () => {
  // a pan that ended may have walked out of the window that was fetched, and
  // asking for the next one is the same question the wheel asks
  if (pan && pan.moved) retune();
  pan = null;
  canvas.style.cursor = '';
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
  if (pan) {
    const moved = event.clientX - pan.x;
    if (Math.abs(moved) < PAN_SLOP && !pan.moved) return;
    pan.moved = dragged = true;
    return zoomTo(pan.from - moved / pan.bars, pan.span);
  }
  const view = visible();
  const rect = canvas.getBoundingClientRect();
  const step = (rect.width - AXIS.left - AXIS.right) / view.candles.length;
  const i = Math.floor((event.clientX - rect.left - AXIS.left) / step);
  const candle = view.candles[i];
  if (!candle) return;
  // the range as well as the four prices: how far a bar actually travelled
  // is the number a stop is judged against, and counting decimal places on
  // two prices to get it is what the reader would otherwise be doing
  const span = (candle[2] - candle[3]) / pipSize();
  $('chart-zoom').textContent =
    `${stamp(candle[0])}  O ${price(candle[1])}  H ${price(candle[2])}`
    + `  L ${price(candle[3])}  C ${price(candle[4])}`
    + `  H-L ${span.toFixed(1)} pips`;
});

canvas.addEventListener('mouseleave', () => {
  if (state.selected === null) { $('chart-zoom').textContent = ''; return; }
  const trade = state.data.trades[state.selected];
  $('chart-zoom').textContent =
    `trade ${trade.n} · ${trade.direction} · ${stamp(trade.entryTime)}`
    + ` → ${trade.exitTime === null ? openAtEnd() : stamp(trade.exitTime)}`;
});

window.addEventListener('resize', () => { draw(); drawEquity(); drawLevels(); });

/* ------------------------------------------------------------------- live */

// The form as it is now, trading on the accounts ticked: see web/livesessions.py
$('live-open').addEventListener('click', async () => {
  const fields = formFields();
  const say = (text) => { $('live-message').textContent = text; $('live-message').hidden = !text; };
  say('');
  $('live-what').textContent = `${fields.strategy} on ${fields.instrument} ${fields.granularity}`
    + (fields.risk ? `, risk ${fields.risk}% of a capital of ${fields.balance || 'the default'}, the same on every account`
                   + ' (one in USD takes it 1:1)' : '')
    + ' - one session per account, on the bars from now on.';
  const box = $('live-targets');
  box.innerHTML = '<p class="hint">reading the accounts…</p>';
  $('live-start').disabled = true;
  $('live-dialog').showModal();
  let targets;
  try { ({ targets } = await ask('api/live/targets')); }
  catch (error) { box.textContent = ''; say(String(error.message || error)); return; }
  box.textContent = '';
  for (const row of targets) {
    const group = document.createElement('fieldset');
    const legend = document.createElement('legend');
    legend.textContent = row.provider;
    group.appendChild(legend);
    const note = !row.configured ? 'no credentials in .env'
      : row.error ? row.error : row.accounts.length ? '' : 'no account';
    if (note) {
      const p = document.createElement('p');
      p.className = 'hint';
      p.textContent = note;
      group.appendChild(p);
    }
    for (const account of row.accounts) {
      const label = document.createElement('label');
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.dataset.provider = row.provider;
      tick.dataset.account = account.id;
      // a real-money account is never ticked for you
      tick.checked = !!account.demo;
      label.append(tick, ` ${account.id} ${account.name || ''} · ${account.currency || ''} `
        + `${account.balance === null ? '' : account.balance.toFixed(2)}`
        + (account.demo ? ' · demo' : ' · REAL MONEY'));
      group.appendChild(label);
    }
    box.appendChild(group);
  }
  $('live-start').disabled = false;
  $('live-start').onclick = async () => {
    const picked = [...box.querySelectorAll('input:checked')]
      .map((tick) => ({ provider: tick.dataset.provider, account: tick.dataset.account }));
    if (!picked.length) { say('tick at least one account'); return; }
    const real = [...box.querySelectorAll('input:checked')].some((tick) => tick.parentNode.textContent.includes('REAL MONEY'));
    if (real && !await askUser('One of these is a REAL MONEY account. Trade real money?')) return;
    $('live-start').disabled = true;
    say('starting…');
    try {
      await post('api/live', JSON.stringify({ fields, targets: picked }));
      location.href = 'live';
    } catch (error) {
      say(String(error.message || error));
      $('live-start').disabled = false;
    }
  };
});

/* ------------------------------------------------------------------- data */

// The two writes need this header; see do_POST in web/service.py.
async function post(url, body) {
  const response = await fetch(url, { method: 'POST', body,
                                      headers: { 'X-Parity-Deriva': '1' } });
  // a proxy in front refusing the body (413) answers in HTML, not JSON
  const payload = await response.json().catch(() => ({
    error: response.status === 413
      ? 'the proxy refused a body this large (nginx client_max_body_size)'
      : `${response.status} ${response.statusText}` }));
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

/*
 * Stopping a run. The button goes dead the moment it is pressed - there is
 * nothing to press twice - and the run's own request is what reports the
 * outcome, because it is the one that knows where it got to.
 */
$('stop').addEventListener('click', async () => {
  $('stop').disabled = true;
  try {
    const where = await post('api/backtest/stop');
    message(where.bars
      ? `stopping after ${where.bars} of ${where.total} bars…`
      : 'stopping while the candles are being read…', 'info');
  } catch (error) {
    message(String(error.message || error));
  }
});

/* --------------------------------------------------------- the calendar */

/*
 * The economic calendar is collected by the browser, not by the service: the
 * site refuses a datacentre address after a handful of requests. This page
 * cannot read forexfactory either - one site may not read another's pages,
 * which is the rule working as intended - so the collector runs on their
 * calendar page, where the weeks are same-origin, and hands back a file.
 *
 * All this end does is say what the file holds and take the one that comes
 * back. Nothing here talks to forexfactory.
 */
function calendarLine(state) {
  if (!state || !state.events) {
    return state && state.start
      ? `no calendar imported - the collector would start at ${state.start}`
      : 'no calendar imported';
  }
  const impacts = Object.keys(state.impacts || {})
    .sort((a, b) => state.impacts[b] - state.impacts[a])
    .map((name) => `${state.impacts[name]} ${name}`)
    .join(', ');
  return `${state.events.toLocaleString()} events, `
    + `${day(state.from)} .. ${day(state.to)}`
    + (impacts ? ` \u00b7 ${impacts}` : '')
    + (state.start ? ` \u00b7 collect from ${state.start}` : '');
}

async function showCalendar() {
  try {
    const state = await ask('api/calendar');
    $('calendar-state').textContent = calendarLine(state);
    // the service's suggestion, which the field is free to override: the
    // history first, and the weeks since once the history is there
    if (state.start && !$('calendar-from').value) {
      $('calendar-from').value = state.start;
    }
  } catch (error) {
    $('calendar-state').textContent = String(error.message || error);
  }
}

$('calendar-copy').addEventListener('click', async () => {
  const button = $('calendar-copy');
  try {
    // from the service and not from /static: the copy carries this service's
    // address and its token, so the weeks are pushed here as they are read
    // instead of being downloaded and imported by hand
    const source = await (await fetch(`api/collector`
      + `?origin=${encodeURIComponent(location.origin)}`
      + `&from=${encodeURIComponent($('calendar-from').value || '')}`)).text();
    await navigator.clipboard.writeText(source);
    button.textContent = 'copied - paste it in the console';
  } catch (error) {
    // the clipboard is refused on some browsers over plain http; the script
    // is still a page you can open and copy by hand
    window.open('api/collector?origin=' + encodeURIComponent(location.origin)
                + '&from=' + encodeURIComponent($('calendar-from').value || ''),
                '_blank');
    button.textContent = 'opened it - copy it by hand';
  }
  setTimeout(() => { button.textContent = 'copy the collector'; }, 6000);
});

$('calendar-import').addEventListener('click', () => dataAction(async () => {
  const file = ($('calendar-file').files || [])[0];
  if (!file) { dataLog(['choose the calendar.csv the collector downloaded']); return; }
  dataLog([`importing ${file.name} ...`]);
  const done = await post('api/calendar', await file.text());
  dataLog([`${done.read} rows read, ${done.added} new, `
           + `${done.events} in ${done.path}`]);
  showCalendar();
}));

function dataLog(lines) {
  const log = $('data-log');
  log.hidden = !lines.length;
  log.textContent = lines.join('\n');
}

function fileSize(bytes) {
  return bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB`
                          : `${Math.ceil(bytes / 1024)} KB`;
}

// the dialog's head: one line per store, the series it keeps and, dimmer,
// the ones built from them on request
function showLoaded() {
  const box = $('data-loaded');
  box.textContent = '';
  const rows = state.instruments || [];
  if (!rows.length) { box.textContent = 'nothing loaded: the stores are empty'; return; }
  for (const row of rows) {
    const line = document.createElement('div');
    const kept = row.granularities.filter((g) => !g.derivedFrom);
    const built = row.granularities.filter((g) => g.derivedFrom);
    line.textContent = `${row.instrument}  ` + kept.map((g) =>
      `${g.granularity} ${g.bars} bars ${day(g.from)} .. ${day(g.to)}`).join(' · ');
    if (built.length) {
      const dim = document.createElement('span');
      dim.className = 'dim';
      dim.textContent = `  + ${built.map((g) => g.granularity).join(' ')} built from ${built[0].derivedFrom}`;
      line.appendChild(dim);
    }
    box.appendChild(line);
  }
}

async function loadImports() {
  showLoaded();
  const { directory, sets } = await ask('api/imports');
  $('data-dir').textContent = `import folder: ${directory}`;
  const body = $('data-rows');
  body.textContent = '';
  $('data-all').checked = false;
  // one row per -ASK/-BID pair, the unit the import reads; a side that is
  // not there is named, since the import skips a set without both
  for (const s of sets) {
    const row = document.createElement('tr');
    // a set is picked here and imported by name; one missing a side cannot
    // be imported, so it has no box to tick
    const pick = document.createElement('td');
    if (s.complete) {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.className = 'data-pick';
      box.value = s.set;
      box.setAttribute('aria-label', `import ${s.set}`);
      pick.appendChild(box);
    }
    row.appendChild(pick);
    // a click anywhere on the row ticks its box, the way a list is used
    row.addEventListener('click', (event) => {
      const box = row.querySelector('input.data-pick');
      if (box && event.target !== box) box.checked = !box.checked;
    });
    for (const side of [null, 'instrument', 'granularity', 'ASK', 'BID']) {
      const cell = document.createElement('td');
      if (side === null) cell.textContent = s.set;
      else if (side === 'instrument' || side === 'granularity') cell.textContent = s[side];
      else if (s.sides[side] !== undefined) cell.textContent = fileSize(s.sides[side]);
      else { cell.textContent = 'missing'; cell.className = 'missing'; }
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  if (!sets.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.textContent = 'no import sets here yet';
    row.appendChild(cell);
    body.appendChild(row);
  }
}

// busy for the length of one action, so a second press cannot start another
async function dataAction(work) {
  const buttons = [$('data-upload'), $('data-import'), $('calendar-import')];
  for (const b of buttons) b.disabled = true;
  try {
    await work();
  } catch (error) {
    dataLog([String(error.message || error)]);
  } finally {
    for (const b of buttons) b.disabled = false;
  }
}

// Follow the background import until it ends: the bar is bytes of CSV read
// of the bytes the sets still to import hold. Sets already imported are not
// read at all, so a run of nothing new finishes at once.
async function followImport(status) {
  const box = $('data-progress-box');
  box.hidden = false;
  while (status.running) {
    const share = status.total ? status.done / status.total : 0;
    $('data-progress').value = share;
    $('data-progress-text').textContent = `${Math.round(share * 100)}%  ${status.text}`;
    await new Promise((resolve) => setTimeout(resolve, 500));
    status = await ask('api/imports/status');
  }
  box.hidden = true;
  dataLog(status.lines.concat(status.ok ? [] : ['import reported a problem, see above']));
  // the stores may hold new series or a longer range now
  await loadStores();
  showLoaded();
}

$('data-all').addEventListener('change', () => {
  for (const box of document.querySelectorAll('#data-rows input.data-pick')) {
    box.checked = $('data-all').checked;
  }
});

$('help-open').addEventListener('click', () => $('help-dialog').showModal());
$('data-open').addEventListener('click', () => {
  showCalendar();
  dataLog([]);
  $('data-dialog').showModal();
  loadImports().catch((error) => dataLog([String(error.message || error)]));
  // an import started earlier, from this page or another, is picked up
  ask('api/imports/status').then((status) => {
    if (status.running) dataAction(() => followImport(status));
  }).catch(() => {});
});

$('data-upload').addEventListener('click', () => dataAction(async () => {
  const files = Array.from($('data-file').files || []);
  if (!files.length) { dataLog(['choose a CSV on this computer to upload first']); return; }
  const lines = [];
  for (const file of files) {
    dataLog(lines.concat([`uploading ${file.name} ...`]));
    try {
      const done = await post(`api/imports/upload?name=${encodeURIComponent(file.name)}`, file);
      lines.push(`${done.name}: ${fileSize(done.bytes)} uploaded`);
    } catch (error) {
      lines.push(`${file.name}: ${error.message || error}`);
    }
  }
  $('data-file').value = '';
  dataLog(lines);
  await loadImports();
}));

$('data-import').addEventListener('click', () => dataAction(async () => {
  const sets = Array.from(document.querySelectorAll('#data-rows input.data-pick:checked'))
    .map((box) => box.value);
  if (!sets.length) { dataLog(['tick at least one import set in the table']); return; }
  dataLog([]);
  await followImport(await post('api/imports/run', JSON.stringify({ sets })));
}));

/* ---------------------------------------------------------------- runs */

async function openRuns() {
  const body = $('runs-rows');
  body.textContent = '';
  $('runs-dialog').showModal();
  const { runs } = await ask('api/runs');
  if (!runs.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 10;
    cell.textContent = 'no run saved yet';
    return;
  }
  for (const run of runs) {
    const row = body.insertRow();
    row.dataset.id = run.id;
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'fav-star';
    star.dataset.favRun = run.id;
    star.title = 'segna come preferita';
    paintStar(star, !!favouriteOf({ kind: 'run', id: run.id }));
    row.insertCell().appendChild(star);
    const cells = [run.id, stamp(run.saved), run.strategy, run.instrument,
      run.granularity + (run.fine ? ` (${run.fine})` : ''), day(run.from),
      day(run.to), String(run.trades),
      run.balance === null ? '' : run.balance.toFixed(2)];
    cells.forEach((text, i) => {
      const cell = row.insertCell();
      cell.textContent = text;
      if (i >= 7) cell.className = 'num';
    });
  }
}

async function loadRun(id) {
  const saved = await ask('api/runs/' + id);
  $('runs-dialog').close();
  fillForm(saved.fields);
  show(saved.payload, saved.fields);
  // the payload kept on disk predates runId: the row it was opened from knows
  state.runId = id;
  renderStar();
  message('');
}

$('runs-open').addEventListener('click', () =>
  openRuns().catch((error) => message(String(error.message || error))));
$('runs-rows').addEventListener('click', (event) => {
  const star = event.target.closest('.fav-star');
  if (star) {
    // the star is on the row but is not the row: no loadRun
    toggleFavourite({ kind: 'run', id: star.dataset.favRun })
      .catch((error) => message(String(error.message || error)));
    return;
  }
  const row = event.target.closest('tr[data-id]');
  if (row) loadRun(row.dataset.id).catch((error) => message(String(error.message || error)));
});

/* ----------------------------------------------------------- favourites */

/*
 * A favourite is a form the live page trades: a saved run by its id, or one
 * run of a set by set and number. The list is the service's (web/service.py
 * favourites); this page only stars and unstars, and asks for it again after
 * each, since starring twice refreshes the same entry rather than adding one.
 */
function favouriteOf(source) {
  return state.favourites.find((f) => f.source.kind === source.kind && f.source.id === source.id
    && (source.kind !== 'sweep' || f.source.n === source.n)) || null;
}

// what the run on show would be starred as, or null when it is nothing saved
function currentSource() {
  if (state.sweepRef) return { kind: 'sweep', id: state.sweepRef.sweep, n: state.sweepRef.run };
  return state.runId ? { kind: 'run', id: state.runId } : null;
}

function paintStar(button, on) {
  button.textContent = on ? '★' : '☆';
  button.setAttribute('aria-pressed', String(on));
}

function renderStar() {
  const source = currentSource();
  $('fav-star').hidden = !source;
  if (source) paintStar($('fav-star'), !!favouriteOf(source));
  for (const star of document.querySelectorAll('#runs-rows .fav-star')) {
    paintStar(star, !!favouriteOf({ kind: 'run', id: star.dataset.favRun }));
  }
}

async function loadFavourites() {
  state.favourites = (await ask('api/favourites')).favourites || [];
  renderStar();
}

async function toggleFavourite(source) {
  const have = favouriteOf(source);
  if (have) await post(`api/favourites/${have.id}/delete`, '{}');
  else await post('api/favourites', JSON.stringify({ source }));
  await loadFavourites();
}

$('fav-star').addEventListener('click', () => {
  const source = currentSource();
  if (source) toggleFavourite(source).catch((error) => message(String(error.message || error)));
});

async function start() {
  await loadStores();
  // not awaited: the stars can wait, the run cannot wait on them
  loadFavourites().catch((error) => message(String(error.message || error)));
  const saved = savedRun();
  if (!saved) return;
  fillForm(saved.fields);
  let data = await post('api/backtest',
                        JSON.stringify({ ...saved.fields, cachedOnly: true }));
  if (data.cached === false && saved.sweep) {
    // a run of a simulation set, kept on disk with it
    data = await ask(`api/sweeps/${saved.sweep}/${saved.run}`);
  }
  if (data.cached === false && (EMBED || saved.sweep)) {
    // not kept any more (a set older than its runs on disk): run it again,
    // which is what asking to look at it means; and kept with the set this time
    await run(null, saved.sweep ? { sweep: saved.sweep, sweepRun: saved.run } : {});
    return;
  }
  if (data.cached === false) {
    message('the form is the last run\'s; the service no longer holds its '
            + 'result - press run to run it again', 'info');
    return;
  }
  show(data, saved.fields);
  if (saved.trade !== null) {
    const index = data.trades.findIndex((t) => t.n === saved.trade);
    if (index >= 0) select(index);
  }
}

start().catch((error) => message(String(error.message || error)));
