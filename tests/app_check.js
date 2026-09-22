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
const ctx2d = new Proxy({}, { get: (t, k) => {
  if (k === 'measureText') return () => ({ width: 40 });
  return (...a) => calls.push(k);
} });

const element = () => new Proxy({
  getContext: () => ctx2d, addEventListener() {}, appendChild() {}, append() {},
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

console.log('app.js: loaded, curve drawn, %d canvas calls', calls.length);
