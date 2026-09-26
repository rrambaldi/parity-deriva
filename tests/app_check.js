/*
 * Does static/app.js load, and does it draw the capital curve it says it does?
 *
 *     node parity_deriva/tests/app_check.js
 *
 * Run by hand. It is not part of the Python suite and nothing here depends on
 * it, because the front end has no test story and inventing one - a browser, a
 * runner, a DOM library - would be a larger change than the page it checks.
 * What it does need is node, which is not a dependency of this project either;
 * on a machine without it, the arithmetic the curve stands on is still pinned
 * in tests/web_test.py, which is where the truth about a balance lives. This
 * only checks the plotting.
 *
 * The page is a browser script rather than a module, so it is run in a vm
 * context with the smallest DOM that gets through it, and its top-level
 * bindings are read back by evaluating more source in the same context.
 */

const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'static', 'app.js'), 'utf8');
// Was: app.js alone. Now: menu.js's shared globals first - palette() and the
// dashes, which the run page's canvases read since the layout of DESIGN.md -
// and not its menu, which is wiring over a page this sandbox does not have
const menu = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'static', 'menu.js'), 'utf8');
const shared = menu.slice(0, menu.indexOf('\n(function () {'));

// every canvas call is recorded rather than performed, so "it drew something"
// is a question that can be asked
const calls = [];
// where each line started, so "it drew a level" can be asked about the x it
// was drawn from - which is the whole point of the swing lines below
const starts = [];
// and every box, because where the setup box ends is the whole of what it
// claims: the signal bar, not the entry
const rects = [];
const ctx2d = new Proxy({}, { get: (t, k) => {
  if (k === 'measureText') return () => ({ width: 40 });
  return (...a) => {
    if (k === 'moveTo') starts.push(a[0]);
    if (k === 'strokeRect') rects.push(a);
    return calls.push(k);
  };
} });

const element = () => new Proxy({
  getContext: () => ctx2d, appendChild() {}, append() {},
  // kept rather than dropped: the click that picks a trade off the chart is
  // one of the two ways to select one, and it is arithmetic, not wiring
  addEventListener(type, fn) { (this.on || (this.on = {}))[type] = fn; },
  getBoundingClientRect: () => ({ left: 0, width: 900 }),
  dataset: {}, style: {}, classList: { add() {} }, selectedOptions: [],
  options: [], clientWidth: 900, textContent: '', value: '', hidden: false,
  scrollIntoView() {},
}, { get: (t, k) => (k in t ? t[k] : undefined), set: (t, k, v) => (t[k] = v, true) });

// by id, because the page sets a property on one element and reads it back
const nodes = {};
const sandbox = {
  console, Math, Number, String, Array, JSON, Date, isFinite, URLSearchParams,
  document: { getElementById: (id) => (nodes[id] || (nodes[id] = element())),
              createElement: element, querySelector: () => null,
              addEventListener() {}, documentElement: {} },
  // no stylesheet here: every colour of the theme reads as empty
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  window: { devicePixelRatio: 2, addEventListener() {} },
  history: { replaceState() {} }, location: { search: '' },
  // web/static/i18n.js's, in English: a template's values put in
  t: (text, values) => String(text).replace(/\{(\w+)\}/g, (all, name) => (values && name in values ? values[name] : all)),
  // the timeframe ladder debounces its fetch; here it is run inline, because
  // what is under test is which series it picks and not how long it waits
  setTimeout: (fn) => { fn(); return 0; }, clearTimeout() {},
  fetch: async () => ({ ok: true, json: async () => (
    { instruments: [], strategies: ['AG01'], params: {}, equity: 100000 }) }),
};
vm.createContext(sandbox);
vm.runInContext(shared, sandbox);
vm.runInContext(src, sandbox);
const run = (code) => vm.runInContext(code, sandbox);

/*
 * Two trades that overlap and close in the opposite order to the one they
 * opened in, and a third still open. That is the case the curve is read off
 * the trades for rather than off report.equity, which is in entry order and
 * would draw a balance the account never held.
 */
run(`state.data = { balance: 1000, candles: new Array(10).fill([0,1,1,1,1,1,1,1,1]),
  trades: [
    { n:1, exitIndex: 8, exitTime: 800, balance: 1002, pl: 2 },
    { n:2, exitIndex: 4, exitTime: 400, balance: 1003, pl: 3 },
    { n:3, exitIndex: null, exitTime: null, balance: null, pl: null },
  ] };`);

// compared as text: an array built inside the vm context has that context's
// own Array prototype, so deepStrictEqual calls two identical lists different
assert.strictEqual(JSON.stringify(run('equityPoints()')),
  '[[0,1000],[4,1003],[8,1002]]',
  'the curve follows the closes, opens at the starting balance, and leaves a '
  + 'trade that never closed off it');

// a label has to tell its own gridlines apart: P&L is price x units, so a
// whole run can move in the fourth decimal of the balance
assert.strictEqual(run('amountDecimals(0.05)'), 4);
assert.strictEqual(run('amountDecimals(100)'), 2);
assert.strictEqual(run('amountDecimals(0)'), 2);

run('drawEquity()');
assert.ok(calls.includes('stroke') && calls.includes('fillText'),
  'the curve and its axis were drawn');

run('state.data.trades = []; drawEquity()');
assert.strictEqual(run('$("equity-panel").hidden'), true,
  'a run that closed nothing hides the panel instead of drawing a flat line');

/*
 * The entry arrows. closePath() is drawn by nothing else on this canvas, so
 * counting it counts triangles. The rule is that every trade is marked on
 * every chart - a trade that is not drawn is a trade that cannot be clicked -
 * and that the room available changes the size of the mark, not whether it
 * is there.
 */
const arrows = () => {
  const before = calls.filter((c) => c === 'closePath').length;
  run('draw()');
  return calls.filter((c) => c === 'closePath').length - before;
};

const CANDLE = '[0,1,1.1,0.9,1,1.1,0.9,1.1,0.9]';
const TRADES = `[
  { n:1, direction:'long',  entryIndex: 1, exitIndex: 2, entryPrice: 1, exitPrice: 1,
    stopLoss: 0.9, takeProfit: null, stopFinal: null, exitTime: 1, balance: 1001, pl: 1 },
  { n:2, direction:'short', entryIndex: 3, exitIndex: 4, entryPrice: 1, exitPrice: 1,
    stopLoss: 1.1, takeProfit: null, stopFinal: null, exitTime: 2, balance: 1002, pl: 1 }]`;

run(`state.data = { balance: 1000, risk: 0.01,
  candles: new Array(6).fill(${CANDLE}), trades: ${TRADES} };
  state.view = null; state.selected = null;`);
assert.strictEqual(arrows(), 2, 'wide bars: one arrow per trade');

// the same two trades on a chart with far more bars than pixels
run(`state.data.candles = new Array(4000).fill(${CANDLE});`);
assert.strictEqual(arrows(), 2, 'bars too narrow: still one mark per trade');

// Was: 3 and then 2, when the selection's entry was a dot. Now: one more
// each, since that entry is a triangle up or down like the arrows (the
// markers of web/DESIGN.md), drawn with or without an exit
run('state.selected = 1;');
assert.strictEqual(arrows(), 4,
  'a selection does not hide the other trades, and adds its entry and the '
  + 'arrow that runs from its entry to its exit');

run('state.data.trades[1].exitPrice = null;');
assert.strictEqual(arrows(), 3, 'a trade with no exit has nowhere to point');
run('state.data.trades[1].exitPrice = 1;');

/*
 * Clicking the chart goes to the nearest entry, wherever it landed. Six bars
 * over 900px is 136px a bar, with the two entries at x=271 (bar 1) and x=544
 * (bar 3); a click at 500 is bar 3.2, so it is the second trade even though
 * it is 44px away from its mark. Missing by a few pixels used to do nothing
 * at all, which reads as a page that ignored the click.
 */
run(`state.data.candles = new Array(6).fill(${CANDLE});
     state.view = null; state.selected = null;`);
const click = (x) => { nodes['chart'].on.click({ clientX: x }); return run('state.selected'); };
assert.strictEqual(click(271), 0, 'a click on the first trade\'s entry selects it');
assert.strictEqual(click(500), 1, 'a click nearer the second entry goes there');
// Was: click(0), off the left edge. Now x=0 is on the price axis, where a
// click is a stretch or half a double click that fits the prices, and picks
// nothing; just inside the plot is still left of every entry
assert.strictEqual(click(0), 1, 'a click on the price axis leaves the selection alone');
assert.strictEqual(click(67), 0, 'and one off the left edge of the plot goes to the first');
assert.strictEqual(click(544), 1, 'and one on the second entry selects that');

/*
 * Picking a trade writes its figures over the chart and leaves the page where
 * it is. It used to scroll the table's row into view, which took the chart
 * that had just zoomed onto the trade off the screen.
 */
// Was: the line's text. Now a card built of nodes this sandbox does not keep,
// so what it says is read off tradeCard(), which it is drawn from
const line = run('JSON.stringify(tradeCard(state.data.trades[state.selected]))');
assert.ok(line.includes('#2') && line.includes('"target"') && line.includes('"stop"')
          && line.includes('P&L'),
  'the chart carries what the table row carries');
assert.strictEqual(run('$("chart-trade").hidden'), false);
nodes['reset'].on.click();
assert.strictEqual(run('$("chart-trade").hidden'), true,
  'and it goes when the selection does');

/*
 * And the capital curve picks trades by their close, which is the other end
 * of the same trade. Its axis is the whole run over 900px less a 92px left
 * margin and a 14px right one: six candles at 132px, so the trade that closed
 * on bar 2 is at x=423 and the one that closed on bar 4 at x=688.
 */
run('state.data.balance = 1000; state.selected = null;');
const equityClick = (x) => {
  nodes['equity'].on.click({ clientX: x });
  return run('state.selected');
};
assert.strictEqual(equityClick(423), 0, 'a click on the first step goes to it');
assert.strictEqual(equityClick(688), 1, 'and one on the second step to that');
assert.strictEqual(equityClick(880), 1,
  'a click past the last close stays on the trade that made it');

/*
 * The hovered bar says how far it travelled, in pips. The fixture's candle is
 * 1.1 high and 0.9 low with five decimals shown, so a pip is 0.0001 and the
 * bar is 2000 of them - a silly number for a silly fixture, and the point is
 * that it is (high - low) / pip and not a count of decimal places.
 */
/*
 * The timeframe under the zoom. The run is six four-hour bars; the finer
 * series is the same stretch drawn every twenty-five milliseconds of the
 * fixture's clock. What has to hold is that nothing moves: a trade is placed
 * by the instant it was filled at, so it lands on whichever bar covers that
 * instant, and the curves the payload carries per run-bar step across the
 * finer ones instead of being read at the wrong index.
 */
const FINE = [];
for (let t = 0; t <= 525; t += 25) FINE.push([t, 1, 1.1, 0.9, 1, 1.1, 0.9, 1.1, 0.9]);
run(`state.data = { instrument: 'X', granularity: 'H4', balance: 1000,
  candles: [0,100,200,300,400,500].map((t) => [t,1,1.1,0.9,1,1.1,0.9,1.1,0.9]),
  indicators: [{ label: 'SMA 2', values: [null, 1, 2, 3, 4, 5] }],
  slope: { period: 100, window: 20, atr: 14, trained: 6,
           values: [null, 0.5, -0.5, 0, 0.5, 0.5],
           thresholds: [{ percentile: 90, value: 0.2 }] },
  trades: [{ n:1, direction:'long', entryIndex: 2, exitIndex: 4, entryTime: 250,
             exitTime: 450, signalTime: 250, signalIndex: 2, entryPrice: 1,
             exitPrice: 1, stopLoss: 0.9, takeProfit: null, stopFinal: null,
             balance: 1001, pl: 1 }] };
  state.view = null; state.selected = null; state.series = null;`);

assert.strictEqual(run('tradeBar(state.data.trades[0], "entry")'), 2,
  'on the run\'s own bars a trade keeps the index the service worked out');

run(`state.series = { granularity: 'M5', candles: ${JSON.stringify(FINE)},
                     from: 0, to: 525 };`);
assert.strictEqual(run('tradeBar(state.data.trades[0], "entry")'), 10,
  'on a finer series it lands on the bar covering the instant it was filled');
assert.strictEqual(run('runIndex(10)'), 2,
  'and a drawn bar maps back to the run bar it happened inside');
assert.strictEqual(run('runIndex(0)'), 0);
assert.strictEqual(run('barAt(-1)'), null, 'an instant before the first bar is outside');

const marks = () => {
  const before = calls.filter((c) => c === 'closePath').length;
  run('draw()');
  return calls.filter((c) => c === 'closePath').length - before;
};
assert.strictEqual(marks(), 1, 'the trade is still marked once, on the finer bars');

run('state.slope = 1; draw();');
assert.ok(run('$("slope-note").textContent').includes('directional'),
  'the slope shading reads the run\'s own bars through the finer ones');

/*
 * Which series the zoom asks for. The whole run is six bars over 820px of
 * plot, which is wide but not absurd, so it stays where it is; five bars is
 * 164px a candle, which is a chart asking to be opened up.
 */
run(`state.instruments = [{ instrument: 'X', granularities: [
  { granularity: 'H4', bars: 100, from: 0, to: 100 * 14400000 },
  { granularity: 'M5', bars: 4800, from: 0, to: 100 * 14400000 }] }];
  state.data.granularity = 'H4'; state.series = null;
  state.data.candles = [];
  for (let i = 0; i < 100; i++) state.data.candles.push([i * 14400000,1,1.1,0.9,1,1.1,0.9,1.1,0.9]);`);
assert.strictEqual(run('autoPick([0, 99 * 14400000])'), 'H4',
  'the whole run is drawn on the bars the strategy read');
assert.strictEqual(run('autoPick([0, 4 * 14400000])'), 'M5',
  'zoomed onto five bars, the same window wants the finest series that fills it');
run('state.data.candles = [0,100,200,300,400,500].map((t) => [t,1,1.1,0.9,1,1.1,0.9,1.1,0.9]);'
    + 'state.instruments = []; state.series = null; state.selected = null; state.view = null;');

/*
 * The line the page shows while a long run is running. A backtest over eleven
 * years is minutes of waiting, and "running the backtest" for all of them is
 * indistinguishable from a page that has hung.
 */
const LINE = run(`progressLine({ running: true, instrument: 'EUR_USD',
  granularity: 'H4', strategy: 'H401', bars: 5000, total: 18878,
  at: 1709251200000, balance: 104320.5 })`);
assert.ok(LINE.includes('26%') && LINE.includes('18878')
          && LINE.includes('104320.50') && LINE.includes('2024-03-01'),
  'where it is, how far along, and what the account is worth there');
const TRADED = run(`progressLine({ running: true, instrument: 'EUR_USD',
  granularity: 'H4', strategy: 'H401', bars: 5000, total: 18878,
  at: 1709251200000, balance: 104320.5, trades: 137, won: 59, lost: 78 })`);
assert.ok(TRADED.includes('137 trades') && TRADED.includes('59 won')
          && TRADED.includes('78 lost'),
  'and how the trades closed so far went');
const READING = run(`progressLine({ running: true, loading: true,
  instrument: 'EUR_USD', granularity: 'H4', strategy: 'H401',
  stage: 'reading EUR_USD M5', read: 394000, toRead: 876253,
  total: 18878, bars: 0, at: null, balance: null })`);
assert.ok(READING.includes('reading EUR_USD M5') && READING.includes('44%')
          && READING.includes('876253'),
  'the reading has a count of its own, and it is rows and not bars');

assert.ok(!run(`progressLine({ instrument: 'X', granularity: 'H4',
  strategy: 'S', bars: 0, total: 0, at: null, balance: null })`).includes('NaN'),
  'a run that has not reported yet says nothing rather than NaN%');

/* the calendar line on the settings page: what the file holds, or that there
   is none. The collecting happens in another tab, on the site's own page.
   settings.js is a page of its own, so it gets a context of its own */
const settings = vm.createContext({ console, Math, Number, String, Array, JSON, Date, Object,
  // its tabs ask the document for them, and the window for the address
  Promise, document: { ...sandbox.document, querySelectorAll: () => [] }, fetch: sandbox.fetch,
  setTimeout: sandbox.setTimeout, location: sandbox.location, window: { addEventListener: () => {} } });
vm.runInContext(fs.readFileSync(
  path.join(__dirname, '..', 'web', 'static', 'settings.js'), 'utf8'), settings);
const inSettings = (code) => vm.runInContext(code, settings);
assert.strictEqual(inSettings('calendarLine(null)'), 'no calendar imported');
assert.strictEqual(inSettings('calendarLine({ events: 0 })'), 'no calendar imported');
const CAL = inSettings(`calendarLine({ events: 24310, from: 1420761600000,
  to: 1789689600000, impacts: { low: 12000, high: 4000, medium: 8310 } })`);
assert.ok(CAL.includes('24,310 events') && CAL.includes('12000 low')
          && CAL.indexOf('12000 low') < CAL.indexOf('4000 high'),
  'how many, over what, and the impacts commonest first');

run('state.decimals = 5;');
nodes['chart'].on.mousemove({ clientX: 300, clientY: 100 });
assert.ok(run('$("chart-zoom").textContent').includes('H-L 2000.0 pips'),
  'the readout carries the range of the bar under the cursor');
assert.strictEqual(run('pipSize()'), 0.0001);
// Was: state.decimals, the closes' decimals. Now: state.quoted, the ask's and
// the bid's - a candle is their middle and has a decimal more than the
// instrument, which made every pip ten times too many
run('state.quoted = 1;');
assert.strictEqual(run('pipSize()'), 1, 'an index quoted to one decimal has '
  + 'a point for a pip');
run('state.quoted = 5;');
const MID = '[0, 1.171125, 1.17131, 1.170025, 1.171185, 1.17136, 1.17006, 1.17126, 1.16999]';
assert.deepStrictEqual([run(`decimalsOf([${MID}])`), run(`decimalsOf([${MID}], 5)`)], [6, 5],
  'the middle has six decimals, the ask five: the pip is read off the ask');

/*
 * The swing levels. Twenty flat bars with a single high at bar 7, which
 * SWING_BARS=5 confirms at bar 12: the line has to start there and not at the
 * swing, because until bar 12 printed nobody knew bar 7 was a high. Flat lows
 * are not swings - the test of a swing is strict - so exactly one level.
 *
 * Counted on the chart as the difference between drawing with the levels on
 * and with them off, because the grid and the candle wicks start lines too
 * and the recorder only knows that a line started, not who started it.
 */
const flat = '[0,1,1,1,1,1,1,1,1]';
const at = (h) => `[0,1,${h},1,1,${h},1,${h},1]`;
const series = (bars, trades) => `state.data = { balance: null, indicators: [],
  candles: [${bars}], trades: ${trades || '[]'} };
  state.view = null; state.selected = null;`;

run(series(Array.from({ length: 20 }, (_, i) => i === 7 ? at(1.5) : flat)));

const swings = () => { starts.length = 0; run('draw()'); return starts.slice(); };
const lines = () => {
  run('state.levels = false;');
  const bare = swings().length;
  run('state.levels = true;');
  const drawn = swings();
  return { count: drawn.length - bare, starts: drawn };
};

const one = lines();
assert.strictEqual(one.count, 1, 'one swing in the series, one line on the chart');
assert.ok(one.starts.includes(66 + 12.5 * (900 - 66 - 14) / 20 - 0.5),
  'and it starts at the bar that confirmed the swing, not at the swing');

/*
 * Two swings at the same price are one level, starting at the earlier of
 * them: three lines a third of a pip apart are one thick line that says
 * nothing about how often the level held.
 */
run(series(Array.from({ length: 40 }, (_, i) =>
  (i === 7 || i === 25) ? at(1.5) : flat)));
const merged = JSON.parse(JSON.stringify(run('levelsOf()')));
assert.strictEqual(merged.length, 1, 'the same price twice is one level');
assert.strictEqual(merged[0].at, 12, 'drawn from the first time it was confirmed');
assert.strictEqual(merged[0].swings, 2, 'and it knows it was made twice');
// 1% of the run's range, which here is 1.5 - 1. The level is drawn this wide
// because this is the width it is judged by: two swings inside it are one
// level, and a bar inside it has touched it.
assert.ok(Math.abs(merged[0].near - 0.005) < 1e-12,
  'the level carries the tolerance it was merged with');

/*
 * One level per band of the run's range, and inside a band the one price came
 * back to.
 *
 * The fixture is the case that made the rule: a crowd of swings down at 1.05
 * and 1.10, where the market spent its time, and one lone swing at 3.00. Rank
 * by touches alone and every line drawn is in the crowd, which on ten years
 * of EUR_USD left three quarters of the chart without one. So 3.00 has to
 * survive although nothing ever went back to it - and 1.10, which shares a
 * band with 1.05 and was never tested, has to lose to it.
 */
const crowd = Array.from({ length: 200 }, (_, i) => {
  if (i === 14) return at(1.10);            // a swing nothing came back to
  if (i === 25) return at(1.05);            // a swing that was tested
  if (i === 102) return at(3.00);           // the lone one, far above
  // revisits of 1.05, every other bar so that none of them is itself a swing
  if (i >= 150 && i <= 158 && i % 2 === 0) return at(1.05);
  return flat;
});
run(series(crowd));
const banded = JSON.parse(JSON.stringify(run('levelsOf()')));
assert.deepStrictEqual(banded.map((l) => l.price), [1.05, 3],
  'one per band, and the far one is drawn although it was never tested again');
assert.strictEqual(banded[0].touches, 6,
  'five revisits and the bar at 102, whose range runs straight through 1.05: '
  + 'a touch is price being at the level, not a bar closing on it');

/*
 * The box around the candles the entry rule read. Twenty bars, a signal on
 * bar 8 and a fill on bar 10: with SETUP_BARS=5 the box covers bars 4..8 and
 * stops at the signal, because the decision was made there and the fill came
 * later. Twenty bars over 900px is 41px a bar, so that is x=66+4*41 and
 * 5*41 wide.
 */
run(series(Array.from({ length: 20 }, () => flat), `[{ n:1, direction:'long',
  signalIndex: 8, entryIndex: 10, exitIndex: 12, entryPrice: 1, exitPrice: 1,
  stopLoss: null, takeProfit: null, stopFinal: null, exitTime: 1,
  balance: null, pl: 1 }]`) + ' state.data.setupBars = 5; state.selected = 0;');
// Was: every strokeRect of draw(). Now draw() draws the navigator under the
// chart too (drawNav), whose box around the stretch on show is the one at y=1.5
const boxes = () => { rects.length = 0; run('draw()'); return rects.filter((r) => r[1] !== 1.5); };

const box = boxes();
assert.strictEqual(box.length, 1, 'the selected trade gets one box');
assert.strictEqual(box[0][0], 230.5, 'starting five bars before the signal');
assert.strictEqual(box[0][2], 205, 'and ending on the signal bar, not on the fill');

run('state.data.setupBars = null;');
assert.strictEqual(boxes().length, 0, 'a strategy that does not say gets no box');
run('state.data.setupBars = 5; state.data.trades[0].signalIndex = null;');
assert.strictEqual(boxes().length, 0, 'nor does a trade with no signal bar');

/*
 * The wheel. 200 bars zoomed in one notch is 160, and the bar under the
 * pointer keeps its share of the window - at x=500 that is bar 105 of 200,
 * which puts the left edge at 21.
 */
run(series(Array.from({ length: 200 }, () => flat)));
const wheel = (clientX, deltaY) => {
  nodes['chart'].on.wheel({ clientX, deltaY, preventDefault() {} });
  return JSON.stringify(run('state.view'));
};
assert.strictEqual(wheel(500, -1), '{"from":21,"to":180}', 'a notch in zooms on the pointer');
assert.strictEqual(wheel(500, 1), 'null', 'and a notch out is back to the whole range');

/*
 * The keys, the box, the ranges and the measure (chartKey and measured in
 * menu.js; keyed, boxZoom, showTimes and measureOf here), on 200 daily bars
 * whose highs are 2 and lows 0 - a price range of -0.12..2.12 once padded.
 * Shift and an arrow move a quarter of the window, + and - zoom about its
 * middle, End and Home go to either end of the run with the same span.
 */
run(series(Array.from({ length: 200 }, (_, i) => `[${i * 864e5},1,2,0,1,1,2,0,1]`)));
const shown = () => JSON.stringify(run('state.view && [state.view.from, state.view.to]'));
const press = (key, shiftKey) => run(`keyed(chartKey({ key: '${key}', shiftKey: ${!!shiftKey}, target: {} }))`);
run('zoomTo(100, 40);');
press('ArrowLeft', true);
assert.strictEqual(shown(), '[90,129]', 'shift and left: a quarter of the window back');
press('ArrowRight', true);
assert.strictEqual(shown(), '[100,139]', 'and right: a quarter on');
press('-');
assert.strictEqual(shown(), '[95,144]', 'minus: a notch out, about the middle');
press('+');
assert.strictEqual(shown(), '[100,139]', 'plus: a notch in');
press('End');
assert.strictEqual(shown(), '[160,199]', 'End: the last bars, the same span');
press('Home');
assert.strictEqual(shown(), '[0,39]', 'Home: the first ones');
assert.strictEqual(run(`chartKey({ key: 'ArrowLeft', shiftKey: false, target: {} })`), null,
  'an arrow on its own is not the chart\'s: it walks the runs and the trades');

// a box from the left edge of bar 50 to that of bar 100 - bars 50..99 - and
// the top half of the plot: 820px of plot is 4.1px a bar, 382px of it high, so the prices are 2.12 down to the middle, 1.00
run('state.view = null; draw(); pan = {};');
run(`boxZoom({ x0: ${66 + 50 * 4.1}, y0: 12, x1: ${66 + 100 * 4.1}, y1: ${12 + 382 / 2} }); pan = null;`);
assert.strictEqual(shown(), '[50,99]', 'a box zooms onto its bars');
assert.deepStrictEqual(JSON.parse(JSON.stringify(run('[state.scale.high, state.scale.low]'))).map((v) => +v.toFixed(2)),
  [2.12, 1], 'and onto its prices, set by hand');
run(`pan = {}; boxZoom({ x0: 300, y0: 100, x1: 304, y1: 150 }); pan = null;`);
assert.strictEqual(shown(), '[50,99]', 'one too narrow to have been meant does nothing');

// 1M: thirty days ending where the chart does, bar 99 - so from bar 69
nodes['ranges'].on.click({ target: { closest: () => ({ dataset: { range: '1M' } }) } });
assert.strictEqual(shown(), '[69,99]', 'a range ends where the chart does');

run('state.quoted = 5;');
assert.strictEqual(run(`measureOf({ a: { ms: 0, price: 1 }, b: { ms: 10 * 864e5, price: 1.001 } })`),
  '+10.0 pips · +0.10% · 10 bars · 10d', 'a measure in pips, percent, bars and time');

/*
 * Under the chart: the calendar's events counted and named, and the selected
 * trade's second line (tradeMore) - its id, the bars it was held, the pips it
 * moved (a short from 1.1000 to 1.0990 is ten up) and the events while it
 * was open. Ten hourly bars, a release at 3:30 and one after the trade.
 */
run(series(Array.from({ length: 10 }, (_, i) => `[${i * 36e5},1,2,0,1,1,2,0,1]`), `[{ n: 1,
  key: 'S 1:EUR_USD:H1:1', direction: 'short', units: 1, signalTime: 36e5, entryTime: 72e5,
  entryPrice: 1.1, exitTime: 18e6, exitPrice: 1.099, entryIndex: 2, exitIndex: 5, signalIndex: 1,
  orderPrice: 1.1, stopLoss: 1.11, stopFinal: null, takeProfit: 1.09,
  outcome: 'TAKE_PROFIT_ORDER', pl: 1, balance: 1001 }]`));
run(`state.quoted = 5; state.data.granularity = 'H1'; state.events = [
  [126e5, 'USD', 'high', 'Payrolls', 256, 180, 150, 'K'], [324e5, 'EUR', 'medium', 'Later', null, null, null, null]];`);
run('draw()');   // the range the card measures "off the chart" against: 0..2 padded
const card = JSON.parse(JSON.stringify(run('tradeCard(state.data.trades[0])')));
const said = JSON.stringify(card);
assert.ok(said.includes('"S 1:EUR_USD:H1:1"') && said.includes('3 bars · 3h') && said.includes('+10.0 pips')
  && said.includes('USD high Payrolls 256K (forecast 180K)') && !said.includes('Later'),
  'the trade\'s id, bars, pips and only the events while it was open: ' + said);
assert.deepStrictEqual(card.head.slice(1, 3), [['short', 'side short'], ['target', 'outcome tp']],
  'its side and its outcome in the table\'s colours');
assert.deepStrictEqual(card.facts.find((f) => f[0] === 'target'), ['target', '1.09000', 'tp'],
  'a target inside the chart is only its price, in the target\'s colour');
run('state.data.trades[0].takeProfit = 5; draw();');
assert.strictEqual(JSON.parse(JSON.stringify(run('tradeCard(state.data.trades[0])'))).facts
  .find((f) => f[0] === 'target')[1], '5.00000 · off the chart',
  'and one above the candles is said to be off the chart, which fits the candles');
run('state.data.trades[0].takeProfit = 1.09;');

/*
 * The drawdown (drawdowns) and the heatmap's cells (heatCells). A capital of
 * 100, 110, 99, 105, 111: the worst is 99 under 110, -10%, from the second
 * close to the third, and the fifth is the one back over 110.
 */
const dd = JSON.parse(JSON.stringify(run('drawdowns([[0, 100], [3, 110], [5, 99], [7, 105], [9, 111]])')));
assert.strictEqual(dd.points.map((q) => +q[1].toFixed(2)).join(), '0,0,-10,-4.55,0',
  'each close in per cent under the best so far');
assert.deepStrictEqual([dd.worst.from, dd.worst.at, dd.worst.back], [1, 2, 4],
  'the worst: from its best, at its bottom, back over it');
assert.strictEqual(run('drawdowns([[0, 100], [1, 90]]).worst.back'), null, 'and none back is said to be none');

// Monday 21 September 2026 09:15 and 09:40, Tuesday 10:00, and one still open
const HEAT_TRADES = `[
  { entryTime: Date.UTC(2026, 8, 21, 9, 15), exitTime: Date.UTC(2026, 8, 21, 11), pl: 5 },
  { entryTime: Date.UTC(2026, 8, 21, 9, 40), exitTime: Date.UTC(2026, 8, 22, 1), pl: -2 },
  { entryTime: Date.UTC(2026, 8, 22, 10), exitTime: Date.UTC(2026, 8, 22, 12), pl: 1 },
  { entryTime: Date.UTC(2026, 8, 22, 10), exitTime: null, pl: null }]`;
const heat = (layout, when) => JSON.parse(JSON.stringify(run(`heatCells(${HEAT_TRADES}, '${layout}', '${when}')`)));
const week = heat('week', 'entry');
assert.deepStrictEqual([week.trades, week.xs.length, week.ys.length], [3, 7, 24], 'the closed trades, a week of hours');
assert.deepStrictEqual(week.cells[0][9], { n: 2, ok: 1, ko: 1, pl: 3 }, 'Monday 09:00 holds both of its entries');
assert.deepStrictEqual(week.cells[1][10], { n: 1, ok: 1, ko: 0, pl: 1 }, 'Tuesday 10:00 the third');
assert.strictEqual(heat('week', 'exit').cells[1][1].n, 1, 'by exit, the loser is Tuesday 01:00');
assert.strictEqual(heat('month', 'entry').cells[8][20].n, 2, 'September the 21st');
const year = heat('year', 'entry');
assert.deepStrictEqual([year.xs, year.cells[0][8].n], [['2026'], 3], 'the years there are, against their months');
assert.strictEqual(JSON.stringify(run('eventsOfBar(3).map((e) => e[3])')), '["Payrolls"]',
  'the release at 3:30 is in the bar of 3:00');
run('draw()');
// with the payrolls on Monday at 10:00 and a CPI on Tuesday at 14:00
const HEAT_EVENTS = `[[Date.UTC(2026, 8, 21, 10), 'USD', 'high', 'Payrolls'], [Date.UTC(2026, 8, 22, 14), 'EUR', 'medium', 'CPI']]`;
const heatNews = (layout, news) => JSON.parse(JSON.stringify(
  run(`heatCells(${HEAT_TRADES}, '${layout}', 'entry', ${HEAT_EVENTS}, '${news}')`)));
assert.deepStrictEqual([heatNews('week', 'near').trades, heatNews('week', 'away').trades], [2, 1],
  "the two entered in the hour before the payrolls are near an event, Tuesday's is not");
const marked = heatNews('week', 'all').marks;
assert.deepStrictEqual([marked[0][10].length, marked[1][14].length, marked[0][9].length], [1, 1, 0],
  'each event marks the cell of its own hour');
const around = heatNews('event', 'all');
assert.deepStrictEqual([around.ys, around.trades, around.xs[1], around.cells[1][0].n, around.cells[2][0].n],
  [['USD Payrolls'], 2, '−1h…−30m', 1, 1],
  'by event: 45 minutes before the payrolls and 20 before, and the CPI four hours off is no row');
assert.strictEqual(run(`heatEvents(${HEAT_EVENTS})`), '2 events: USD Payrolls, EUR CPI');
assert.strictEqual(run('$("events-note").textContent'), '2 on show · 2 in the run (1 high, 1 medium)');

/*
 * How a trade ended. A closed trade must never be reported as running: the
 * page used to say "still open" for any outcome it had no name for, which on
 * a strategy with its own vocabulary was almost all of them.
 */
const outcome = (t) => JSON.parse(JSON.stringify(
  vm.runInContext(`outcomeCell(${JSON.stringify(t)})`, sandbox)));
assert.deepStrictEqual(outcome({ exitTime: null, outcome: 'STILL_OPEN' }),
  ['open', 'open at end']);
assert.deepStrictEqual(outcome({ exitTime: 5, outcome: 'TAKE_PROFIT_ORDER' }),
  ['tp', 'target']);
assert.deepStrictEqual(outcome({ exitTime: 5, outcome: 'STEP_EXIT' }),
  ['other', 'step exit'], 'an outcome this page has no styling for keeps its name');
assert.deepStrictEqual(outcome({ exitTime: 5, outcome: 'OPPOSITE_SIGNAL' }),
  ['other', 'opposite signal']);

/*
 * The strip for the curves that are not on the price axis. An ATR belongs
 * there and an SMA does not, and the page is told which is which by the
 * payload rather than deciding from the size of the numbers - so the check is
 * that the flag, and only the flag, moves a curve off the chart.
 */
run(series(Array.from({ length: 20 }, () => flat)));
run(`$("curve-box").open = true;
  state.data.indicators = [
    { kind: 'sma', label: 'SMA 3', values: new Array(20).fill(1) },
    { kind: 'atr', label: 'ATR 14', panel: true,
      values: new Array(20).fill(null).map((_, i) => i < 5 ? null : 0.004) }];
  draw();`);
assert.strictEqual(run('$("curve-panel").hidden'), false,
  'a declared ATR opens the strip below the chart');
assert.strictEqual(run('panelSeries().length'), 1, 'and only the ATR is in it');
assert.strictEqual(run('indicatorSeries().length'), 1,
  'while the SMA stays on the price axis');
assert.ok(run('$("curve-note").textContent').includes('ATR 14'),
  'the strip names what is in it');

// the price scale must not have seen the ATR: 0.004 next to a price of 1
// would put the whole candle range in the top tenth of the plot
const withAtr = run('scales(visible(), []).low');
run(`state.data.indicators.pop(); draw();`);
assert.strictEqual(run('scales(visible(), []).low'), withAtr,
  'an ATR on its own axis leaves the price scale exactly where it was');

assert.strictEqual(run('$("curve-panel").hidden'), true,
  'a strategy that declares no such curve gets no strip');

// AB states no starting balance: the curve starts from the first close
run(`state.data = { balance: null, candles: new Array(10).fill([0,1,1,1,1,1,1,1,1]),
  trades: [{ n:1, exitIndex: 4, exitTime: 400, balance: 1003, pl: 3 },
           { n:2, exitIndex: 8, exitTime: 800, balance: 1001, pl: -2 }] };`);
assert.strictEqual(JSON.stringify(run('equityPoints()')),
  '[[0,1000],[4,1003],[8,1001]]', 'a run with no stated capital still has a curve');

// 250 trades are three pages, and walking the trades turns them
run(`state.data.trades = Array.from({ length: 250 }, (_, i) => ({ n: i + 1,
  direction: 'long', entryIndex: 0, exitIndex: 1, entryTime: 0, exitTime: 1,
  pl: 1, balance: 1000 + i })); state.selected = null; state.page = 0; renderTrades();`);
assert.ok(run('$("page-info").textContent').includes('1\u2013100 of 250'));
run('select(230)');
assert.strictEqual(run('state.page'), 2, 'selecting a trade opens its page');
run('$("trade-next").on.click()');
assert.strictEqual(run('state.selected'), 231, 'next goes to the next trade');
run('$("trade-prev").on.click(); $("trade-prev").on.click()');
assert.strictEqual(run('state.selected'), 229, 'prev goes back');

// how long a trade was held, in the run's bars, and the report's min, avg, max
assert.strictEqual(run('barsHeld({ entryIndex: 3, exitIndex: 7 })'), 4, 'entry bar to exit bar');
assert.strictEqual(run('barsHeld({ entryIndex: 3, exitIndex: null })'), null, 'none while open');
run(`state.data.trades = [{ entryIndex: 0, exitIndex: 0 }, { entryIndex: 2, exitIndex: 7 },
  { entryIndex: 9, exitIndex: null }]; stat = (label, value) => label + ' ' + value;`);
assert.strictEqual(run('heldStats().join(", ")'), 'bars min 0, bars avg 2.5, bars max 5',
  'the open trade is left out');
run('state.data.trades = []');
assert.strictEqual(run('heldStats().join(", ")'), 'bars min n/a, bars avg n/a, bars max n/a');

/* the parameter analysis of the simulate page (paramEffects in sim.js): a
   page of its own, so a context of its own, with menu.js's shared globals.
   A 2x2x2 grid where slScale=2 adds 20 to the score and grows the capital in
   every quarter, inverse=1 adds 5, and tpScale changes nothing */
const simPage = vm.createContext({ ...sandbox });
vm.runInContext(shared, simPage);
// Was: sim.js alone. Now: analysis.js first, the KPIs in words it shares with the mix page
vm.runInContext(fs.readFileSync(
  path.join(__dirname, '..', 'web', 'static', 'analysis.js'), 'utf8'), simPage);
vm.runInContext(fs.readFileSync(
  path.join(__dirname, '..', 'web', 'static', 'sim.js'), 'utf8'), simPage);
const inSim = (code) => vm.runInContext(code, simPage);
// a run out of margin is not a candidate, whatever its KPIs say
assert.match(inSim(`analyse({ kpi: { roi: 30, car: 20, profitFactor: 2.5, expectancy: 5, winRate: 0.5,
  maxDrawdownPct: 5, riskReward: 2, sharpe: 2.5, carMdd: 4, ulcer: 2 },
  margin: { ok: false, leverage: 30, peakMarginPct: 140, minFree: -5, breachAt: 0, negativeAt: null } }).verdict`),
  /out of margin/);
assert.match(inSim(`marginText({ ok: true, leverage: 30, peakMargin: 1000, peakMarginPct: 1, minFree: 90000,
  maxOpen: 2, breachAt: null, negativeAt: null })`), /always room/);
inSim(`var T0 = Date.UTC(2020, 0, 1), T1 = Date.UTC(2020, 11, 31, 23, 59, 59), Q = (T1 - T0) / 4;
  var grid = [], n = 0;
  for (const sl of ['1', '2']) for (const tp of ['1', '2']) for (const inv of ['0', '1']) {
    const up = sl === '2';
    grid.push({ n: ++n, params: { slScale: sl, tpScale: tp, inverse: inv }, balance: 100,
      final: up ? 140 : 100, kpi: { score: 40 + (up ? 20 : 0) + (inv === '1' ? 5 : 0),
      maxDrawdownPct: up ? 20 : 30 },
      curve: up ? [1, 2, 3, 4].map((k) => [T0 + Q * k - 1000, 100 + 10 * k]) : [] });
  }
  grid.push({ n: 99, params: { slScale: '2', tpScale: '1', inverse: '0' }, error: 'boom' });
  var effects = (key, low) => paramEffects(grid, ['slScale', 'tpScale', 'inverse'],
    (r) => (r.kpi ? r.kpi[key] ?? null : null), low, [T0, T1]);`);
const EFFECTS = JSON.parse(inSim(`JSON.stringify(effects('score', false).map((e) => [e.param,
  e.best.value, e.worst.value, e.effect, e.consistency, e.edge, +e.eta2.toFixed(3),
  e.best.slicesWon, e.slices, e.best.runs, e.verdict]))`));
assert.deepStrictEqual(EFFECTS[0], ['slScale', '2', '1', 20, 1, 20, 0.941, 4, 4, 4, 'decides'],
  'slScale moves the score, in every context and every quarter; the run in error is left out');
assert.deepStrictEqual(EFFECTS[1].slice(0, 5).concat(EFFECTS[1][10]),
  ['inverse', '1', '0', 5, 1, 'middling'], 'inverse wins every context but explains little');
assert.deepStrictEqual([EFFECTS[2][0], EFFECTS[2][3], EFFECTS[2][6], EFFECTS[2][7], EFFECTS[2][10]],
  ['tpScale', 0, 0, 2, 'matters little'], 'tpScale changes nothing, and a tied quarter is shared');
const LOW = JSON.parse(inSim(`JSON.stringify(effects('maxDrawdownPct', true)[0])`));
assert.deepStrictEqual([LOW.param, LOW.best.value, LOW.best.median, LOW.effect],
  ['slScale', '2', 20, 10], 'a drawdown is better low: the smaller one is the best value');
assert.strictEqual(inSim(`paramEffects(grid, ['slScale'], (r) => null, false, null)[0].best`), null,
  'no run with the figure, no best value');

console.log('app.js: loaded, curve drawn, arrows, clicks, swings, panels and '
  + 'outcomes checked, %d canvas calls', calls.length);
