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
const AXIS_DRAG = 150;     // pixels of drag on an axis that stretch it e-fold
const BOX_MIN = 8;         // pixels a zoom box needs either way to count
const NAV_EDGE = 6;        // pixels either side of the navigator's box that take its edge
const AXIS = { left: 66, right: 14, top: 12, bottom: 26 };
// the service's own ceiling on a window (web/service.py MAX_CANDLES): asking
// for more is refused, so the ladder does not ask
const MAX_FETCH = 5000;

const state = {
  data: null,        // the last backtest payload
  view: null,        // {from, to} indices into data.candles, or null for all
  scale: null,       // {high, low} once the prices are stretched by hand, or null to fit the bars
  range: null,       // the prices last drawn: where a stretch or a pan starts from
  height: 420,       // the price chart's, which the layer over it shares: the grip under it sets it
  pointer: null,     // {x, y} in the chart's own pixels while the pointer is over the plot
  events: null,      // the run's calendar events once they are in (loadEvents), [ms, currency, impact, title, actual, forecast, previous, unit]
  showEvents: false, // drawn on the chart: the button under it
  measure: null,     // {a, b}, each {ms, price}, while a shift-drag measure is on show
  want: null,        // [fromMs, toMs] for tune() to fit exactly, from showTimes()
  asked: 0,          // retune()s so far: a series that comes back after a newer one was asked is dropped
  selected: null,    // index into data.trades
  hover: null,       // index into data.candles
  decimals: 5,
  quoted: 5,         // the decimals the broker quotes, which a pip is ten ticks of: see pipSize()
  about: {},         // strategy -> what it says it does, from /api/stores
  levels: true,      // draw the swing levels, toggled by the button
  slope: 0,          // 0 = no shading, else the threshold's place in the list
  detail: 'auto',    // the granularity the chart draws at, or 'auto'
  series: null,      // {granularity, candles, from, to} when it is not the run's own
  fetching: false,   // one request at a time: the wheel would queue a dozen
  tuning: null,      // the timer that lets the wheel settle before fetching
  swings: [],        // the levels of the current payload, see levelsOf()
  swingsFor: null,   // the payload they were computed from
  runId: null,       // the saved run on show, if it is one
  sweepRef: null,    // {sweep, run} when it is a run of a simulation set
  favourites: [],    // the forms starred to trade live, from /api/favourites
};

const $ = (id) => document.getElementById(id);

const canvas = $('chart');
const ctx = canvas.getContext('2d');

// The crosshair, a measure and a zoom box are drawn on a canvas of their own
// over the chart, so following the pointer never redraws seventeen thousand
// candles: see drawOver().
const over = $('chart-over');
const octx = over.getContext('2d');

// The whole run under the chart, with the stretch on show boxed: see drawNav().
const NAV_H = 44;
const NAV_AXIS = { left: AXIS.left, right: AXIS.right, top: 4, bottom: 4 };
const navCanvas = $('chart-nav');
const nctx = navCanvas.getContext('2d');

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
 * read off the prices the broker quoted, so the page needs no table of its own
 * to fall out of step with. Off the ask and the bid, not off the candles: a
 * candle is their middle, and the middle of 1.17136 and 1.17126 has a sixth
 * decimal the instrument does not, which made every pip here ten times too
 * many.
 */
function pipSize() {
  // a division rather than a negative power: Math.pow(10, -4) is
  // 0.00009999999999999999, and a pip that is not the number it is named
  // after turns every count into a rounding story
  return 1 / Math.pow(10, state.quoted - 1);
}

function decimalsOf(candles, column = 4) {
  // Read the precision off the data rather than off a table: the store holds
  // what the broker served, and a chart that rounds harder than the data is a
  // chart that hides the tick a level was reached by.
  let d = 0;
  for (let i = 0; i < candles.length && d < 6; i++) {
    const text = String(candles[i][column]);
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
  showReset();
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
  state.asked++;
  clearTimeout(state.tuning);
  state.tuning = setTimeout(tune, now ? 0 : 140);
}

async function tune() {
  const win = state.want || viewTimes();
  if (!win || state.fetching) return;
  state.want = null;
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
  const asked = state.asked;
  let stale = false;
  try {
    const query = new URLSearchParams({
      instrument: state.data.instrument, granularity: wanted,
      from: iso(win[0] - pad), to: iso(win[1] + pad) });
    const answer = await ask('api/candles?' + query.toString());
    if (!answer.candles.length) return;
    // the chart was moved while these were on their way: fitting them to the
    // window they were asked for would snap it back, so the newer one is
    // asked instead
    if (asked !== state.asked) { stale = true; return; }
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
    if (stale) retune(true);
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

/*
 * RG2 - the slope shading.
 *
 * The measure and the percentiles come from the service (web/service.py), so
 * this file decides nothing about them: it picks one of the thresholds the
 * payload offers and colours the bars each side of it. `state.slope` is a
 * place in that list and not a number, because a number typed here would be
 * a threshold chosen by the viewer.
 *
 * A bar moving up is tinted in the theme's up colour, one moving down in its
 * down colour, a flat one in grey - faintly, because the candles go on top
 * and are what is being read.
 */
const SLOPE_ALPHA = 0.16;
const SLOPE_FLAT_ALPHA = 0.10;

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
function shade(view, plotH, step, p) {
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
    ctx.fillStyle = side > 0 ? p.up : side < 0 ? p.down : p.text3;
    ctx.globalAlpha = side ? SLOPE_ALPHA : SLOPE_FLAT_ALPHA;
    ctx.fillRect(AXIS.left + i * step, AXIS.top, Math.max(1, step), plotH);
  }
  ctx.globalAlpha = 1;
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
 *
 * All of them grey, each told from the others by its dash: the chart's
 * colours already mean up, down and entry, and a curve is none of those.
 */
function flatten(wanted) {
  // every line of one axis, flattened: a Bollinger band is three of them, all
  // in the band's own dash - above, middle and below is what tells them
  // apart. The dash is taken from the curve's place in the declared list and
  // not from its place here, so it matches the sample the legend prints.
  const out = [];
  const list = (state.data && state.data.indicators) || [];
  list.forEach((curve, i) => {
    if (!!curve.panel !== wanted) return;
    const dash = DASHES[i % DASHES.length];
    if (curve.kind === 'bollinger') {
      out.push({ values: curve.upper, dash },
               { values: curve.middle, dash },
               { values: curve.lower, dash });
    } else {
      out.push({ values: curve.values, dash, label: curve.label });
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
  return fitCanvas(canvas, ctx, state.height);
}

function draw() {
  const p = palette();
  const { width, height } = resize();
  fitCanvas(over, octx, height);
  ctx.clearRect(0, 0, width, height);
  $('ranges').hidden = !(state.data && state.data.candles.length);
  drawNav();
  if (!state.data || !state.data.candles.length) { renderTrade(); return; }

  const view = visible();
  const trade = state.selected === null ? null : state.data.trades[state.selected];
  const zoomed = state.view !== null;
  // The candles first: a selected trade's stop and target used to join the
  // range, and a stop far from its entry flattened the bars it was about into
  // a line. They are read in the card under the chart now, which says when one
  // is off the chart (tradeCard).
  const range = state.scale || scales(view, []);
  state.range = range;

  const plotW = width - AXIS.left - AXIS.right;
  const plotH = height - AXIS.top - AXIS.bottom;
  const n = view.candles.length;
  const step = plotW / n;
  const bodyW = Math.max(1, Math.min(14, step * 0.7));

  const y = (p) => AXIS.top + (range.high - p) / (range.high - range.low) * plotH;
  const x = (i) => AXIS.left + (i + 0.5) * step;

  drawPanel();

  grid(width, height, range, y, p);
  // prices stretched by hand put bars above and below the plot: they stop at
  // its edge instead of running over the axes
  ctx.save();
  ctx.beginPath();
  ctx.rect(AXIS.left, AXIS.top, plotW, plotH);
  ctx.clip();
  shade(view, plotH, step, p);
  if (state.showEvents) drawEvents(view, x, plotH, p);
  curves(view, x, y, p);
  if (state.levels) supports(view, x, y, plotW, n, range, p);

  // the holding period, behind everything: the bars the trade was open for
  const held = trade ? tradeBar(trade, 'entry') : null;
  if (trade && held !== null && held !== undefined) {
    const out = tradeBar(trade, 'exit');
    const a = Math.max(0, held - view.from);
    const b = (out === null || out === undefined ? n - 1 : out - view.from);
    if (b >= 0 && a <= n) {
      ctx.fillStyle = p.span;
      const left = AXIS.left + Math.max(0, a) * step;
      const right = AXIS.left + Math.min(n, b + 1) * step;
      ctx.fillRect(left, AXIS.top, Math.max(1, right - left), plotH);
    }
  }

  candles(view, x, y, bodyW, zoomed, p);
  if (trade) setupBox(trade, view, x, y, n, step, p);
  arrows(view, x, y, step, p);
  if (trade) overlay(trade, view, x, y, plotW, n, step, p);
  ctx.restore();
  times(view, x, height, n, step, p);
  drawOver();
  renderEvents();
  // after the range: the card says which levels it leaves out
  renderTrade();
  // the capital below boxes the same stretch
  drawEquity();
}

/* ------------------------------------------------- the calendar's events */

/*
 * The calendar's high and medium events for the run's currencies, asked once
 * a run is on show (web/service.py calendarEvents). Counted under the chart
 * for the stretch on show; drawn on it, as uprights behind the candles, when
 * the button there is pressed; read out with a bar's prices on hover.
 */
async function loadEvents() {
  const data = state.data;
  state.events = null;
  renderEvents();
  if (!data || !data.candles.length) return;
  const run = data.candles;
  const query = new URLSearchParams({ instrument: data.instrument,
                                      from: iso(run[0][0]), to: iso(run[run.length - 1][0]) });
  let events = [];
  try {
    events = (await ask('api/calendar/events?' + query.toString())).events;
  } catch (error) {
    message(`calendar: ${error.message}`, 'info');
  }
  if (state.data !== data) return;   // another run came on meanwhile
  state.events = events;
  draw();
  drawHeat();
}

// the events from one instant to another, oldest first, of a list oldest first
function eventsBetween(fromMs, toMs, events = state.events || []) {
  const first = (ms) => {
    let low = 0, high = events.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (events[mid][0] < ms) low = mid + 1; else high = mid;
    }
    return low;
  };
  return events.slice(first(fromMs), first(toMs));
}

// the events inside a drawn bar: from its open to the next bar's
function eventsOfBar(i) {
  const list = bars();
  if (!list[i]) return [];
  const next = list[i + 1] ? list[i + 1][0] : list[i][0] + 60000 * (MINUTES[drawnAt()] || 1);
  return eventsBetween(list[i][0], next);
}

// one event as the readouts write it: its time, whose it is, and its number against the forecast
function eventText(e, withDate) {
  const [ms, currency, impact, title, actual, forecast, , unit] = e;
  const number = (v) => (v === null || v === undefined) ? '' : v + (unit || '');
  return `${stamp(ms, withDate)} ${currency} ${impact} ${title}`
    + (actual !== null && actual !== undefined ? ` ${number(actual)}` : '')
    + (forecast !== null && forecast !== undefined ? ` (forecast ${number(forecast)})` : '');
}

// the line under the chart: how many there are, on show and in the whole run
function renderEvents() {
  const note = $('events-note'), button = $('events');
  if (!state.data || !state.data.candles.length) { note.textContent = ''; return; }
  const events = state.events;
  button.disabled = !events || !events.length;
  if (events === null) { note.textContent = 'asking the calendar\u2026'; return; }
  if (!events.length) {
    note.textContent = `no high or medium calendar events for ${state.data.instrument} in this run`
      + ' - the calendar is imported on the settings page';
    return;
  }
  const win = viewTimes();
  const shown = win ? eventsBetween(win[0], win[1] + 1).length : 0;
  const high = events.filter((e) => e[2] === 'high').length;
  note.textContent = `${shown} on show \u00b7 ${events.length} in the run`
    + ` (${high} high, ${events.length - high} medium)`;
}

// Uprights at the bars the events fell in: a high one in the loss colour and
// solid, a medium one faint and dashed, and their currency on top when there
// are few enough on show to read them.
function drawEvents(view, x, plotH, p) {
  const shown = [];
  for (let i = view.from; i <= view.to; i++) {
    for (const e of eventsOfBar(i)) shown.push([i, e]);
  }
  ctx.save();
  ctx.lineWidth = 1;
  ctx.font = '10px ' + p.mono;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const [i, e] of shown) {
    const high = e[2] === 'high', px = Math.round(x(i - view.from)) + 0.5;
    ctx.strokeStyle = ctx.fillStyle = high ? p.down : p.text3;
    ctx.globalAlpha = high ? 0.55 : 0.35;
    ctx.setLineDash(high ? [] : [3, 3]);
    ctx.beginPath();
    ctx.moveTo(px, AXIS.top);
    ctx.lineTo(px, AXIS.top + plotH);
    ctx.stroke();
    if (shown.length <= 60) {
      ctx.globalAlpha = 0.9;
      ctx.fillText(e[1], px, AXIS.top + 2);
    }
  }
  ctx.restore();
}

/* ------------------------------------------------- the selected trade */

/*
 * The selected trade, as the card under the chart shows it: a head - its
 * number and id, side, outcome, P&L, pips and how long it was held - then
 * its figures under their names, each in the colour its mark has on the
 * chart, then the calendar's events while it was open. The stop and the
 * target are read here rather than off the chart, which fits the candles:
 * one outside them is said to be.
 */
function tradeCard(trade) {
  const [kind, outcome] = outcomeCell(trade);
  const open = trade.exitTime === null || trade.exitTime === undefined;
  const dir = trade.direction === 'short' ? -1 : 1, held = barsHeld(trade);
  const moved = open ? null : (trade.exitPrice - trade.entryPrice) * dir / pipSize();
  const given = (v) => v !== null && v !== undefined;
  const signed = (v, digits) => (v >= 0 ? '+' : '') + v.toFixed(digits);
  const tone = (v) => 'pl ' + (v >= 0 ? 'good' : 'bad');
  const r = state.range;
  const off = (v) => r && given(v) && (v > r.high || v < r.low) ? ' · off the chart' : '';
  const run = state.data.candles;
  const during = state.events === null ? null : eventsBetween(
    trade.signalTime ?? trade.entryTime, (open ? run[run.length - 1][0] : trade.exitTime) + 1);
  return {
    head: [
      [`#${trade.n}`, 'trade-n'],
      [trade.direction, 'side ' + trade.direction],
      [outcome, 'outcome ' + kind],
      given(trade.pl) ? [`P&L ${signed(trade.pl, Math.min(state.decimals + 1, 8))}`, tone(trade.pl)] : null,
      moved === null ? null : [`${signed(moved, 1)} pips`, tone(moved)],
      held === null ? null : [`${held} bars · ${lasted(trade.exitTime - trade.entryTime)}`, 'trade-held'],
      trade.key ? [trade.key, 'trade-id'] : null,
    ].filter(Boolean),
    facts: [
      ['signal', stamp(trade.signalTime), ''],
      ['entry', `${stamp(trade.entryTime)} @ ${price(trade.entryPrice)}`, 'entry'],
      ['exit', open ? openAtEnd() : `${stamp(trade.exitTime)} @ ${price(trade.exitPrice)}`, 'exit'],
      ['stop', stopCell(trade) + off(given(trade.stopFinal) ? trade.stopFinal : trade.stopLoss), 'sl'],
      ['target', given(trade.takeProfit) ? price(trade.takeProfit) + off(trade.takeProfit) : 'none', 'tp'],
      given(trade.orderPrice) ? ['ordered at', price(trade.orderPrice), ''] : null,
      ['size', `${size(trade.units)} units`, ''],
      given(trade.balance) ? ['balance after', amount(trade.balance, 2), ''] : null,
    ].filter(Boolean),
    // [impact, what] a row, or null while the calendar has not answered
    events: during && during.map((e) => [e[2], eventText(e, true)]),
  };
}

// The card, drawn again only when what it says changed: draw() asks on every
// frame of a drag, and the stop's "off the chart" is all that moves then.
function renderTrade() {
  const box = $('chart-trade');
  const trade = state.data && state.selected !== null ? state.data.trades[state.selected] : null;
  box.hidden = !trade;
  if (!trade) { box.dataset.card = ''; return; }
  const card = tradeCard(trade), said = JSON.stringify(card);
  if (box.dataset.card === said) return;
  box.dataset.card = said;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const head = el('div', 'trade-head');
  head.append(...card.head.map(([text, cls]) => el('span', cls, text)));
  const facts = el('dl', 'trade-facts');
  for (const [name, value, cls] of card.facts) {
    const fact = el('div', 'fact ' + cls);
    fact.append(el('dt', '', name), el('dd', '', value));
    facts.append(fact);
  }
  box.textContent = '';
  box.append(head, facts);
  if (card.events) {
    const events = el('div', 'trade-events');
    events.append(el('span', 'trade-label', `calendar while open${card.events.length ? ` (${card.events.length})` : ''}`),
      ...(card.events.length ? card.events.map(([impact, text]) => el('span', 'event ' + impact, text))
        : [el('span', 'trade-quiet', 'no high or medium events')]));
    box.append(events);
  }
}
/* ------------------------------------------------------ over the chart */

// the plot as draw() last laid it out, for the pointer's side of things
function plot() {
  const view = visible(), r = state.range;
  const plotW = canvas.clientWidth - AXIS.left - AXIS.right, plotH = state.height - AXIS.top - AXIS.bottom;
  const step = plotW / view.candles.length;
  return { view, r, plotW, plotH, step,
           y: (v) => AXIS.top + (r.high - v) / (r.high - r.low) * plotH,
           priceAt: (py) => r.high - (py - AXIS.top) / plotH * (r.high - r.low),
           indexAt: (px) => view.from + (px - AXIS.left) / step };
}

// The pointer in the chart's own pixels, the ones draw() works in. The height
// is scaled: the canvas is drawn state.height high and shown inside its border.
function local(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left - canvas.clientLeft,
           y: (event.clientY - rect.top - canvas.clientTop) * state.height / canvas.clientHeight };
}

// the bar and the price under the pointer, as a measure keeps them: by time
// and by price, so it stays on its bars through a zoom or a pan
function dataAt(event) {
  const g = plot(), at = local(event);
  const i = Math.max(0, Math.min(g.view.candles.length - 1, Math.floor(g.indexAt(at.x) - g.view.from)));
  return { ms: g.view.candles[i][0], price: g.priceAt(at.y) };
}

function measureOf(m) {
  return measured(m.a, m.b, pipSize(), Math.abs((barAt(m.b.ms) ?? 0) - (barAt(m.a.ms) ?? 0)), drawnAt());
}

/*
 * The layer over the chart: the crosshair with its price and its time tagged
 * on the axes, a measure, a zoom box. Redrawn on every move of the pointer,
 * which the chart under it is not.
 */
function drawOver() {
  octx.clearRect(0, 0, canvas.clientWidth, state.height);
  if (!state.data || !state.data.candles.length || !state.range) return;
  const p = palette(), g = plot();
  const xAt = (ms) => AXIS.left + ((barAt(ms) ?? -1) - g.view.from + 0.5) * g.step;
  const m = state.measure, box = pan && pan.box,
    // a pointer that is not a number (no layout yet) draws no crosshair
    at = state.pointer && isFinite(state.pointer.x) && isFinite(state.pointer.y) ? state.pointer : null;
  const colour = m && m.b.price < m.a.price ? p.down : p.up;
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
    octx.strokeStyle = octx.fillStyle = p.text;
    octx.globalAlpha = 0.06;
    octx.fillRect(Math.min(box.x0, box.x1), Math.min(box.y0, box.y1), Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
    octx.globalAlpha = 0.7;
    octx.setLineDash([4, 3]);
    octx.strokeRect(Math.min(box.x0, box.x1) + 0.5, Math.min(box.y0, box.y1) + 0.5,
                    Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
  }
  let i = null;
  if (at) {
    // the upright on the middle of the bar under the pointer, the level at the pointer
    i = Math.max(0, Math.min(g.view.candles.length - 1, Math.floor((at.x - AXIS.left) / g.step)));
    const cx = Math.round(AXIS.left + (i + 0.5) * g.step) + 0.5, cy = Math.round(at.y) + 0.5;
    octx.strokeStyle = p.text;
    octx.globalAlpha = 0.45;
    octx.lineWidth = 1;
    octx.setLineDash([3, 3]);
    octx.beginPath();
    octx.moveTo(cx, AXIS.top);
    octx.lineTo(cx, AXIS.top + g.plotH);
    octx.moveTo(AXIS.left, cy);
    octx.lineTo(AXIS.left + g.plotW, cy);
    octx.stroke();
  }
  octx.restore();
  if (at) {
    axisTag(octx, p, price(g.priceAt(at.y)), AXIS.left - 2, at.y, 'left');
    axisTag(octx, p, stamp(g.view.candles[i][0]), AXIS.left + (i + 0.5) * g.step, state.height - AXIS.bottom + 3, 'bottom');
  }
  if (m) axisTag(octx, p, measureOf(m), xAt(m.b.ms) + 10, g.y(m.b.price) + 8, 'at', colour);
}

/*
 * A zoom box let go of: the bars across it and the prices up it, set by hand
 * the way the axes set them. One too small to have been meant is dropped.
 */
function boxZoom(box) {
  pan.box = null;
  if (Math.abs(box.x1 - box.x0) < BOX_MIN || Math.abs(box.y1 - box.y0) < BOX_MIN) return;
  const g = plot();
  const a = g.indexAt(Math.min(box.x0, box.x1)), b = g.indexAt(Math.max(box.x0, box.x1));
  state.scale = { high: g.priceAt(Math.min(box.y0, box.y1)), low: g.priceAt(Math.max(box.y0, box.y1)) };
  zoomTo(a, b - a);
}

/*
 * A stretch of time by its two ends, on whichever bars can hold it: the finer
 * series on show when it is inside it, the run's own otherwise. The bars that
 * suit it are then asked for with the stretch itself, and not with the one
 * MIN_BARS of the coarser bars may have widened it to - a day on a four hour
 * run is a day, on finer bars. Not while a drag is running: its mouseup asks.
 */
function showTimes(fromMs, toMs, dragging) {
  const s = state.series;
  if (s && (fromMs < s.from || toMs > s.to)) state.series = null;
  setViewByTime(fromMs, toMs);
  draw();
  if (dragging) return;
  state.want = [fromMs, toMs];
  retune(true);
}

// what the chart keys do (chartKey() in menu.js)
function keyed(key) {
  const view = visible(), span = view.to - view.from + 1;
  if (key === 'left' || key === 'right') return zoomTo(view.from + (key === 'left' ? -span : span) / 4, span);
  if (key === 'in' || key === 'out') {
    const wanted = span * (key === 'out' ? ZOOM_STEP : 1 / ZOOM_STEP);
    return zoomTo(view.from + (span - wanted) / 2, wanted);
  }
  if (key === 'fit') { state.scale = null; showReset(); return draw(); }
  // either end of the run, and not of the finer bars fetched around the view
  const run = state.data.candles, [a, b] = viewTimes();
  if (key === 'home') showTimes(run[0][0], run[0][0] + (b - a));
  if (key === 'end') showTimes(run[run.length - 1][0] - (b - a), run[run.length - 1][0]);
}

/* ------------------------------------------------------- the navigator */

/*
 * The whole run under the chart, as its capital - or its closes, for a run
 * with too few trades to draw one - with the stretch on show boxed. Dragging
 * the box moves the chart, its edges widen or narrow it, a press elsewhere
 * takes the chart there: across a run of ten years in one move.
 */
function navWindow() {
  const last = state.data.candles.length - 1;
  const win = (state.view || state.series) && viewTimes();
  if (!win) return [0, last];
  return [runBarAt(win[0]) ?? 0, runBarAt(win[1]) ?? last];
}

function drawNav() {
  const has = !!(state.data && state.data.candles.length);
  navCanvas.hidden = !has;
  if (!has) return;
  const p = palette();
  const { width, height } = fitCanvas(navCanvas, nctx, NAV_H);
  nctx.clearRect(0, 0, width, height);
  const run = state.data.candles, n = run.length;
  const plotW = width - NAV_AXIS.left - NAV_AXIS.right, plotH = height - NAV_AXIS.top - NAV_AXIS.bottom;
  const x = (i) => NAV_AXIS.left + (i + 0.5) * plotW / n;
  const points = equityPoints(), capital = points.length >= 2;
  // a loop and not Math.max(...): ten years of five minute closes are more
  // arguments than a call takes
  let high = -Infinity, low = Infinity;
  for (const v of capital ? points.map((q) => q[1]) : run.map((c) => c[4])) {
    if (v > high) high = v;
    if (v < low) low = v;
  }
  if (high === low) { high += 0.5; low -= 0.5; }
  const y = (v) => NAV_AXIS.top + (high - v) / (high - low) * plotH;
  nctx.lineWidth = 1;
  nctx.strokeStyle = !capital ? p.text3 : points[points.length - 1][1] >= points[0][1] ? p.up : p.down;
  nctx.beginPath();
  if (capital) {
    // a step, as drawEquity() draws it
    nctx.moveTo(x(points[0][0]), y(points[0][1]));
    for (let i = 1; i < points.length; i++) {
      nctx.lineTo(x(points[i][0]), y(points[i - 1][1]));
      nctx.lineTo(x(points[i][0]), y(points[i][1]));
    }
    nctx.lineTo(width - NAV_AXIS.right, y(points[points.length - 1][1]));
  } else {
    // a point a pixel is all a line this narrow can show
    const every = Math.max(1, Math.floor(n / plotW));
    nctx.moveTo(x(0), y(run[0][4]));
    for (let i = every; i < n; i += every) nctx.lineTo(x(i), y(run[i][4]));
  }
  nctx.stroke();
  // what is not on show, dimmed; what is, boxed
  const [a, b] = navWindow();
  const left = NAV_AXIS.left + a * plotW / n, right = NAV_AXIS.left + (b + 1) * plotW / n;
  nctx.fillStyle = p.panel;
  nctx.globalAlpha = 0.65;
  nctx.fillRect(NAV_AXIS.left, 0, left - NAV_AXIS.left, height);
  nctx.fillRect(right, 0, width - NAV_AXIS.right - right, height);
  nctx.globalAlpha = 1;
  nctx.strokeStyle = p.text2;
  nctx.lineWidth = 1.5;
  nctx.strokeRect(left, 1.5, Math.max(2, right - left), height - 3);
}

// the navigator's pointer as a bar of the run, and which part of the box it is on
function navBar(event) {
  const rect = navCanvas.getBoundingClientRect();
  return (event.clientX - rect.left - navCanvas.clientLeft - NAV_AXIS.left)
    / (navCanvas.clientWidth - NAV_AXIS.left - NAV_AXIS.right) * state.data.candles.length;
}

function navPart(event) {
  const [a, b] = navWindow();
  const k = (navCanvas.clientWidth - NAV_AXIS.left - NAV_AXIS.right) / state.data.candles.length;
  const px = navBar(event) * k;
  if (Math.abs(px - a * k) <= NAV_EDGE) return 'from';
  if (Math.abs(px - (b + 1) * k) <= NAV_EDGE) return 'to';
  return px > a * k && px < (b + 1) * k ? 'move' : 'out';
}

// bars a to b of the run on the chart, the box kept inside the run
function navShow(a, b, dragging) {
  const run = state.data.candles, last = run.length - 1;
  if (a < 0) { b -= a; a = 0; }
  if (b > last) { a = Math.max(0, a - (b - last)); b = last; }
  showTimes(run[Math.round(a)][0], run[Math.round(b)][0], dragging);
}

function grid(width, height, range, y, p) {
  const lines = 6;
  ctx.lineWidth = 1;
  ctx.font = '11px ' + p.mono;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= lines; i++) {
    const v = range.low + (range.high - range.low) * (i / lines);
    const py = Math.round(y(v)) + 0.5;
    ctx.strokeStyle = p.grid;
    ctx.beginPath();
    ctx.moveTo(AXIS.left, py);
    ctx.lineTo(width - AXIS.right, py);
    ctx.stroke();
    ctx.fillStyle = p.text3;
    ctx.fillText(price(v), AXIS.left - 8, py);
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
function curves(view, x, y, p) {
  for (const line of indicatorSeries()) {
    if (!line.values) continue;
    ctx.save();
    ctx.strokeStyle = p.text3;
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

function supports(view, x, y, plotW, n, range, p) {
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
    band(ctx, l, left, width, y, p);
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
 *
 * Grey, both kinds: red and green on this chart are target and stop, down
 * and up, and a level is neither. A resistance is a dashed line through its
 * band, a support a dotted one.
 */
function band(context, l, left, width, y, p) {
  const top = y(l.price + l.near), bottom = y(l.price - l.near);
  context.fillStyle = context.strokeStyle = p.text3;
  context.globalAlpha = 0.13;
  context.fillRect(left, top, width, Math.max(1, bottom - top));
  context.globalAlpha = 0.75;
  context.setLineDash(l.up ? [6, 4] : [2, 3]);
  const py = Math.round(y(l.price)) + 0.5;
  context.beginPath();
  context.moveTo(left, py);
  context.lineTo(left + width, py);
  context.stroke();
  context.setLineDash([]);
  context.globalAlpha = 1;
}

function candles(view, x, y, bodyW, zoomed, p) {
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
      ctx.strokeStyle = p.text3;
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, y(askH));
      ctx.lineTo(cx, y(bidL));
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    ctx.strokeStyle = ctx.fillStyle = up ? p.up : p.down;
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
function setupBox(trade, view, x, y, n, step, p) {
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
  ctx.strokeStyle = p.text;
  ctx.globalAlpha = 0.6;
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.strokeRect(Math.round(left) + 0.5, Math.round(top - pad) + 0.5,
                 Math.round(right - left), Math.round(bottom - top + pad * 2));
  ctx.restore();
}

function overlay(trade, view, x, y, plotW, n, step, p) {
  // The levels asked for are labelled on the right, the prices actually got
  // on the left. A trade that closed at its target has an exit and a target
  // at the same price, and two labels on the same side would sit on top of
  // each other - which is exactly the case worth being able to read.
  const line = (value, colour, dash, label, side, width = 1.5, alpha = 1) => {
    if (value === null || value === undefined) return;
    const py = Math.round(y(value)) + 0.5;
    ctx.save();
    ctx.setLineDash(dash);
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.moveTo(AXIS.left, py);
    ctx.lineTo(AXIS.left + plotW, py);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = colour;
    ctx.font = '11px ' + p.mono;
    ctx.textBaseline = 'bottom';
    if (side === 'right') {
      ctx.textAlign = 'right';
      ctx.fillText(`${t(label)} ${price(value)}`, AXIS.left + plotW - 4, py - 3);
    } else {
      ctx.textAlign = 'left';
      ctx.fillText(`${t(label)} ${price(value)}`, AXIS.left + 4, py - 3);
    }
  };

  line(trade.takeProfit, p.up, [5, 4], 'target', 'right');
  line(trade.stopLoss, p.down, [5, 4], 'stop', 'right');
  // Where a walking stop ended up, drawn only when it is not where it was
  // ordered. Usually it is also the exit - but not when the bar gapped
  // through it, and that is the case worth being able to see: the fill is
  // past the level, never short of it.
  if (trade.stopFinal !== null && trade.stopFinal !== undefined
      && trade.stopFinal !== trade.stopLoss) {
    line(trade.stopFinal, p.trail, [2, 3], 'stop moved to', 'right');
  }
  line(trade.entryPrice, p.entry, [], 'entry', 'left');
  // thinner and faded: the exit is the text colour, and at full strength it
  // would be the loudest line on the chart
  line(trade.exitPrice, p.exit, [], 'exit', 'left', 1, 0.55);

  // The entry a triangle pointing the way the trade was taken, the exit a
  // square: told apart by shape, since the exit has no colour of its own.
  // Each is ringed in the panel's colour so it stands off the candle under it,
  // and drawn over its guide line rather than crossed by it.
  const marker = (index, value, colour, shape) => {
    if (index === null || index === undefined || value === null) return;
    const i = index - view.from;
    if (i < 0 || i >= n) return;
    const cx = x(i);
    const cy = y(value);
    ctx.strokeStyle = colour;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, AXIS.top);
    ctx.lineTo(Math.round(cx) + 0.5, AXIS.top + (canvas.clientHeight - AXIS.top - AXIS.bottom));
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.save();
    ctx.beginPath();
    if (shape === 'square') ctx.rect(cx - 4, cy - 4, 8, 8);
    else {
      const tip = shape === 'up' ? -5 : 5;
      ctx.moveTo(cx, cy + tip);
      ctx.lineTo(cx - 5, cy - tip);
      ctx.lineTo(cx + 5, cy - tip);
      ctx.closePath();
    }
    // twice the ring's width, half of it then covered by the fill
    ctx.strokeStyle = p.panel;
    ctx.lineWidth = 4;
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.fillStyle = colour;
    ctx.fill();
    ctx.restore();
  };
  marker(tradeBar(trade, 'entry'), trade.entryPrice, p.entry,
         trade.direction === 'long' ? 'up' : 'down');
  marker(tradeBar(trade, 'exit'), trade.exitPrice, p.exit, 'square');

  // Entry to exit, so the trade is one movement across the chart rather than
  // two marks the eye has to join. Green or red by what it made, because the
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
    trade.pl > 0 ? p.up : trade.pl < 0 ? p.down : p.text3;
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
function arrows(view, x, y, step, p) {
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
    ctx.fillStyle = long ? p.up : p.down;
    ctx.fill();
    if (i === state.selected) {
      ctx.strokeStyle = p.text;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }
}

function times(view, x, height, n, step, p) {
  ctx.fillStyle = p.text3;
  ctx.font = '11px ' + p.mono;
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
  const p = palette();
  const panel = $('equity-panel');
  const points = equityPoints();
  panel.hidden = points.length < 2;
  if (panel.hidden) { $('equity-note').textContent = ''; return; }

  const { width, height } = fitCanvas(equityCanvas, ectx, 200);
  ectx.clearRect(0, 0, width, height);

  const start = points[0][1];
  const last = points[points.length - 1][1];
  const values = points.map((p) => p[1]);
  // the band a point a close, as far as the run's closes go
  const band = state.band && state.band.p5.length ? points.slice(1).map((pt, k) => (
    k < state.band.p5.length ? [pt[0], start * (1 + state.band.p5[k]), start * (1 + state.band.p95[k])] : null))
    .filter(Boolean) : [];
  for (const [, lo, hi] of band) values.push(lo, hi);
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

  ectx.font = '11px ' + p.mono;
  ectx.textAlign = 'right';
  ectx.textBaseline = 'middle';
  ectx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    ectx.strokeStyle = p.grid;
    ectx.beginPath();
    ectx.moveTo(EQ_AXIS.left, py);
    ectx.lineTo(width - EQ_AXIS.right, py);
    ectx.stroke();
    ectx.fillStyle = p.text3;
    ectx.fillText(amount(v, decimals), EQ_AXIS.left - 8, py);
  }

  // the stretch the price chart has on show, when it is not the whole run
  if (state.view || state.series) {
    const [a, b] = navWindow();
    ectx.fillStyle = p.span;
    ectx.fillRect(EQ_AXIS.left + a * step, EQ_AXIS.top, Math.max(1, (b - a + 1) * step), plotH);
  }

  // where it started, so profit and loss are read against a line rather than
  // against the axis labels
  ectx.strokeStyle = p.text3;
  ectx.setLineDash([4, 4]);
  ectx.beginPath();
  ectx.moveTo(EQ_AXIS.left, Math.round(y(start)) + 0.5);
  ectx.lineTo(width - EQ_AXIS.right, Math.round(y(start)) + 0.5);
  ectx.stroke();
  ectx.setLineDash([]);

  // the band, under the curve: where the capital could have been after as
  // many closes (5th to 95th percentile)
  if (band.length > 1) {
    ectx.fillStyle = p.grid;
    ectx.beginPath();
    ectx.moveTo(x(band[0][0]), y(band[0][2]));
    for (const [i, , hi] of band) ectx.lineTo(x(i), y(hi));
    for (const [i, lo] of band.slice().reverse()) ectx.lineTo(x(i), y(lo));
    ectx.closePath();
    ectx.fill();
  }

  // A step, not a slope: the realised balance does not drift between closes,
  // it sits still and then jumps. Drawing it as a slope would invent a
  // reading for every bar in between.
  ectx.strokeStyle = last >= start ? p.up : p.down;
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
    for (const point of points) if (point[0] <= trade.entryIndex) level = point[1];
    ectx.fillStyle = p.entry;
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
        : '  \u00b7 price x units, not money')
    + (band.length > 1 ? `  \u00b7 grey: 5th to 95th percentile of ${state.band.n} draws of its trades` : '');
}

/* ---------------------------------------------------------- the drawdown */

const ddCanvas = $('drawdown');
const dctx = ddCanvas.getContext('2d');

/*
 * How far each close left the capital under the best it had reached, in per
 * cent of that best, and the worst of them: the close it was reached at, the
 * best it fell from, and the close that climbed back over that best - null
 * when none did. Positions are into the points, as equityPoints() gives them.
 */
function drawdowns(points) {
  let best = -Infinity, from = 0;
  let worst = { pct: 0, from: null, at: null, back: null, best: null };
  const out = points.map(([i, v], k) => {
    if (v >= best) { best = v; from = k; }
    const pct = best > 0 ? (v - best) / best * 100 : 0;
    if (pct < worst.pct) worst = { pct, from, at: k, back: null, best };
    return [i, pct];
  });
  if (worst.at !== null) {
    const k = points.findIndex(([, v], j) => j > worst.at && v >= worst.best);
    worst.back = k < 0 ? null : k;
  }
  return { points: out, worst };
}

/*
 * The drawdown under the capital's axis: the same run's bars across, so the
 * two read together, zero at the top and the worst at the bottom. The worst
 * one's stretch - from the best it fell from to the close back over it - is
 * shaded, and the note says how deep and how long.
 */
function drawDrawdown() {
  const points = equityPoints();
  $('drawdown-panel').hidden = points.length < 2;
  if (points.length < 2) return;
  const { points: dd, worst } = drawdowns(points);
  const run = state.data.candles, timeOf = (k) => run[Math.min(run.length - 1, points[k][0])][0];
  $('drawdown-note').textContent = worst.at === null ? 'never under its best'
    : `worst ${worst.pct.toFixed(2)}% on ${stamp(timeOf(worst.at))} · ${lasted(
      (worst.back === null ? run[run.length - 1][0] : timeOf(worst.back)) - timeOf(worst.from))}`
      + (worst.back === null ? ' from its best to the end, not back over it' : ' from its best until back over it');
  if (!$('drawdown-box').open) return;

  const p = palette();
  const { width, height } = fitCanvas(ddCanvas, dctx, 160);
  dctx.clearRect(0, 0, width, height);
  // the worst at the bottom, however shallow: a run a hundredth of a per cent
  // under its best is read on an axis of hundredths, and says so in the labels
  const low = Math.min(-0.001, worst.pct * 1.15);
  const decimals = -low >= 5 ? 1 : -low >= 0.5 ? 2 : 3;
  const plotW = width - EQ_AXIS.left - EQ_AXIS.right, plotH = height - EQ_AXIS.top - EQ_AXIS.bottom;
  const step = plotW / run.length;
  const x = (i) => EQ_AXIS.left + (i + 0.5) * step;
  const y = (v) => EQ_AXIS.top + v / low * plotH;

  if (worst.at !== null) {
    const a = x(points[worst.from][0]);
    const b = worst.back === null ? width - EQ_AXIS.right : x(points[worst.back][0]);
    dctx.fillStyle = p.span;
    dctx.fillRect(a, EQ_AXIS.top, Math.max(1, b - a), plotH);
  }
  dctx.font = '11px ' + p.mono;
  dctx.textAlign = 'right';
  dctx.textBaseline = 'middle';
  dctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = low * i / 4, py = Math.round(y(v)) + 0.5;
    dctx.strokeStyle = p.grid;
    dctx.beginPath();
    dctx.moveTo(EQ_AXIS.left, py);
    dctx.lineTo(width - EQ_AXIS.right, py);
    dctx.stroke();
    dctx.fillStyle = p.text3;
    dctx.fillText(v.toFixed(decimals) + '%', EQ_AXIS.left - 8, py);
  }
  // a step, as the capital is: the drawdown sits still between closes
  dctx.beginPath();
  dctx.moveTo(x(dd[0][0]), y(0));
  let last = 0;
  for (const [i, v] of dd) {
    dctx.lineTo(x(i), y(last));
    dctx.lineTo(x(i), y(v));
    last = v;
  }
  dctx.lineTo(width - EQ_AXIS.right, y(last));
  dctx.lineTo(width - EQ_AXIS.right, y(0));
  dctx.closePath();
  dctx.fillStyle = dctx.strokeStyle = p.down;
  dctx.globalAlpha = 0.25;
  dctx.fill();
  dctx.globalAlpha = 1;
  dctx.lineWidth = 1.2;
  dctx.stroke();
  if (worst.at !== null) {
    const px = x(points[worst.at][0]), py = y(worst.pct);
    dctx.beginPath();
    dctx.arc(px, py, 3.5, 0, Math.PI * 2);
    dctx.fill();
    dctx.textAlign = px > width / 2 ? 'right' : 'left';
    dctx.textBaseline = 'bottom';
    dctx.fillText(t('worst {pct}%', { pct: worst.pct.toFixed(2) }), px + (px > width / 2 ? -8 : 8), py - 2);
  }
}

/* ----------------------------------------------------------- the heatmap */

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/*
 * The closed trades counted into cells by the time they were entered or
 * exited (`when`), UTC: the week's days across and its hours down, the months
 * across and their days down, or the years across and their months down. A
 * cell is {n, ok, ko, pl}: OK made money, KO lost it, one at nought is
 * neither. cells[x][y], under the labels xs and ys.
 *
 * With the calendar's events (oldest first): `news` 'near' keeps the trades
 * with one within NEAR of their time and 'away' those with none, and marks[x][y]
 * are the events that fell in the cell. The layout 'event' is the other way
 * round: a row an event, a column how long before or after it the time was.
 */
const NEAR = 60 * 60000;
const AROUND = [-120, -60, -30, 0, 30, 60, 120];   // the event layout's edges, minutes

function heatCells(trades, layout, when, events = [], news = 'all') {
  const near = (t) => eventsBetween(t[when + 'Time'] - NEAR, t[when + 'Time'] + NEAR + 1, events).length > 0;
  const closed = trades.filter((t) => t.pl !== null && t.pl !== undefined
    && t.exitTime !== null && t.exitTime !== undefined
    && (news === 'all' || near(t) === (news === 'near')));
  if (layout === 'event') return eventCells(closed, when, events);
  const dates = closed.map((t) => new Date(t[when + 'Time']));
  const years = dates.map((d) => d.getUTCFullYear());
  const first = years.length ? Math.min(...years) : 0, count = years.length ? Math.max(...years) - first + 1 : 0;
  const pad = (n) => String(n).padStart(2, '0');
  const [xOf, yOf, xs, ys] = {
    week: [(d) => (d.getUTCDay() + 6) % 7, (d) => d.getUTCHours(),
           DAYS, Array.from({ length: 24 }, (_, h) => pad(h) + ':00')],
    month: [(d) => d.getUTCMonth(), (d) => d.getUTCDate() - 1,
            MONTHS, Array.from({ length: 31 }, (_, i) => String(i + 1))],
    year: [(d) => d.getUTCFullYear() - first, (d) => d.getUTCMonth(),
           Array.from({ length: count }, (_, i) => String(first + i)), MONTHS],
  }[layout];
  const cells = xs.map(() => ys.map(() => ({ n: 0, ok: 0, ko: 0, pl: 0 })));
  closed.forEach((t, k) => heatAdd(cells[xOf(dates[k])][yOf(dates[k])], t));
  const marks = xs.map(() => ys.map(() => []));
  for (const e of events) {
    const d = new Date(e[0]);
    if (marks[xOf(d)]) marks[xOf(d)][yOf(d)].push(e);   // a year with no trades has no column
  }
  return { xs, ys, cells, marks, trades: closed.length };
}

function heatAdd(cell, t) {
  cell.n++;
  cell.pl += t.pl;
  if (t.pl > 0) cell.ok++;
  else if (t.pl < 0) cell.ko++;
}

// The trades by their time from an event's: a row an event (currency and
// title), busiest first, a column a stretch of AROUND. A trade near two
// events is in both rows, near the same one twice in the nearest.
function eventCells(closed, when, events) {
  const edges = AROUND.map((m) => m * 60000), last = edges.length - 1;
  const rows = new Map();
  let counted = 0;
  for (const t of closed) {
    const ms = t[when + 'Time'], seen = new Set();
    const near = eventsBetween(ms - edges[last] + 1, ms - edges[0] + 1, events)
      .sort((a, b) => Math.abs(ms - a[0]) - Math.abs(ms - b[0]));
    for (const e of near) {
      const name = `${e[1]} ${e[3]}`;
      if (seen.has(name)) continue;
      seen.add(name);
      if (!rows.has(name)) rows.set(name, edges.slice(1).map(() => ({ n: 0, ok: 0, ko: 0, pl: 0 })));
      heatAdd(rows.get(name)[edges.findIndex((_, i) => ms - e[0] < edges[i + 1])], t);
    }
    if (seen.size) counted++;
  }
  const total = (name) => rows.get(name).reduce((sum, c) => sum + c.n, 0);
  const ys = [...rows.keys()].sort((a, b) => total(b) - total(a));
  const span = (m) => m ? (m > 0 ? '+' : '−') + (Math.abs(m) >= 60 ? Math.abs(m) / 60 + 'h' : Math.abs(m) + 'm') : '0';
  const xs = AROUND.slice(1).map((m, i) => `${span(AROUND[i])}…${span(m)}`);
  return { xs, ys, cells: xs.map((_, x) => ys.map((y) => rows.get(y)[x])), marks: null, trades: counted };
}

/*
 * The heatmap, as a table: a cell's number is its text and its tooltip says
 * the rest. What its colour reads is the choice above it:
 *  - win rate: green over half OK, red under, and the fuller the more trades
 *    it holds, so one lucky trade is a pale cell and not a bright one;
 *  - P&L: green made, red lost, the fuller the more;
 *  - OK and KO: two bars, as long as the counts against the busiest cell.
 * A corner marks where the calendar's events fell, red for a high one.
 */
function drawHeat() {
  const trades = state.data ? state.data.trades : [];
  $('heat-panel').hidden = !trades.length;
  if (!trades.length || !$('heat-box').open) return;
  const layout = $('heat-layout').value, mode = $('heat-cell').value, when = $('heat-when').value;
  const impact = $('heat-impact').value, kind = impact === 'high' ? 'high' : 'high or medium';
  const news = layout === 'event' ? 'all' : $('heat-news').value;
  $('heat-news').disabled = layout === 'event';
  const events = (state.events || []).filter((e) => impact !== 'high' || e[2] === 'high');
  const { xs, ys, cells, marks, trades: counted } = heatCells(trades, layout, when, events, news);
  const all = cells.flat();
  const most = Math.max(1, ...all.map((c) => c.n));
  const widest = Math.max(1e-12, ...all.map((c) => Math.abs(c.pl)));
  const decimals = widest >= 100 ? 0 : widest >= 1 ? 1 : amountDecimals(widest);
  const tint = (token, share) => `color-mix(in srgb, var(--${token}) ${Math.round(8 + 52 * share)}%, transparent)`;
  $('heat-note').textContent = (layout === 'event'
    ? `${counted} closed trades with their ${when} within 2h of a ${kind} event, by how long before or after it`
    : `${counted} closed trades by ${when} time, UTC`
      + { all: '', near: ` · only those within 1h of a ${kind} event`,
          away: ` · only those with no ${kind} event within 1h` }[news])
    + (state.events === null ? ' · asking the calendar…'
       : !events.length ? ` · no ${kind} calendar events in this run` : '');

  const table = $('heat');
  table.textContent = '';
  const head = table.createTHead().insertRow();
  head.appendChild(document.createElement('th'));
  for (const label of xs) {
    const th = document.createElement('th');
    th.textContent = label;
    head.appendChild(th);
  }
  const body = table.createTBody();
  ys.forEach((yLabel, yi) => {
    const row = body.insertRow();
    const th = document.createElement('th');
    th.textContent = yLabel;
    row.appendChild(th);
    xs.forEach((xLabel, xi) => {
      const c = cells[xi][yi], td = row.insertCell();
      const fell = marks ? marks[xi][yi] : [];
      const said = fell.length ? ` · ${heatEvents(fell)}` : '';
      if (fell.length) td.className = 'news' + (fell.some((e) => e[2] === 'high') ? '' : ' medium');
      if (!c.n) { if (fell.length) td.title = `${xLabel} ${yLabel}${said}`; return; }
      const rate = (c.ok + c.ko) ? c.ok / (c.ok + c.ko) : 0.5;
      td.title = `${xLabel} ${yLabel} · ${c.n} trade${c.n === 1 ? '' : 's'} · ${c.ok} OK · ${c.ko} KO`
        + ` · won ${Math.round(rate * 100)}% · P&L ${c.pl >= 0 ? '+' : ''}${amount(c.pl, decimals)}` + said;
      // the colour and not the whole background, which holds the events' corner
      if (mode === 'win') {
        td.textContent = `${c.ok}/${c.ko}`;
        td.style.backgroundColor = tint(rate >= 0.5 ? 'up' : 'down', Math.abs(rate - 0.5) * 2 * Math.sqrt(c.n / most));
      } else if (mode === 'pl') {
        td.textContent = (c.pl >= 0 ? '+' : '') + amount(c.pl, decimals);
        td.style.backgroundColor = tint(c.pl >= 0 ? 'up' : 'down', Math.abs(c.pl) / widest);
      } else {
        for (const [kind, count] of [['ok', c.ok], ['ko', c.ko]]) {
          const bar = document.createElement('span');
          // not .bar, which is the page's own bar across the top (app.css)
          bar.className = 'heat-bar ' + kind;
          bar.style.width = `${Math.max(count ? 6 : 0, count / most * 100)}%`;
          td.appendChild(bar);
        }
      }
    });
  });
}

// a cell's events for its tooltip: how many, and the commonest by name
function heatEvents(fell) {
  const names = new Map();
  for (const e of fell) names.set(`${e[1]} ${e[3]}`, (names.get(`${e[1]} ${e[3]}`) || 0) + 1);
  const top = [...names].sort((a, b) => b[1] - a[1]);
  return `${fell.length} event${fell.length === 1 ? '' : 's'}: `
    + top.slice(0, 3).map(([name, n]) => n > 1 ? `${name} \u00d7${n}` : name).join(', ')
    + (top.length > 3 ? ` and ${top.length - 3} more` : '');
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
  const p = palette();
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

  pctx.font = '11px ' + p.mono;
  pctx.textBaseline = 'middle';
  pctx.textAlign = 'right';
  pctx.lineWidth = 1;
  // two labels and no more: this strip is read for its shape and for the
  // number at the top, and a grid of five would be most of its height
  for (const v of [high, low]) {
    const py = Math.round(y(v)) + 0.5;
    pctx.strokeStyle = p.grid;
    pctx.beginPath();
    pctx.moveTo(PN_AXIS.left, py);
    pctx.lineTo(PN_AXIS.left + plotW, py);
    pctx.stroke();
    pctx.fillStyle = p.text3;
    pctx.fillText(price(v), PN_AXIS.left - 8, py);
  }

  for (const line of lines) {
    pctx.strokeStyle = p.text3;
    pctx.lineWidth = 1.25;
    pctx.setLineDash(line.dash);
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
  pctx.setLineDash([]);

  const last = lines[0].values[runIndex(view.to)];
  $('curve-note').textContent = lines.map((l) => l.label).join(', ')
    + (last === null || last === undefined ? '' : ` \u00b7 last ${price(last)}`);
}

function drawLevels() {
  const p = palette();
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

  lctx.font = '11px ' + p.mono;
  lctx.textBaseline = 'middle';
  lctx.lineWidth = 1;
  lctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    lctx.strokeStyle = p.grid;
    lctx.beginPath();
    lctx.moveTo(LV_AXIS.left, py);
    lctx.lineTo(LV_AXIS.left + plotW, py);
    lctx.stroke();
    lctx.fillStyle = p.text3;
    lctx.fillText(price(v), LV_AXIS.left - 8, py);
  }

  // the closes, thin and grey: a level means nothing without the price that
  // made it, and a line is all the context this panel needs
  lctx.strokeStyle = p.border;
  lctx.beginPath();
  for (let i = 0; i < bars.length; i++) {
    const py = y(bars[i][4]);
    if (i) lctx.lineTo(x(i), py); else lctx.moveTo(x(i), py);
  }
  lctx.stroke();

  for (const l of list) {
    const left = x(l.at);
    band(lctx, l, left, LV_AXIS.left + plotW - left, y, p);
    // the label beside the band's own dash, which already says which kind it is
    lctx.fillStyle = p.text3;
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

// The run's bars a trade was held, from the entry's to the exit's: 0 is in
// and out on one bar. The same count max bars closes on (TradeTimer in
// portfolio/session.py). null for one still open.
function barsHeld(trade) {
  const a = trade.entryIndex, b = trade.exitIndex;
  return a === null || a === undefined || b === null || b === undefined ? null : b - a;
}

// a result in risk units (backtest/ledger.rMultiple); blank with no stop,
// or for a run saved before R was kept
function rText(r) {
  return r === null || r === undefined ? '' : (r > 0 ? '+' : '') + r.toFixed(2) + 'R';
}

// the run's trades as a CSV file, one row a trade
function downloadTrades() {
  const trades = state.data ? state.data.trades : [];
  const head = ['n', 'signal', 'side', 'units', 'entry time', 'entry', 'exit time', 'exit',
                'stop', 'target', 'outcome', 'pl', 'r'];
  const cell = (v) => v === null || v === undefined ? '' : /[",\n]/.test(String(v))
    ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  const iso = (ms) => ms === null || ms === undefined ? '' : new Date(ms).toISOString();
  const rows = trades.map((t) => [t.n, iso(t.signalTime), t.direction, t.units, iso(t.entryTime),
    t.entryPrice, iso(t.exitTime), t.exitPrice, t.stopLoss, t.takeProfit, t.outcome, t.pl, t.r]
    .map(cell).join(','));
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([[head.join(',')].concat(rows).join('\n') + '\n'],
                                           { type: 'text/csv' }));
  const d = state.data || {};
  link.download = ['trades', d.strategy, d.instrument, d.granularity].filter(Boolean).join('-') + '.csv';
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderTrades() {
  const body = $('trade-rows');
  body.textContent = '';
  const trades = state.data ? state.data.trades : [];
  const pages = Math.max(1, Math.ceil(trades.length / PAGE_SIZE));
  state.page = Math.min(Math.max(0, state.page || 0), pages - 1);
  const first = state.page * PAGE_SIZE;
  const last = Math.min(trades.length, first + PAGE_SIZE);
  $('trade-pager').hidden = pages < 2;
  $('trades-csv').hidden = !trades.length;
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
    cell.colSpan = 15;
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
      ['num', barsHeld(trade) === null ? '' : String(barsHeld(trade))],
      ['num', stopCell(trade)],
      ['num', price(trade.takeProfit)],
      ['outcome-cell', null],
      ['num pl ' + (trade.pl > 0 ? 'good' : trade.pl < 0 ? 'bad' : ''), pl(trade.pl)],
      ['num', rText(trade.r)],
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
    tag.className = 'k series';
    tag.innerHTML = dashSample(DASHES[i % DASHES.length]);
    tag.append(curve.label);
    box.appendChild(tag);
  });
}

// how long the trades were held, in the run's bars: shortest, mean, longest
function heldStats() {
  const held = state.data.trades.map(barsHeld).filter((v) => v !== null);
  const show = (v) => held.length ? v : 'n/a';
  return [
    stat('bars min', show(String(held.reduce((a, b) => Math.min(a, b), Infinity)))),
    stat('bars avg', show((held.reduce((a, b) => a + b, 0) / held.length).toFixed(1))),
    stat('bars max', show(String(held.reduce((a, b) => Math.max(a, b), -Infinity)))),
  ];
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
    stat('expectancy R', rText(r.expectancyR) || 'n/a'),
    stat('avg win', pl(r.averageWin)),
    stat('avg loss', pl(r.averageLoss)),
    stat('max drawdown', pl(r.maxDrawdown), 'bad'),
    stat('run of wins', String(r.maxConsecutiveWins)),
    stat('run of losses', String(r.maxConsecutiveLosses)),
    ...heldStats(),
  );
  // what the account needed on margin, and whether it always had room (report.margin)
  const m = state.data.margin;
  if (m) {
    const box = stat(`margin ${m.leverage}:1`, `${m.ok ? '\u2713' : '\u2717'} peak ${m.peakMarginPct.toFixed(1)}%`,
                     m.ok ? '' : 'bad');
    box.title = marginText(m);
    report.append(box);
  }

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

  // the KPIs in words (analysis.js): only for a run sized off an account,
  // the only kind whose percentages are of something
  const d = state.data;
  $('analysis-body').replaceChildren(...(d.kpi && d.risk
    ? [Object.assign(document.createElement('h3'), { textContent: 'KPI analysis' }), ...analysisNodes({ kpi: d.kpi, report: r, margin: m })] : []));
}

/* ------------------------------------------------------------ interaction */

function select(index) {
  const trades = state.data ? state.data.trades : [];
  if (index === null || index < 0 || index >= trades.length) return;
  state.selected = index;
  state.page = Math.floor(index / PAGE_SIZE);
  const trade = trades[index];

  // finer bars fetched around another stretch do not hold this trade, and
  // looking it up in them lands on their edge: back to the run's own bars
  const s = state.series, end = trade.exitTime ?? trade.entryTime;
  if (s && (trade.entryTime < s.from || end > s.to)) { state.series = null; renderDetail(); }

  const last = bars().length - 1;
  const entry = tradeBar(trade, 'entry');
  const exit_ = tradeBar(trade, 'exit');
  const from = (entry === null || entry === undefined) ? 0 : entry;
  const to = (exit_ === null || exit_ === undefined) ? last : exit_;
  state.view = {
    from: Math.max(0, from - PADDING_BARS),
    to: Math.min(last, to + PADDING_BARS),
  };
  state.scale = null;

  $('reset').hidden = false;

  renderTrades();
  draw();
  drawEquity();
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
  showReset();
  draw();
  // and then, once the wheel stops, the bars themselves may change: see
  // retune(). The chart is redrawn first, so the zoom never waits on a
  // request to feel like it happened. Not in the middle of a drag: new bars
  // would renumber the ones the drag started from, and the mouseup asks
  if (!pan) retune();
}

function showReset() {
  $('reset').hidden = state.view === null && !state.series && !state.scale;
}

// the prices stretched by hand: they stay as set while the bars move under
// them, until a double click on the price axis or the reset gives them back
function setScale(high, low) {
  state.scale = { high, low };
  showReset();
  draw();
}

// which part of the chart a pointer is on: the price axis down the left, the
// time axis along the bottom, or the plot
function zone(event) {
  const rect = canvas.getBoundingClientRect();
  if (event.clientX - rect.left < AXIS.left) return 'price';
  if (event.clientY - rect.top > rect.height - AXIS.bottom) return 'time';
  return 'plot';
}

function resetView() {
  state.view = null;
  state.scale = null;
  state.measure = null;
  state.selected = null;
  // back to the bars the strategy read. "The whole range" at five minute
  // detail is not the whole range, it is a refusal from the service
  state.series = null;
  state.detail = 'auto';
  renderDetail();
  $('reset').hidden = true;
  $('chart-zoom').textContent = '';
  renderTrades();
  draw();
  drawEquity();
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

/*
 * The stores and what each strategy says it does: the first for the ladder
 * of timeframes the chart can zoom through, the second for the line over
 * it. Read from the service rather than listed here, where they would drift.
 */
async function loadStores() {
  const { instruments, descriptions } = await ask('api/stores');
  state.instruments = instruments || [];
  state.about = descriptions || {};
}

// AB-INVERSA and the FTW ones were strategies before `inverse` was an
// option: an old run is run again as its strategy turned round
function unalias(fields) {
  const alias = /^(.+)-INVERSA$/.exec(fields.strategy || '');
  return alias ? { ...fields, strategy: alias[1], inverse: '1' } : fields;
}

async function run(fields, extra) {
  message('running the backtest…', 'info');
  const stop = watchProgress();
  try {
    // extra: a run of a set being made again, kept with the set this time
    show(await post('api/backtest', JSON.stringify(
      { ...unalias(fields), ...extra, confirmed: true })));
    message('');
  } catch (error) {
    message(String(error.message || error));
  } finally {
    stop();
  }
}

/* A backtest's payload on the page, whether just run or redrawn. */
function show(data) {
  state.data = data;
  state.decimals = decimalsOf(data.candles);
  // the ask high (see scales()), where a store has one
  state.quoted = data.candles.length && data.candles[0][5] != null
    ? decimalsOf(data.candles, 5) : state.decimals;
  state.view = null;
  state.selected = null;
  state.page = 0;
  $('reset').hidden = true;
  $('chart-zoom').textContent = '';
  loadEvents();
  // the run's id: a set's run as set/number, any other as the saved run's
  const where = new URLSearchParams(location.search);
  const id = where.get('sweep') ? `${where.get('sweep')}/${where.get('run')}` : data.runId;
  state.runId = data.runId || null;
  state.sweepRef = where.get('sweep')
    ? { sweep: where.get('sweep'), run: Number(where.get('run')) } : null;
  state.pushed = data.pushed || null;
  renderStar();
  loadBand();
  $('holdout-note').hidden = !data.holdout;
  if (data.holdout) $('holdout-note').textContent = `holdout starts ${data.holdout.cut}: the run stops there`;
  $('journal-link').href = 'journal?strategy=' + encodeURIComponent(data.strategy);
  $('journal-link').hidden = !data.strategy;
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
  drawDrawdown();
  drawHeat();
}

/*
 * The Monte Carlo band of the run on show (api/run/band, performance/
 * montecarlo.py): the 5th to the 95th percentile of where its capital could
 * have been after each close, its own trades drawn again a thousand times.
 * Drawn under the curve once it comes; a run not saved has none.
 */
async function loadBand() {
  state.band = null;
  const ref = state.sweepRef;
  const query = ref ? `sweep=${encodeURIComponent(ref.sweep)}&n=${ref.run}`
    : state.runId ? `run=${encodeURIComponent(state.runId)}` : null;
  if (!query) return;
  const shown = state.data;
  try {
    const { band } = await (await fetch('api/run/band?' + query)).json();
    if (state.data !== shown) return;
    state.band = band || null;
    drawEquity();
  } catch (error) { /* no band: the curve alone */ }
}

/* ------------------------------------------------------------- address */

/*
 * The run on show is the address: its fields, and the set and number when it
 * is a run of a simulation set (the simulate page's links). A reload is the
 * same run, asked of the service's cache or of the disk, never a new form.
 */
function fromAddress() {
  const params = new URLSearchParams(location.search);
  const { sweep, run } = Object.fromEntries(params);
  for (const key of ['sweep', 'run', 'trade']) params.delete(key);
  if (!params.get('instrument')) return null;
  return { fields: Object.fromEntries(params), sweep, run };
}

/* -------------------------------------------------------------- listeners */

$('reset').addEventListener('click', resetView);

// set by a drag that moved the chart, read and cleared by the click that
// follows it: a mouseup after a pan still fires a click, and without this
// every pan would also select whatever trade it finished over
let dragged = false;

$('page-prev').addEventListener('click', () => { state.page--; renderTrades(); });
$('page-next').addEventListener('click', () => { state.page++; renderTrades(); });
$('trades-csv').addEventListener('click', downloadTrades);
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
  if (!state.data || !state.data.trades.length || zone(event) !== 'plot') return;
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
$('drawdown-box').addEventListener('toggle', drawDrawdown);
$('heat-box').addEventListener('toggle', drawHeat);
for (const id of ['heat-layout', 'heat-cell', 'heat-when', 'heat-news', 'heat-impact']) $(id).addEventListener('change', drawHeat);
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

$('events').addEventListener('click', () => {
  state.showEvents = !state.showEvents;
  $('events').setAttribute('aria-pressed', String(state.showEvents));
  draw();
});

$('levels').addEventListener('click', () => {
  state.levels = !state.levels;
  $('levels').setAttribute('aria-pressed', String(state.levels));
  draw();
});

canvas.addEventListener('wheel', (event) => {
  if (!state.data || !state.data.candles.length) return;
  event.preventDefault();
  // over the price axis, or with shift: the prices stretch about the one
  // under the pointer and the bars stay. Shift turns the wheel sideways in
  // some browsers, hence either delta
  if (event.shiftKey || zone(event) === 'price') {
    const r = state.range, rect = canvas.getBoundingClientRect();
    const at = r.high - (event.clientY - rect.top - AXIS.top)
      / (rect.height - AXIS.top - AXIS.bottom) * (r.high - r.low);
    const k = (event.deltaY || event.deltaX) > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    return setScale(at + (r.high - at) * k, at - (at - r.low) * k);
  }
  const view = visible();
  const span = view.to - view.from + 1;
  const pivot = barUnder(event.clientX);
  const wanted = span * (event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
  // the bar under the pointer keeps its share of the window, which is what
  // makes the wheel feel like it is zooming on something rather than on the
  // middle of the chart
  zoomTo(pivot - (pivot - view.from) / span * wanted, wanted);
}, { passive: false });

// Dragging the plot moves the window, and the prices too once they are set by
// hand; dragging an axis stretches it. A drag under PAN_SLOP pixels is a
// click and is left to the handler above, which is what picks a trade.
let pan = null;
canvas.addEventListener('mousedown', (event) => {
  if (!state.data || !state.data.candles.length) return;
  // otherwise the browser starts its own drag - a text selection that runs
  // out of the canvas and swallows the mousemove the pan is made of
  event.preventDefault();
  const view = visible(), where = zone(event);
  // on the plot, shift measures and ctrl (cmd on a Mac) boxes a stretch to zoom onto
  const mode = where !== 'plot' ? where : event.shiftKey ? 'measure'
    : (event.ctrlKey || event.metaKey) ? 'box' : 'plot';
  pan = { where: mode, x: event.clientX, y: event.clientY, start: local(event), range: state.range,
          from: view.from, span: view.to - view.from + 1,
          bars: (canvas.clientWidth - AXIS.left - AXIS.right) / view.candles.length,
          plotH: canvas.clientHeight - AXIS.top - AXIS.bottom, moved: false };
  // a measure on show goes at the next press, whatever the press is for
  state.measure = mode === 'measure' ? { a: dataAt(event), b: dataAt(event) } : null;
  if (mode !== 'measure' && mode !== 'box') state.pointer = null;
  if (mode === 'plot') canvas.style.cursor = 'grabbing';
  drawOver();
});

// ctrl and a click is the context menu on a Mac, and here the start of a box
canvas.addEventListener('contextmenu', (event) => { if (event.ctrlKey) event.preventDefault(); });

// the price axis back to fitting the bars
canvas.addEventListener('dblclick', (event) => {
  if (zone(event) !== 'price' || !state.scale) return;
  state.scale = null;
  showReset();
  draw();
});
window.addEventListener('mouseup', () => {
  if (pan && pan.box) boxZoom(pan.box);
  // a shift-click that never moved measured nothing, and is left a click
  if (pan && pan.where === 'measure' && !pan.moved) state.measure = null;
  // a pan that ended may have walked out of the window that was fetched, and
  // asking for the next one is the same question the wheel asks
  if (pan && pan.moved) retune();
  pan = null;
  canvas.style.cursor = '';
  drawOver();
});

document.addEventListener('keydown', (event) => {
  if (event.target.matches('input, select, button')) return;
  if (event.key === 'Escape') {
    // a zoom box half drawn is let go of, and only that
    if (pan && pan.where === 'box') { pan = null; return drawOver(); }
    return resetView();
  }
  const key = chartKey(event);
  if (key && state.data && state.data.candles.length) { event.preventDefault(); return keyed(key); }
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
    const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < PAN_SLOP && !pan.moved) return;
    pan.moved = dragged = true;
    if (pan.where === 'measure') {
      state.measure.b = dataAt(event);
      state.pointer = local(event);
      $('chart-zoom').textContent = measureOf(state.measure);
      return drawOver();
    }
    if (pan.where === 'box') {
      const at = local(event);
      pan.box = { x0: pan.start.x, y0: pan.start.y, x1: at.x, y1: at.y };
      state.pointer = at;
      return drawOver();
    }
    const r = pan.range;
    if (pan.where === 'price') {
      // down squeezes the prices together, up pulls them apart, about the middle
      const mid = (r.high + r.low) / 2, half = (r.high - r.low) / 2 * Math.exp(dy / AXIS_DRAG);
      return setScale(mid + half, mid - half);
    }
    if (pan.where === 'time') {
      // right fewer bars, left more, the last one on the chart staying put
      const span = pan.span * Math.exp(-dx / AXIS_DRAG);
      return zoomTo(pan.from + pan.span - span, span);
    }
    if (state.scale) {
      const shift = dy / pan.plotH * (r.high - r.low);
      state.scale = { high: r.high + shift, low: r.low + shift };
    }
    return zoomTo(pan.from - dx / pan.bars, pan.span);
  }
  canvas.style.cursor = { price: 'ns-resize', time: 'ew-resize' }[zone(event)] || '';
  state.pointer = zone(event) === 'plot' ? local(event) : null;
  drawOver();
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
    + `  H-L ${span.toFixed(1)} pips`
    + (state.showEvents ? eventsOfBar(view.from + i).map((e) => '  \u00b7  ' + eventText(e, false)).join('') : '');
});

canvas.addEventListener('mouseleave', () => {
  state.pointer = null;
  drawOver();
  if (state.selected === null) { $('chart-zoom').textContent = ''; return; }
  const trade = state.data.trades[state.selected];
  $('chart-zoom').textContent =
    `trade ${trade.n} · ${trade.direction} · ${stamp(trade.entryTime)}`
    + ` → ${trade.exitTime === null ? openAtEnd() : stamp(trade.exitTime)}`;
});

window.addEventListener('resize', () => { draw(); drawEquity(); drawLevels(); drawDrawdown(); });

// taller or shorter, from the grip under the chart (chartGrip in menu.js)
state.height = chartGrip($('chart-grip'), 420, (height) => { state.height = height; draw(); });

// The stretches the buttons over the chart jump to: a day, a week, a month
// and three, ending where the chart does - or starting where the run does,
// when there is not that much of it before.
const RANGES = { '1D': 864e5, '1W': 7 * 864e5, '1M': 30 * 864e5, '3M': 91 * 864e5 };
$('ranges').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-range]');
  if (!button || !state.data || !state.data.candles.length) return;
  const run = state.data.candles, d = RANGES[button.dataset.range];
  const from = Math.max(run[0][0], viewTimes()[1] - d);
  showTimes(from, Math.min(run[run.length - 1][0], from + d));
});

// the navigator's gestures: see drawNav()
let navDrag = null;
navCanvas.addEventListener('mousedown', (event) => {
  if (!state.data || !state.data.candles.length) return;
  event.preventDefault();
  let [a, b] = navWindow();
  const part = navPart(event), at = navBar(event);
  if (part === 'out') {
    // a press away from the box takes it there, and a drag goes on from it
    const half = (b - a) / 2;
    navShow(at - half, at + half, true);
    [a, b] = navWindow();
  }
  navDrag = { part: part === 'out' ? 'move' : part, at, a, b };
});
window.addEventListener('mousemove', (event) => {
  if (!navDrag) return;
  const d = navBar(event) - navDrag.at;
  let { a, b } = navDrag;
  if (navDrag.part === 'move') { a += d; b += d; }
  // an edge stops at the run's own, where the box would otherwise slide
  if (navDrag.part === 'from') a = Math.max(0, Math.min(a + d, b - MIN_BARS));
  if (navDrag.part === 'to') b = Math.min(state.data.candles.length - 1, Math.max(b + d, a + MIN_BARS));
  navShow(a, b, true);
});
window.addEventListener('mouseup', () => {
  if (!navDrag) return;
  navDrag = null;
  retune(true);
});
navCanvas.addEventListener('mousemove', (event) => {
  if (navDrag || !state.data || !state.data.candles.length) return;
  navCanvas.style.cursor = { from: 'ew-resize', to: 'ew-resize', move: 'grab', out: 'pointer' }[navPart(event)];
});
navCanvas.addEventListener('wheel', (event) => {
  if (!state.data || !state.data.candles.length) return;
  event.preventDefault();
  const [a, b] = navWindow(), span = b - a + 1, at = navBar(event);
  const wanted = span * ((event.deltaY || event.deltaX) > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
  const from = at - (at - a) / span * wanted;
  navShow(from, from + wanted - 1, true);
  retune();
}, { passive: false });

/* ------------------------------------------------------------------ write */

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

$('help-open').addEventListener('click', () => $('help-dialog').showModal());

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
  // the gate is for a starred run of a set: its neighbours are the plateau
  $('gate-run').hidden = !(state.sweepRef && source && favouriteOf(source));
  // verify, for a run another server pushed here (scripts/sync.py push --run)
  $('verify-run').hidden = !(state.sweepRef && state.pushed);
}

$('verify-run').addEventListener('click', async () => {
  const ref = state.sweepRef;
  if (!ref) return;
  $('verify-run').disabled = true;
  message('verifying\u2026');
  try {
    const told = await post('api/verify/run', JSON.stringify({ sweep: ref.sweep, n: ref.run }));
    message(told.ok ? `verify: the same ${told.here.trades} trades here`
      : `verify: trade ${told.first.trade} is not the same here`);
  } catch (error) {
    message(String(error.message || error));
  } finally {
    $('verify-run').disabled = false;
  }
});

/*
 * The gate SIM -> DEMO of the run on show (api/gate, performance/gate.py):
 * started here, followed while it runs its backtests, its verdict a line a
 * check. A version reads its holdout once: the verdict stays on its card.
 */
function gateRow(row) {
  const tr = document.createElement('tr');
  for (const [text, cls] of [[row.ok ? '\u2713' : '\u2717', row.ok ? 'good' : 'bad'], [row.check],
                             [row.value === null || row.value === undefined ? 'n/a' : String(row.value), 'num'],
                             [row.need]]) {
    const td = document.createElement('td');
    td.textContent = text;
    if (cls) td.className = cls;
    tr.appendChild(td);
  }
  return tr;
}

function showGate(job) {
  const r = job.result;
  $('gate-sub').textContent = r ? `${r.card.label} \u00b7 holdout from ${r.cut}` : '';
  $('gate-table').hidden = !r;
  $('gate-notes').textContent = '';
  if (job.running) {
    $('gate-state').textContent = `checking: ${job.stage}\u2026`;
    return;
  }
  if (job.error || !r) {
    $('gate-state').textContent = job.error || '';
    return;
  }
  $('gate-state').textContent = r.ok ? 'passed: the version is ready for demo, its reference on its card'
    : r.holdout ? 'not passed on the holdout: the version stays in SIM - try again or discard it, your call'
      : 'not passed on the development period: the holdout was not opened';
  const body = $('gate-rows');
  body.textContent = '';
  for (const row of r.development) body.appendChild(gateRow(row));
  for (const row of r.holdout || []) body.appendChild(gateRow(row));
  const read = r.readBy || {};
  $('gate-notes').textContent = [
    r.openings ? `the holdout from ${r.cut} has been opened ${r.openings} times` : '',
    read.sets || read.runs ? (read.whole ? `holdout read by ${read.sets} sets and ${read.runs} runs, up to the last bar`
      : `holdout read by ${read.sets} sets and ${read.runs} runs, up to ${read.until}`) : '',
  ].filter(Boolean).join(' \u00b7 ');
}

$('gate-run').addEventListener('click', async () => {
  const ref = state.sweepRef;
  if (!ref) return;
  $('gate-dialog').showModal();
  showGate({ running: true, stage: 'starting' });
  try {
    let job = await post('api/gate', JSON.stringify({ sweep: ref.sweep, n: ref.run }));
    while (job.running) {
      showGate(job);
      await new Promise((done) => setTimeout(done, 1500));
      job = await ask('api/gate');
    }
    showGate(job);
  } catch (error) {
    $('gate-state').textContent = String(error.message || error);
  }
});

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

/*
 * Prev and next: the runs of the table this one was opened from, in that
 * table's order, left in sessionStorage by the page that has the table
 * (sim.js, mix.js). A run opened from anywhere else has none. They replace
 * the address rather than push one, so back still goes straight to the table.
 */
function walkRuns() {
  let list = null;
  try { list = JSON.parse(sessionStorage.getItem('run-list')); } catch (error) { /* none */ }
  const here = new URLSearchParams(location.search);
  const key = (q) => `${q.get('sweep')}/${q.get('run')}`;
  const at = here.get('sweep') && list && Array.isArray(list.runs)
    ? list.runs.findIndex((href) => key(new URLSearchParams(href.split('?')[1])) === key(here)) : -1;
  if (at < 0) return;
  const go = (step) => { if (list.runs[at + step]) location.replace(list.runs[at + step]); };
  $('run-nav').hidden = false;
  $('run-back').href = list.back;
  $('run-at').textContent = `${at + 1} of ${list.runs.length}`;
  $('run-prev').disabled = at === 0;
  $('run-next').disabled = at === list.runs.length - 1;
  $('run-prev').addEventListener('click', () => go(-1));
  $('run-next').addEventListener('click', () => go(1));
  document.addEventListener('keydown', (event) => {
    // shift and the arrows are the chart's pan (chartKey in menu.js)
    if (event.target.matches('input, select, textarea') || event.altKey || event.ctrlKey
      || event.metaKey || event.shiftKey) return;
    if (event.key === 'ArrowLeft') go(-1);
    if (event.key === 'ArrowRight') go(1);
  });
}

async function start() {
  walkRuns();
  const saved = fromAddress();
  // the run asked for with the stores and not after them: those take seconds
  // to answer while a sweep runs, and prev and next wait on them every time.
  // A run of a set is read where the set keeps it, and the cache is asked
  // only when it is not there: a miss there costs seconds too
  const cached = () => post('api/backtest', JSON.stringify({ ...saved.fields, cachedOnly: true }));
  let [, data] = await Promise.all([loadStores(), saved
    && (saved.sweep ? ask(`api/sweeps/${saved.sweep}/${saved.run}`) : cached())]);
  // not awaited: the stars can wait, the run cannot wait on them
  loadFavourites().catch((error) => message(String(error.message || error)));
  if (!saved) {
    message('no run to show: open one from a simulation set', 'info');
    return;
  }
  // by the strategy's name as the menus spell it: the payload's is the
  // label, parameters and all
  const about = state.about[unalias(saved.fields).strategy] || '';
  $('chart-about').textContent = about;
  $('chart-about').hidden = !about;
  // a set older than its runs on disk: the service may still hold it
  if (data.cached === false && saved.sweep) data = await cached();
  if (data.cached === false) {
    // not kept any more (a set older than its runs on disk): run it again,
    // which is what asking to look at it means; and kept with the set this time
    await run(saved.fields, saved.sweep ? { sweep: saved.sweep, sweepRun: saved.run } : {});
    return;
  }
  show(data);
}

start().catch((error) => message(String(error.message || error)));
