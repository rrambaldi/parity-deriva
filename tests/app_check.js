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
              addEventListener() {} },
  window: { devicePixelRatio: 2, addEventListener() {} },
  history: { replaceState() {} }, location: { search: '' },
  // the timeframe ladder debounces its fetch; here it is run inline, because
  // what is under test is which series it picks and not how long it waits
  setTimeout: (fn) => { fn(); return 0; }, clearTimeout() {},
  fetch: async () => ({ ok: true, json: async () => (
    { instruments: [], strategies: ['AG01'], params: {}, equity: 100000 }) }),
};
vm.createContext(sandbox);
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

run('state.selected = 1;');
assert.strictEqual(arrows(), 3,
  'a selection does not hide the other trades, and adds the arrow that runs '
  + 'from its entry to its exit');

run('state.data.trades[1].exitPrice = null;');
assert.strictEqual(arrows(), 2, 'a trade with no exit has nowhere to point');
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
assert.strictEqual(click(0), 0, 'and one off the left edge goes to the first');
assert.strictEqual(click(544), 1, 'and one on the second entry selects that');

/*
 * Picking a trade writes its figures over the chart and leaves the page where
 * it is. It used to scroll the table's row into view, which took the chart
 * that had just zoomed onto the trade off the screen.
 */
const line = run('$("chart-trade").textContent');
assert.ok(line.includes('#2') && line.includes('target') && line.includes('stop')
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

/*
 * The warning before a long run. It is asked in bars the simulator walks, not
 * in candles the chart draws, because that is what the wait is made of.
 */
const ASK = run(`estimateQuestion({ instrument: 'EUR_USD', granularity: 'H4',
  fine: 'M5', bars: 18873, ticks: 894875, seconds: 596.6, rate: 1500 })`);
assert.ok(ASK.includes('894,875') && ASK.includes('M5') && ASK.includes('10 minutes'),
  'how many bars, which fine series, and how long that is');
assert.strictEqual(run('howLong(45)'), '45 seconds');
assert.strictEqual(run('howLong(600)'), '10 minutes');
assert.strictEqual(run('howLong(7200)'), '2.0 hours');

/* the calendar line in the data dialog: what the file holds, or that there
   is none. The collecting happens in another tab, on the site's own page. */
assert.strictEqual(run('calendarLine(null)'), 'no calendar imported');
assert.strictEqual(run('calendarLine({ events: 0 })'), 'no calendar imported');
const CAL = run(`calendarLine({ events: 24310, from: 1420761600000,
  to: 1789689600000, impacts: { low: 12000, high: 4000, medium: 8310 } })`);
assert.ok(CAL.includes('24,310 events') && CAL.includes('12000 low')
          && CAL.indexOf('12000 low') < CAL.indexOf('4000 high'),
  'how many, over what, and the impacts commonest first');

run('state.decimals = 5;');
nodes['chart'].on.mousemove({ clientX: 300, clientY: 100 });
assert.ok(run('$("chart-zoom").textContent').includes('H-L 2000.0 pips'),
  'the readout carries the range of the bar under the cursor');
assert.strictEqual(run('pipSize()'), 0.0001);
run('state.decimals = 1;');
assert.strictEqual(run('pipSize()'), 1, 'an index quoted to one decimal has '
  + 'a point for a pip');
run('state.decimals = 5;');

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
const boxes = () => { rects.length = 0; run('draw()'); return rects.slice(); };

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

console.log('app.js: loaded, curve drawn, arrows, clicks, swings, panels and '
  + 'outcomes checked, %d canvas calls', calls.length);
