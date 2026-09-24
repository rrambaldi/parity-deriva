/*
 * The sweep: one backtest per combination of the values typed in, their
 * capital curves on one chart, and a table of what each one added up to.
 *
 * The service does the work (web/service.py startSweep) - it expands the
 * grid, runs each combination as an ordinary backtest, and reports where it
 * has got to. This page asks every PROGRESS_MS and draws: the finished runs
 * as they arrive, and the one running now as its trades close.
 *
 * Times are epoch milliseconds for a naive UTC instant, formatted back with
 * the getUTC* accessors, as in app.js.
 */

const PROGRESS_MS = 600;
// the same threshold app.js warns at, times the number of runs
const TICK_WARNING = 25000;
const AXIS = { left: 92, right: 14, top: 12, bottom: 22 };
const RUN_KEY = 'parity-deriva.run';   // app.js's saved form, see saveRun()
// the columns after the parameters, as report fields
const STATS = [
  ['trades', (r) => r.report.closedTrades, String],
  ['won', (r) => r.report.wins, String],
  ['lost', (r) => r.report.losses, String],
  ['win rate', (r) => r.report.winRate, percent],
  ['net', (r) => r.report.net, amount],
  ['profit factor', (r) => r.report.profitFactor, (v) => v === null ? 'n/a' : v.toFixed(2)],
  ['avg win', (r) => r.report.averageWin, amount],
  ['avg loss', (r) => r.report.averageLoss, amount],
  ['max drawdown', (r) => r.report.maxDrawdown, amount],
  ['run of wins', (r) => r.report.maxConsecutiveWins, String],
  ['run of losses', (r) => r.report.maxConsecutiveLosses, String],
  ['capital', (r) => r.final, amount],
];

const state = {
  forms: {}, defaults: {}, instruments: [],
  rows: [],          // finished runs, as the service sent them
  job: null,         // the last status
  fields: null,      // the fixed fields the sweep was started with
  pick: null,        // the run under the cursor or clicked in the table
  pinned: null,      // the run clicked in the table
  sort: { col: 'n', dir: 1 },
  polling: false,
  favourites: [],    // the runs starred to trade live, from /api/favourites
};

const $ = (id) => document.getElementById(id);
const canvas = $('sim-equity');
const ctx = canvas.getContext('2d');

/* ------------------------------------------------------------ formatting */

function stamp(ms) {
  if (ms === null || ms === undefined) return '';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}
function percent(v) {
  return (v === null || v === undefined) ? 'n/a' : (v * 100).toFixed(1) + '%';
}
// a balance and a price-times-units P&L are both called an amount here: the
// first wants cents, the second the digits the move happened in
function amount(v) {
  if (v === null || v === undefined) return '';
  return Math.abs(v) >= 1 || v === 0 ? v.toFixed(2) : v.toPrecision(3);
}
function howLong(seconds) {
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`;
  return `${(seconds / 3600).toFixed(1)} hours`;
}
// a run's id: its set's and its number in it - the path the service keeps it at
function runId(n) {
  return state.job && state.job.id ? `${state.job.id}/${n}` : `#${n}`;
}

function paramsText(params) {
  return Object.entries(params || {})
    .map(([k, v]) => `${k}=${v === '' ? 'none' : v}`).join(' ');
}

/* ----------------------------------------------------------------- fetch */

async function ask(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

// the writes need this header; see do_POST in web/service.py
async function post(url, body) {
  const response = await fetch(url, { method: 'POST', body: JSON.stringify(body || {}),
                                      headers: { 'X-Parity-Deriva': '1' } });
  const payload = await response.json().catch(() => ({
    error: `${response.status} ${response.statusText}` }));
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function message(text, kind) {
  const box = $('message');
  box.hidden = !text;
  box.textContent = text || '';
  box.className = kind === 'info' ? 'info' : '';
}

// a yes or no asked on the page, as in app.js
function askUser(text, yes = 'run it anyway') {
  const dialog = $('ask-dialog');
  $('ask-text').textContent = text;
  $('ask-yes').textContent = yes;
  dialog.returnValue = '';
  dialog.showModal();
  $('ask-yes').focus();
  return new Promise((resolve) => dialog.addEventListener('close',
    () => resolve(dialog.returnValue === 'yes'), { once: true }));
}

/* ------------------------------------------------------------------ form */

function fill(select, values, label) {
  select.textContent = '';
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label ? label(value) : value;
    select.appendChild(option);
  }
}

function granularities() {
  const row = state.instruments.find((r) => r.instrument === $('instrument').value);
  return row ? row.granularities : [];
}

function onStrategy() {
  const box = $('grid-strategy');
  box.textContent = '';
  for (const field of state.forms[$('strategy').value] || []) {
    const label = document.createElement('label');
    label.textContent = field.label + ' ';
    const input = document.createElement('input');
    input.type = 'text';
    input.size = 10;
    input.dataset.name = field.name;
    input.value = String(field.value);
    // a menu's values are the ones worth trying, so they are the hint
    input.placeholder = field.choices ? field.choices.join(', ') : String(field.value);
    if (field.choices) input.title = 'accepts ' + field.choices.join(', ');
    label.appendChild(input);
    box.appendChild(label);
  }
  const wanted = state.defaults[$('strategy').value] || {};
  if (wanted.instrument && state.instruments.some((r) => r.instrument === wanted.instrument)) {
    $('instrument').value = wanted.instrument;
  }
  onInstrument();
}

function onInstrument() {
  const rows = granularities();
  fill($('granularity'), rows.map((r) => r.granularity));
  const wanted = (state.defaults[$('strategy').value] || {}).granularity;
  const pick = rows.find((r) => r.granularity === wanted) || rows.find((r) => r.granularity === 'H1');
  if (pick) $('granularity').value = pick.granularity;
  onGranularity();
}

function onGranularity() {
  const row = granularities().find((r) => r.granularity === $('granularity').value);
  if (!row) return;
  $('from').value = stamp(row.from);
  $('to').value = stamp(row.to);
}

function fixedFields() {
  return {
    strategy: $('strategy').value, instrument: $('instrument').value,
    granularity: $('granularity').value, from: $('from').value,
    to: $('to').value, risk: $('risk').value, balance: $('balance').value,
  };
}

function gridFields() {
  const grid = {};
  for (const input of $('grid-strategy').querySelectorAll('input')) {
    grid[input.dataset.name] = input.value;
  }
  for (const name of ['maxStop', 'session', 'intraday', 'maxBars', 'slScale', 'tpScale']) {
    grid[name] = $('g-' + name).value;
  }
  return grid;
}

// the main page's last form, when this tab has one: the sweep usually
// starts from the run just looked at
function fromMainPage() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(RUN_KEY) || 'null'); } catch (e) { /* none */ }
  if (saved && saved.fields) fillForm(saved.fields);
}

// the form from a set of fields: the main page's saved ones, or a sweep's
// fixed fields with its grid over them
function fillForm(f) {
  if (Array.from($('strategy').options).some((o) => o.value === f.strategy)) {
    $('strategy').value = f.strategy;
    onStrategy();
  }
  if (state.instruments.some((r) => r.instrument === f.instrument)) {
    $('instrument').value = f.instrument;
    onInstrument();
  }
  if (granularities().some((r) => r.granularity === f.granularity)) {
    $('granularity').value = f.granularity;
    onGranularity();
  }
  for (const id of ['from', 'to', 'risk', 'balance']) if (f[id]) $(id).value = f[id];
  for (const input of $('grid-strategy').querySelectorAll('input')) {
    if (f[input.dataset.name] !== undefined) input.value = f[input.dataset.name];
  }
  for (const name of ['maxStop', 'session', 'intraday', 'maxBars', 'slScale', 'tpScale']) {
    if (f[name]) $('g-' + name).value = f[name];
  }
}

let counting = null;
function countLater() {
  clearTimeout(counting);
  counting = setTimeout(async () => {
    try {
      const { combos } = await post('api/sweep', { dry: true, fields: fixedFields(), grid: gridFields() });
      $('combos').textContent = `${combos} run${combos === 1 ? '' : 's'}`;
    } catch (error) {
      $('combos').textContent = String(error.message || error);
    }
  }, 250);
}

/* ------------------------------------------------------------------- run */

async function simulate(event) {
  event.preventDefault();
  const fields = fixedFields();
  const grid = gridFields();
  let combos;
  try {
    ({ combos } = await post('api/sweep', { dry: true, fields, grid }));
  } catch (error) { message(String(error.message || error)); return; }
  try {
    const ahead = await ask('api/estimate?' + new URLSearchParams({
      instrument: fields.instrument, granularity: fields.granularity,
      from: fields.from, to: fields.to }));
    if (ahead.ticks * combos > TICK_WARNING) {
      const question = `${combos} runs, each walking ${ahead.ticks.toLocaleString()} bars`
        + (ahead.fine ? ` (${ahead.granularity} candles and the ${ahead.fine} bars under them)` : '')
        + `.\n\nAt the ${ahead.rate.toLocaleString()} bars a second the last run managed,`
        + ` that is about ${howLong(ahead.seconds * combos)} in all.\n\nRun it anyway?`;
      if (!await askUser(question)) return;
    }
  } catch (error) { /* no estimate, no warning, still a sweep */ }

  try {
    state.rows = [];
    state.pick = state.pinned = null;
    state.fields = fields;
    follow(await post('api/sweep', { fields, grid, name: $('sweep-name').value }));
  } catch (error) {
    message(String(error.message || error));
  }
}

/* One status, and the next poll if the sweep is still going. */
function follow(job) {
  state.job = job;
  if (job.since === 0) state.rows = [];
  state.rows.push(...(job.done || []));
  const running = job.running;
  $('run').disabled = running;
  $('stop').hidden = !running;
  $('rerun').hidden = running || !job.total;
  if (running) $('stop').disabled = !!job.cancel;
  if (job.error) message(job.error);
  else if (running) {
    // just the values that change from run to run, the rest is in the title
    const c = job.current;
    const params = c ? Object.fromEntries((job.varied || []).map((k) => [k, c.params[k]])) : {};
    const text = paramsText(params);
    // while this run reads its candles, how far it has got (backtest/ledger._reading)
    const p = job.progress;
    const loading = p && p.loading && p.toRead ? ` · candele ${Math.floor(100 * p.read / p.toRead)}%` : '';
    message(`run ${c ? c.n : state.rows.length} of ${job.total}${loading}` + (text ? ` · ${text}` : ''), 'info');
  }
  else if (job.total) message(job.cancel ? `stopped after ${state.rows.length} of ${job.total} runs` : '');
  const f = job.fields || {};
  $('sim-title').textContent = (job.id ? `[${job.id}] ` : '') + (job.name ? `${job.name} · ` : '')
    + `${f.strategy} on ${f.instrument} ${f.granularity}`
    + ` · ${f.from} .. ${f.to} · ${state.rows.length} of ${job.total} runs`;
  renderCurrent();
  renderTable();
  renderKpi();
  draw();
  if (running && !state.polling) {
    state.polling = true;
    setTimeout(async () => {
      state.polling = false;
      try { follow(await ask('api/sweep?since=' + state.rows.length)); }
      catch (error) { message(String(error.message || error)); }
    }, PROGRESS_MS);
  }
}

/* The set on show once more, as it was run - not as the form was edited
   since. Same path as simulate, warning included; saved as a new set. */
function rerun(job) {
  fillForm({ ...(job.fields || {}), ...(job.grid || {}) });
  $('sweep-name').value = (job.name ? job.name + ' · ' : '') + 'rerun';
  $('sim-controls').requestSubmit();
}
$('rerun').addEventListener('click', () => state.job && rerun(state.job));

$('stop').addEventListener('click', async () => {
  $('stop').disabled = true;
  try { await post('api/sweep/stop'); } catch (error) { message(String(error.message || error)); }
});

/* ------------------------------------------------------------- readouts */

function renderCurrent() {
  const box = $('sim-current');
  const job = state.job;
  const p = job && job.progress;
  box.hidden = !(job && job.current);
  if (box.hidden) return;
  const share = p && p.total ? ` · ${Math.min(100, 100 * p.bars / p.total).toFixed(0)}%` : '';
  const where = !p ? 'starting'
    : p.loading ? (p.stage || 'reading the candles')
    : `${stamp(p.at)}${share}`
      + (p.trades === undefined ? '' : ` · ${p.trades} trades, ${p.won} won, ${p.lost} lost`)
      + (p.balance === null || p.balance === undefined ? '' : ` · capital ${amount(p.balance)}`);
  box.textContent = `#${job.current.n} ${paramsText(job.current.params)} · ${where}`;
}

function columns() {
  const varied = (state.job && state.job.varied) || [];
  return [['#', 'n']].concat(varied.map((name) => [name, 'p:' + name]))
    .concat(STATS.map(([label]) => [label, 's:' + label]));
}

function sortValue(row, col) {
  if (col === 'n') return row.n;
  if (col.startsWith('p:')) {
    const v = row.params[col.slice(2)];
    return v === '' ? -Infinity : (isNaN(Number(v)) ? v : Number(v));
  }
  if (row.error) return -Infinity;
  const stat = STATS.find(([label]) => 's:' + label === col);
  const v = stat[1](row);
  return v === null || v === undefined ? -Infinity : v;
}

function best() {
  let top = null;
  for (const row of state.rows) {
    if (!row.error && (top === null || row.final > top.final)) top = row;
  }
  return top;
}

function renderTable() {
  const cols = columns();
  const head = $('sim-thead');
  head.textContent = '';
  const tr = document.createElement('tr');
  for (const [label, col] of cols) {
    const th = document.createElement('th');
    th.textContent = label + (state.sort.col === col ? (state.sort.dir > 0 ? ' ▲' : ' ▼') : '');
    th.dataset.col = col;
    if (col !== 'n' && !col.startsWith('p:')) th.className = 'num';
    tr.appendChild(th);
  }
  const open = document.createElement('th');
  tr.append(open, document.createElement('th'));
  head.appendChild(tr);

  const body = $('sim-rows');
  body.textContent = '';
  if (!state.rows.length) {
    const empty = document.createElement('tr');
    empty.className = 'empty';
    const td = document.createElement('td');
    td.colSpan = cols.length + 2;
    td.textContent = state.job && state.job.running ? 'the first run is going…'
      : 'press simulate to fill this table';
    empty.appendChild(td);
    body.appendChild(empty);
    return;
  }
  const top = best();
  const { col, dir } = state.sort;
  const rows = state.rows.slice().sort((a, b) => {
    const x = sortValue(a, col), y = sortValue(b, col);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || a.n - b.n;
  });
  for (const row of rows) {
    const line = document.createElement('tr');
    line.dataset.n = row.n;
    if (row.n === state.pick) line.className = 'selected';
    for (const [, c] of cols) {
      const td = document.createElement('td');
      if (c === 'n') td.textContent = row.n + (row === top ? ' ★' : '');
      else if (c.startsWith('p:')) {
        const v = row.params[c.slice(2)];
        td.textContent = v === '' ? 'none' : v;
      } else if (row.error) {
        td.textContent = c === 's:trades' ? row.error : '';
        td.className = 'bad';
      } else {
        const [label, get, format] = STATS.find(([l]) => 's:' + l === c);
        const v = get(row);
        td.textContent = format(v);
        td.className = 'num' + (label === 'net' ? (v > 0 ? ' good' : v < 0 ? ' bad' : '') : '');
      }
      line.appendChild(td);
    }
    line.append(actions(), starCell(row));
    body.appendChild(line);
  }
}

/*
 * A favourite is a form the live page trades; here, one run of a saved set,
 * matched by set and number. A set not saved yet has nothing on disk to point
 * at, so its rows get an empty cell and no star. The list is the service's
 * (web/service.py favourites) and is asked for again after each toggle.
 */
function favouriteOf(n) {
  const id = state.job && state.job.id;
  if (!id) return null;
  return state.favourites.find((f) => f.source.kind === 'sweep' && f.source.id === id
    && f.source.n === n) || null;
}

function starCell(row) {
  const td = document.createElement('td');
  if (row.error || !(state.job && state.job.id)) return td;
  const star = document.createElement('button');
  star.type = 'button';
  star.className = 'fav-star';
  star.dataset.favN = row.n;
  star.title = 'segna come preferita';
  const on = !!favouriteOf(row.n);
  star.textContent = on ? '★' : '☆';
  star.setAttribute('aria-pressed', String(on));
  td.appendChild(star);
  return td;
}

async function loadFavourites() {
  state.favourites = (await ask('api/favourites')).favourites || [];
  renderTable();
  renderKpi();
}

async function toggleFavourite(n) {
  const have = favouriteOf(n);
  if (have) await post(`api/favourites/${have.id}/delete`);
  else await post('api/favourites', { source: { kind: 'sweep', id: state.job.id, n } });
  await loadFavourites();
}

// the two ways out of a row; a click anywhere else on it only picks its curve
function actions(more = []) {
  const td = document.createElement('td');
  td.className = 'run-actions';
  for (const [name, title] of [['view', 'this run in full, here'],
                               ['backtest', 'this run on the backtest page'], ...more]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.action = name;
    button.textContent = name;
    button.title = title;
    td.append(button, ' ');
  }
  return td;
}

// a row clicked: its curve picked and held, or let go on a second click
function rowClick(event) {
  const line = event.target.closest('tr[data-n]');
  if (!line) return;
  const n = Number(line.dataset.n);
  // the star is on the row but is not the row: neither picked nor pinned
  if (event.target.classList.contains('fav-star')) {
    return toggleFavourite(n).catch((error) => message(String(error.message || error)));
  }
  const action = event.target.dataset.action;
  if (action === 'view') return openRun(n);
  if (action === 'backtest') return openBacktest(n);
  if (action === 'analisi') return openAnalysis(n);
  state.pinned = state.pinned === n ? null : n;
  state.pick = n;
  draw();
  renderTable();
  renderKpi();
}

$('sim-thead').addEventListener('click', (event) => {
  const col = event.target.dataset && event.target.dataset.col;
  if (!col) return;
  state.sort = { col, dir: state.sort.col === col ? -state.sort.dir : (col === 'n' ? 1 : -1) };
  renderTable();
});

$('sim-rows').addEventListener('click', rowClick);

// one run's fields, as the backtest page's form spells them
function runFields(n) {
  const row = state.rows.find((r) => r.n === n);
  const fields = { ...state.fields, ...row.params };
  if (fields.intraday === '0') delete fields.intraday;
  for (const key of Object.keys(fields)) if (fields[key] === '') delete fields[key];
  return fields;
}

// the run on the backtest page itself, with the page's saved form set to it
function openBacktest(n) {
  try {
    // sweep and run: see start() in app.js
    sessionStorage.setItem(RUN_KEY, JSON.stringify({
      fields: runFields(n), trade: null, sweep: state.job.id, run: n }));
  } catch (e) { /* none */ }
  location.href = './';
}

// the run in a full page dialog: the backtest page, results only
function openRun(n) {
  const row = state.rows.find((r) => r.n === n);
  if (!row || row.error) return;
  const dialog = $('run-dialog');
  dialog.dataset.n = n;
  $('run-title').textContent = `${runId(n)} ${paramsText(row.params)}`;
  // sweep and run: where the page finds it on disk once the service has
  // let its cache go, instead of running it again
  $('run-frame').src = './?' + new URLSearchParams({
    embed: '1', ...(state.job.id ? { sweep: state.job.id, run: n } : {}), ...runFields(n) });
  dialog.showModal();
}

$('run-backtest').addEventListener('click', () => openBacktest(Number($('run-dialog').dataset.n)));
// an emptied frame stops drawing, and a run reopened starts from its top
$('run-dialog').addEventListener('close', () => { $('run-frame').src = 'about:blank'; });

$('sim-rows').addEventListener('mouseover', (event) => {
  const line = event.target.closest('tr');
  if (!line || !line.dataset.n || state.pinned !== null) return;
  state.pick = Number(line.dataset.n);
  draw();
});

/* ----------------------------------------------------------------- chart */

// every run as [[ms, balance], ...] starting at the opening capital
function curveOf(row, from) {
  // a viewer plugin declares no opening capital: it is the last one less the net
  const start = row.balance !== null && row.balance !== undefined ? row.balance
    : row.final - ((row.report && row.report.net) || 0);
  // by the close, which is when the balance moves; the trades come by entry
  return [[from, start]].concat((row.curve || []).slice().sort((a, b) => a[0] - b[0]));
}

function liveCurve(from) {
  const job = state.job;
  const p = job && job.progress;
  if (!job || !job.current || !p || !p.curve) return null;
  const start = p.start || Number(state.fields && state.fields.balance)
    || (state.rows[0] && state.rows[0].balance);
  if (!start) return null;
  const points = [[from, start]].concat(p.curve);
  if (p.at && p.balance !== null && p.balance !== undefined) points.push([p.at, p.balance]);
  return points;
}

function fitCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = 340;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = height + 'px';
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

let geometry = null;   // what the last draw mapped, for the hover

function draw() {
  const { width, height } = fitCanvas();
  ctx.clearRect(0, 0, width, height);
  geometry = null;
  const fields = state.fields;
  if (!fields) return;
  const from = Date.parse(fields.from + 'T00:00:00Z');
  const to = Date.parse(fields.to + 'T23:59:59Z');
  const curves = state.rows.filter((r) => !r.error).map((r) => [r.n, curveOf(r, from)]);
  const live = liveCurve(from);
  const all = curves.map((c) => c[1]).concat(live ? [live] : []);
  if (!all.length) return;

  let low = Infinity, high = -Infinity;
  for (const points of all) for (const [, v] of points) {
    if (v < low) low = v;
    if (v > high) high = v;
  }
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.06;
  high += pad; low -= pad;
  const plotW = width - AXIS.left - AXIS.right;
  const plotH = height - AXIS.top - AXIS.bottom;
  const x = (ms) => AXIS.left + (ms - from) / Math.max(1, to - from) * plotW;
  const y = (v) => AXIS.top + (high - v) / (high - low) * plotH;
  geometry = { from, to, x, y, low, high, curves, plotW };

  // grid and the axes' labels, recessive
  ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#8b95a6';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    ctx.strokeStyle = '#232932';
    ctx.beginPath(); ctx.moveTo(AXIS.left, py); ctx.lineTo(width - AXIS.right, py); ctx.stroke();
    ctx.fillText(amount(v), AXIS.left - 6, py);
  }
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= 4; i++) {
    const ms = from + (to - from) * (i / 4);
    ctx.fillText(stamp(ms), Math.min(Math.max(x(ms), AXIS.left + 40), width - AXIS.right - 40),
                 height - AXIS.bottom + 6);
  }
  // the opening capital, which every curve starts from
  const start = all[0][0][1];
  ctx.strokeStyle = '#3a414d';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(AXIS.left, y(start)); ctx.lineTo(width - AXIS.right, y(start)); ctx.stroke();
  ctx.setLineDash([]);

  // a step: the balance moves when a trade closes and not in between
  const line = (points, colour, widthPx, end) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = widthPx;
    ctx.beginPath();
    points.forEach(([ms, v], i) => {
      if (i === 0) ctx.moveTo(x(ms), y(v));
      else { ctx.lineTo(x(ms), y(points[i - 1][1])); ctx.lineTo(x(ms), y(v)); }
    });
    const last = points[points.length - 1];
    ctx.lineTo(x(end), y(last[1]));
    ctx.stroke();
  };
  for (const [n, points] of curves) {
    if (n !== state.pick) line(points, 'rgba(139,149,166,.38)', 1, to);
  }
  if (live) line(live, '#e8a33d', 2, live[live.length - 1][0]);
  const picked = curves.find(([n]) => n === state.pick);
  if (picked) line(picked[1], '#58a6ff', 2, to);
}

// the curve nearest the cursor, at the cursor's time
function balanceAt(points, ms) {
  let v = points[0][1];
  for (const [t, b] of points) { if (t > ms) break; v = b; }
  return v;
}

canvas.addEventListener('mousemove', (event) => {
  if (!geometry || !geometry.curves.length) return;
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const { from, to, plotW, y } = geometry;
  const ms = from + (px - AXIS.left) / plotW * (to - from);
  let nearest = null, gap = Infinity;
  for (const [n, points] of geometry.curves) {
    const d = Math.abs(y(balanceAt(points, ms)) - py);
    if (d < gap) { gap = d; nearest = [n, balanceAt(points, ms)]; }
  }
  if (!nearest) return;
  const row = state.rows.find((r) => r.n === nearest[0]);
  $('sim-readout').textContent = `#${row.n} ${paramsText(row.params)} · ${stamp(ms)} capital ${amount(nearest[1])}`;
  if (state.pinned === null && state.pick !== row.n) {
    state.pick = row.n;
    draw();
    renderTable();
  }
});

canvas.addEventListener('click', () => {
  state.pinned = state.pinned === null ? state.pick : null;
});

window.addEventListener('resize', draw);

/* -------------------------------------------------------- KPI comparison */

// [label, key in row.kpi, format, verdict(v) -> 'good' | 'ok' | 'bad' | '', target text]
const pct = (v) => v.toFixed(1) + '%';
const num = (v) => v.toFixed(2);
const KPIS = [
  ['ROI', 'roi', pct, (v) => v > 0 ? 'good' : 'bad', '> 0 over the window'],
  ['CAR', 'car', pct, (v) => v > 15 ? 'good' : v > 0 ? 'ok' : 'bad', '> 15% a year'],
  ['profit factor', 'profitFactor', num, (v) => v > 2 ? 'good' : v > 1.5 ? 'ok' : 'bad', '> 1.5 good, > 2 excellent'],
  ['expectancy', 'expectancy', amount, (v) => v > 0 ? 'good' : 'bad', '> 0 a trade'],
  ['win rate', 'winRate', percent, () => '', '40-60% for most, depends on the strategy'],
  ['max DD', 'maxDrawdownPct', pct, (v) => v < 20 ? 'good' : 'bad', '< 20% peak to trough'],
  ['risk-reward', 'riskReward', num, (v) => v >= 2 ? 'good' : v >= 1 ? 'ok' : 'bad', 'avg win / avg loss >= 2'],
  ['Sharpe', 'sharpe', num, (v) => v > 2 ? 'good' : v > 1 ? 'ok' : 'bad', '> 1 good, > 2 excellent'],
  ['CAR/MDD', 'carMdd', num, (v) => v > 1 ? 'good' : v > 0 ? 'ok' : 'bad', 'higher is better'],
  ['Ulcer', 'ulcer', num, (v) => v < 5 ? 'good' : v < 10 ? 'ok' : 'bad', 'depth and length of drawdowns, lower is better'],
];
const MARK = { good: ' \u2713', ok: ' ~', bad: ' \u2717' };
state.kpiSort = { col: 'targets', dir: -1 };

// the targets a run meets: 'good' counts, 'ok' (the lower bar) counts half
function targets(row) {
  let met = 0;
  for (const [, key, , verdict] of KPIS) {
    const v = row.kpi && row.kpi[key];
    if (v === null || v === undefined) continue;
    const said = verdict(v);
    met += said === 'good' ? 1 : said === 'ok' ? 0.5 : 0;
  }
  return met;
}

function kpiValue(row, col) {
  if (col === 'n') return row.n;
  if (col === 'targets') return targets(row);
  if (col.startsWith('p:')) return sortValue(row, col);
  const v = row.kpi && row.kpi[col];
  return v === null || v === undefined ? -Infinity : v;
}

function renderKpi() {
  const rows = state.rows.filter((r) => !r.error && r.kpi);
  $('kpi-panel').hidden = !rows.length;
  if (!rows.length) return;
  const varied = (state.job && state.job.varied) || [];
  const cols = [['#', 'n', '']].concat(varied.map((v) => [v, 'p:' + v, '']))
    .concat(KPIS.map(([label, key, , , target]) => [label, key, target]))
    .concat([['targets met', 'targets', 'good counts 1, the lower bar half']]);
  const head = $('kpi-thead');
  head.textContent = '';
  const tr = document.createElement('tr');
  for (const [label, col, target] of cols) {
    const th = document.createElement('th');
    th.textContent = label + (state.kpiSort.col === col ? (state.kpiSort.dir > 0 ? ' \u25b2' : ' \u25bc') : '');
    th.dataset.col = col;
    if (target) th.title = 'target: ' + target;
    if (col !== 'n' && !col.startsWith('p:')) th.className = 'num';
    tr.appendChild(th);
  }
  tr.append(document.createElement('th'), document.createElement('th'));
  head.appendChild(tr);

  const { col, dir } = state.kpiSort;
  // ascending is better for the two risk measures, so their first click
  // puts the safest run on top as the others put the best one
  const body = $('kpi-rows');
  body.textContent = '';
  rows.slice().sort((a, b) => {
    const x = kpiValue(a, col), y = kpiValue(b, col);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || a.n - b.n;
  }).forEach((row, i) => {
    const line = document.createElement('tr');
    line.dataset.n = row.n;
    if (row.n === state.pick) line.className = 'selected';
    for (const [, c] of cols) {
      const td = document.createElement('td');
      if (c === 'n') td.textContent = row.n + (i === 0 ? ' \u2605' : '');
      else if (c.startsWith('p:')) {
        const v = row.params[c.slice(2)];
        td.textContent = v === '' ? 'none' : v;
      } else if (c === 'targets') {
        td.textContent = `${targets(row)} / ${KPIS.length - 1}`;
        td.className = 'num';
      } else {
        const [, , format, verdict] = KPIS.find(([, key]) => key === c);
        const v = row.kpi[c];
        if (v === null || v === undefined) { td.textContent = 'n/a'; td.className = 'num'; }
        else {
          const said = verdict(v);
          td.textContent = format(v) + (MARK[said] || '');
          td.className = 'num' + (said ? ' ' + said : '');
        }
      }
      line.appendChild(td);
    }
    line.append(actions([['analisi', 'what these KPIs say about the run, in words']]), starCell(row));
    body.appendChild(line);
  });
}

$('kpi-thead').addEventListener('click', (event) => {
  const col = event.target.dataset && event.target.dataset.col;
  if (!col) return;
  const lowFirst = col === 'maxDrawdownPct' || col === 'ulcer' || col === 'n' || col.startsWith('p:');
  state.kpiSort = { col, dir: state.kpiSort.col === col ? -state.kpiSort.dir : (lowFirst ? 1 : -1) };
  renderKpi();
});
$('kpi-rows').addEventListener('click', rowClick);
$('kpi-rows').addEventListener('mouseover', (event) => {
  const line = event.target.closest('tr[data-n]');
  if (!line || state.pinned !== null) return;
  state.pick = Number(line.dataset.n);
  draw();
});

/* ---------------------------------------------------------- KPI analysis */

// Each KPI judged against its usual benchmark, then a verdict in words.
// The thresholds are the ones given with the procedure, and they are not the
// table's own marks (KPIS above): the table ranks, this reads one run.
const JUDGE = [
  ['ROI', 'roi', pct, (v) => v <= 0 ? ['\u2717', 'Negativo'] : ['\u2713', 'Positivo']],
  ['CAR', 'car', pct, (v) => v <= 0 ? ['\u2717', 'Negativo'] : ['\u2713', 'Positivo']],
  ['Profit Factor', 'profitFactor', num, (v) => v < 1 ? ['\u2717', 'Non profittevole (PF < 1)']
    : v < 1.5 ? ['\u2717', 'Debole: edge esiguo, sensibile a costi/slippage']
    : v < 2 ? ['\u2713', 'Buono'] : ['\u2713', 'Ottimo']],
  ['Expectancy', 'expectancy', amount, (v) => v <= 0 ? ['\u2717', 'Non sostenibile (expectancy \u2264 0)'] : ['\u2713', 'Positiva']],
  ['Win Rate', 'winRate', percent, (v) => (v *= 100) >= 40 && v <= 60 ? ['~', 'Nella media per strategie direzionali']
    : v < 40 ? ['\u2717', 'Basso'] : ['\u2713', 'Alto']],
  ['Max Drawdown', 'maxDrawdownPct', pct, (v) => v < 10 ? ['\u2713', 'Drawdown basso']
    : v < 15 ? ['~', 'Drawdown moderato'] : v < 20 ? ['\u2717', 'Drawdown alto']
    : ['\u2717', 'Drawdown molto alto, psicologicamente difficile']],
  ['Risk-Reward', 'riskReward', num, (v) => v < 1 ? ['\u2717', 'Sfavorevole: perdite medie > guadagni medi']
    : v < 2 ? ['~', 'Neutro/moderato'] : ['\u2713', 'Favorevole']],
  ['Sharpe', 'sharpe', num, (v) => v < 1 ? ['\u2717', 'Sub-par: rendimento aggiustato per volatilit\u00e0 debole']
    : v < 2 ? ['\u2713', 'Buono'] : ['\u2713', 'Ottimo']],
  ['CAR/MDD', 'carMdd', num, (v) => v < 0.3 ? ['~', 'Basso: rendimento \u201ccostoso\u201d in termini di drawdown']
    : v < 0.6 ? ['~', 'Moderato'] : ['\u2713', 'Buono']],
  ['Ulcer', 'ulcer', num, (v) => v < 6 ? ['\u2713', 'Stress da drawdown contenuto']
    : v < 8 ? ['~', 'Moderato'] : ['\u2717', 'Elevato: periodi di sofferenza prolungati']],
];

// the procedure itself: {summary, table, strengths, weaknesses, verdict, todo}
function analyse(row) {
  // a figure the run cannot have is read as 0, except the two ratios with
  // nothing under them - no losing trade - which are then at their best
  const k = { ...row.kpi };
  for (const key of Object.keys(k)) k[key] = k[key] ?? 0;
  for (const key of ['profitFactor', 'riskReward']) k[key] = row.kpi[key] ?? Infinity;
  const { roi, car, profitFactor: pf, expectancy, winRate, maxDrawdownPct: mdd,
          riskReward: rr, sharpe, ulcer } = k;
  const win = winRate * 100;
  const profitable = pf > 1 && expectancy > 0 && roi > 0;
  const weak = pf < 1.5 || mdd > 20 || sharpe < 1;
  const met = targets(row), total = KPIS.length - 1, ratio = total ? met / total : 0;

  const table = JUDGE.map(([label, key, format, judge]) => {
    const v = row.kpi[key];
    return v === null || v === undefined ? [label, 'n/a', '', 'non calcolabile su questo run']
      : [label, format(v), ...judge(v)];
  });
  table.push(['Targets Met', `${met}/${total}`, ...(ratio >= 0.66 ? ['\u2713', 'Buona percentuale di target soddisfatti']
    : ratio >= 0.33 ? ['~', 'Solo una parte dei target soddisfatti'] : ['\u2717', 'Pochi target soddisfatti'])]);

  const strengths = [];
  if (expectancy > 0) strengths.push('Expectancy positiva: ogni trade genera valore atteso.');
  if (roi > 0 && car > 0) strengths.push('ROI e CAR positivi: la strategia \u00e8 redditizia nel periodo analizzato.');
  if (win >= 40 && win <= 60) strengths.push('Win rate nella media per strategie direzionali.');
  const weaknesses = [];
  if (pf < 1.5) weaknesses.push('Profit factor debole: edge esiguo, sensibile a costi e slippage in live.');
  if (mdd > 20) weaknesses.push('Max drawdown molto alto: psicologicamente difficile e rischioso.');
  if (sharpe < 1) weaknesses.push('Sharpe ratio sub-par: rendimento aggiustato per volatilit\u00e0 debole.');
  if (rr < 1) weaknesses.push('Risk-reward sfavorevole: perdite medie superiori ai guadagni medi.');
  if (ulcer > 8) weaknesses.push('Ulcer index elevato: stress da drawdown significativo.');
  const todo = [];
  if (rr < 1) todo.push('Migliorare il risk-reward (es. trailing stop, take-profit pi\u00f9 ampi, filtri di ingresso).');
  if (mdd > 15) todo.push('Ridurre il drawdown (position sizing dinamico, stop pi\u00f9 stretti, filtri di regime).');
  todo.push('Validare su out-of-sample e con walk-forward analysis per verificare robustezza.');

  return {
    summary: !profitable
      ? 'Strategia non profittevole o con edge non chiaro: alcune metriche fondamentali (profit factor, expectancy, ROI) non sono positive.'
      : weak ? 'Strategia profittevole ma debole: edge reale ma margini di sicurezza ridotti. Quasi tutte le metriche risk-adjusted e di rischio sono sotto i target tipici per una strategia robusta.'
      : 'Strategia profittevole e complessivamente solida: edge chiaro e metriche risk-adjusted accettabili.',
    table, strengths, weaknesses, todo,
    verdict: !profitable ? 'Non adatta per live trading; richiede riprogettazione o scarto.'
      : weak ? 'Accettabile per ricerca / paper trading, ma non pronta per live con capitale significativo.'
      : 'Candidata per live trading, previa validazione out-of-sample e controlli operativi.',
  };
}

function openAnalysis(n) {
  const row = state.rows.find((r) => r.n === n);
  if (!row || !row.kpi) return;
  const a = analyse(row);
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const list = (items) => {
    const ul = el('ul');
    for (const item of items.length ? items : ['nessuno']) ul.append(el('li', item));
    return ul;
  };
  const body = $('analysis-body');
  body.textContent = '';
  body.append(el('p', a.summary, 'analysis-summary'));
  const table = el('table');
  table.append(el('thead'));
  table.tHead.insertRow().append(...['KPI', 'Valore', 'Giudizio', 'Motivazione'].map((h) => el('th', h)));
  const tbody = el('tbody');
  const cls = { '\u2713': 'good', '~': 'ok', '\u2717': 'bad' };
  for (const [label, value, mark, reason] of a.table) {
    const tr = tbody.insertRow();
    tr.append(el('td', label), el('td', value, 'num'), el('td', mark, cls[mark]), el('td', reason));
  }
  table.append(tbody);
  body.append(table,
    el('h3', 'Valutazione complessiva'), el('p', 'Punti di forza:'), list(a.strengths),
    el('p', 'Punti deboli critici:'), list(a.weaknesses),
    el('h3', 'Cosa farei / Raccomandazioni'), el('p', 'Classificazione: ' + a.verdict),
    el('p', 'Azioni concrete suggerite:'), list(a.todo));
  $('analysis-title').textContent = `analisi KPI \u00b7 ${runId(n)} ${paramsText(row.params)}`;
  $('analysis-dialog').showModal();
}

/* ---------------------------------------------------------- saved sets */

function fullStamp(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${stamp(ms)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

async function openSets() {
  const body = $('sets-rows');
  body.textContent = '';
  if (!$('sets-dialog').open) $('sets-dialog').showModal();
  const { sweeps } = await ask('api/sweeps');
  if (!sweeps.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 12;
    cell.textContent = 'no set saved yet: one is kept every time a simulation ends';
    return;
  }
  for (const set of sweeps) {
    const row = body.insertRow();
    row.dataset.id = set.id;
    const cells = [set.id, fullStamp(set.saved), null, set.strategy, set.instrument,
      set.granularity, set.from, set.to, (set.varied || []).join(', '),
      `${set.runs}${set.runs < set.total ? ' of ' + set.total : ''}${set.stopped ? ' (stopped)' : ''}`,
      set.best === null || set.best === undefined ? '' : amount(set.best)];
    cells.forEach((text, i) => {
      const cell = row.insertCell();
      if (i === 2) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'set-name';
        input.value = set.name || '';
        input.placeholder = 'name';
        input.maxLength = 120;
        cell.appendChild(input);
        return;
      }
      cell.textContent = text;
      if (i >= 9) cell.className = 'num';
    });
    if (set.bestParams) row.cells[10].title = 'best: ' + paramsText(set.bestParams);
    const rerun = document.createElement('button');
    rerun.type = 'button';
    rerun.className = 'set-rerun';
    rerun.textContent = 'rerun';
    rerun.title = 'run the same set again on the code and data there are now; the old one is kept';
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.className = 'set-delete';
    drop.textContent = 'delete';
    row.insertCell().append(rerun, ' ', drop);
  }
}

async function loadSet(id) {
  const job = await ask('api/sweeps/' + id);
  $('sets-dialog').close();
  state.pick = state.pinned = null;
  state.fields = job.fields || null;
  fillForm({ ...(job.fields || {}), ...(job.grid || {}) });
  $('sweep-name').value = job.name || '';
  countLater();
  follow(job);
  return job;
}

const setsError = (error) => message(String(error.message || error));

$('sets-open').addEventListener('click', () => openSets().catch(setsError));
$('sets-rows').addEventListener('click', async (event) => {
  const row = event.target.closest('tr[data-id]');
  if (!row || event.target.classList.contains('set-name')) return;
  try {
    if (event.target.classList.contains('set-rerun')) {
      rerun(await loadSet(row.dataset.id));
      return;
    }
    if (event.target.classList.contains('set-delete')) {
      if (!await askUser('Delete this set of simulations? It cannot be undone.', 'delete')) return;
      await post('api/sweeps/' + row.dataset.id, { delete: true });
      await openSets();
      return;
    }
    await loadSet(row.dataset.id);
  } catch (error) { setsError(error); }
});
// a name is saved when the box is left, or on enter
$('sets-rows').addEventListener('change', (event) => {
  const row = event.target.closest('tr[data-id]');
  if (row && event.target.classList.contains('set-name')) {
    post('api/sweeps/' + row.dataset.id, { name: event.target.value }).catch(setsError);
  }
});

/* ---------------------------------------------------------------- start */

$('sim-controls').addEventListener('submit', simulate);
$('sim-controls').addEventListener('input', countLater);
$('strategy').addEventListener('change', () => { onStrategy(); countLater(); });
$('instrument').addEventListener('change', onInstrument);
$('granularity').addEventListener('change', onGranularity);

async function start() {
  const stores = await ask('api/stores');
  state.instruments = stores.instruments || [];
  state.forms = stores.params || {};
  state.defaults = stores.defaults || {};
  if (stores.equity !== undefined && stores.equity !== null) $('balance').value = stores.equity;
  fill($('strategy'), stores.strategies || []);
  fill($('instrument'), state.instruments.map((r) => r.instrument));
  onStrategy();
  fromMainPage();
  countLater();
  // not awaited: the stars can wait, the table cannot wait on them
  loadFavourites().catch((error) => message(String(error.message || error)));
  // a sweep already going, or the last one to finish, is picked up again
  const job = await ask('api/sweep?since=0');
  if (job.total) {
    state.fields = job.fields || null;
    fillForm({ ...(job.fields || {}), ...(job.grid || {}) });
    countLater();
    follow(job);
  }
}

start().catch((error) => message(String(error.message || error)));
