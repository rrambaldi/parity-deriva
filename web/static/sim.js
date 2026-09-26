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
  // the peak margin, in per cent of the capital; out of margin last (marginCell)
  ['margin', (r) => r.margin ? (r.margin.ok ? r.margin.peakMarginPct : Infinity) : null, String],
];

// a run's margin in a cell: its peak, and whether the account always had room
function marginCell(td, m) {
  td.textContent = m ? `${m.ok ? '\u2713' : '\u2717'} ${m.peakMarginPct.toFixed(1)}%` : 'n/a';
  td.className = 'num' + (m && !m.ok ? ' bad' : '');
  td.title = marginText(m);
}

const state = {
  forms: {}, defaults: {}, descriptions: {}, instruments: [],
  rows: [],          // finished runs, as the service sent them
  job: null,         // the last status
  fields: null,      // the fixed fields the sweep was started with
  pick: null,        // the run under the cursor or clicked in the table
  pinned: null,      // the run clicked in the table
  sort: { col: 'n', dir: 1 },
  polling: false,
  favourites: [],    // the runs starred to trade live, from /api/favourites
  light: { param: '', value: null },   // the value whose curves are lit, see renderLight
  effectsParam: null,                  // the parameter whose values are listed, see renderEffects
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

function paramsText(params, between = ' ') {
  return Object.entries(params || {})
    .map(([k, v]) => `${k}=${paramValue(k, v)}`).join(between);
}

/*
 * The head of the dialog a run opens in (sim.html): what it is and the run's
 * id, short and in the mono, in the title; its parameters on the line under
 * it, the whole of them in its tooltip when the line is cut.
 */
function runHead(what, n) {
  const title = $('analysis-title');
  title.textContent = what + ' ';
  const id = document.createElement('span');
  id.className = 'id';
  id.textContent = runId(n);
  title.append(id);
  const row = state.rows.find((r) => r.n === n);
  const sub = $('analysis-sub');
  sub.textContent = row ? paramsText(row.params, ' \u00b7 ') : '';
  sub.title = sub.textContent;
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

/*
 * The page out of reach but for its menu and theme, top right: while a
 * simulate is being asked for, from the click to the question answered,
 * nothing on it can start a second one or change the form under the first.
 * inert rather than disabled: it takes the mouse and the keyboard both, and
 * hands every control back as it was.
 */
function lock(on) {
  for (const el of [...document.body.children, $('sets-open')]) {
    if (!['HEADER', 'DIALOG', 'SCRIPT'].includes(el.tagName)) el.inert = on;
  }
}

// a yes or no asked on the page, as in app.js. Not modal, so the menu stays
// in reach: the rest of the page is locked instead - unless it is asked from
// a dialog that is modal already, which would hide a plain one behind it
function askUser(text, yes = 'run it anyway') {
  const dialog = $('ask-dialog');
  $('ask-text').textContent = text;
  $('ask-yes').textContent = yes;
  $('ask-yes').dataset.icon = yes === 'delete' ? 'delete' : 'yes';
  dialog.returnValue = '';
  if (document.querySelector('dialog:modal')) dialog.showModal();
  else { lock(true); dialog.show(); }
  $('ask-yes').focus();
  return new Promise((resolve) => dialog.addEventListener('close', () => {
    lock(false);
    resolve(dialog.returnValue === 'yes');
  }, { once: true }));
}
// a modal dialog closes on escape by itself, a plain one has to be told
$('ask-dialog').addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('ask-dialog').matches(':modal')) $('ask-dialog').close();
});

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

/* The account's flags, each a Y box and an N box (sim.html): Y is '1', N is
   '0', both is a run of each. Clearing the last one ticked puts the default
   back. The trailing stop's default is the strategy's: on for those with a
   climbing stop of their own (api/stores defaults). */
const FLAGS = ['intraday', 'inverse', 'trailing', 'trailProfit'];

function flagDefault(name) {
  return name === 'trailing'
    && (state.defaults[$('strategy').value] || {}).trailing === 1 ? '1' : '0';
}

function flagGrid(name) {
  const y = $(`g-${name}-y`).checked, n = $(`g-${name}-n`).checked;
  return y && n ? '0, 1' : y ? '1' : '0';
}

// from a grid's text, or one run's value: 'none' and '' are the default
function fillFlag(name, text) {
  const values = String(text ?? '').split(',').map((v) => v.trim().toLowerCase())
    .map((v) => (v === '' || v === 'none' ? flagDefault(name) : v === 'true' ? '1' : v));
  let y = values.includes('1'), n = values.includes('0');
  if (!y && !n) [y, n] = flagDefault(name) === '1' ? [true, false] : [false, true];
  $(`g-${name}-y`).checked = y;
  $(`g-${name}-n`).checked = n;
  trailPipsOn();
}

// the trail pips are for a stop that follows: with the trailing stop N only
// there is nothing to set (MoneyManager.trail), and the sweep runs one run
// for them whatever they are (expandGrid in web/service.py)
function trailPipsOn() {
  $('g-trailPips').disabled = !$('g-trailing-y').checked;
}

for (const name of FLAGS) {
  for (const side of ['y', 'n']) {
    $(`g-${name}-${side}`).addEventListener('change', () => {
      if (!$(`g-${name}-y`).checked && !$(`g-${name}-n`).checked) fillFlag(name, '');
      trailPipsOn();
    });
  }
}

function onStrategy() {
  // a strategy picked puts its own trailing default back; fillForm runs after
  fillFlag('trailing', '');
  // what the strategy says it does, on a line of its own under the choice
  const about = state.descriptions[$('strategy').value] || '';
  $('strategy-about').textContent = about;
  $('strategy-about').hidden = !about;
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
    // what the number is, in a few words under it (the strategy's PARAM_HELP)
    if (field.help) {
      const help = document.createElement('small');
      help.className = 'param-help';
      help.textContent = field.help;
      label.appendChild(help);
    }
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

// the dates typed stay when the timeframe, the instrument or the strategy
// changes: only an empty one is filled, with what the series holds. They were
// overwritten here, and a sweep asked for from 2025 ran from 2015
function onGranularity() {
  const row = granularities().find((r) => r.granularity === $('granularity').value);
  if (!row) return;
  if (!$('from').value) $('from').value = stamp(row.from);
  if (!$('to').value) $('to').value = stamp(row.to);
}

function fixedFields() {
  return {
    strategy: $('strategy').value, instrument: $('instrument').value,
    granularity: $('granularity').value, from: $('from').value,
    to: $('to').value, risk: $('risk').value, balance: $('balance').value,
    leverage: $('leverage').value,
    newsBefore: $('newsBefore').value, newsAfter: $('newsAfter').value,
    newsImpacts: $('newsImpacts').value,
  };
}

function gridFields() {
  const grid = {};
  for (const input of $('grid-strategy').querySelectorAll('input')) {
    grid[input.dataset.name] = input.value;
  }
  for (const name of ['maxStop', 'session', 'maxBars', 'slScale', 'tpScale', 'trailPips']) {
    // switched off, the box keeps what it held for when it is on again
    grid[name] = $('g-' + name).disabled ? '' : $('g-' + name).value;
  }
  for (const name of FLAGS) grid[name] = flagGrid(name);
  return grid;
}

// the form from a sweep's fixed fields with its grid over them
function fillForm(f) {
  // AB-INVERSA and the FTW ones were strategies before `inverse` was an
  // option: an old set comes back as its strategy turned round
  const alias = /^(.+)-INVERSA$/.exec(f.strategy || '');
  if (alias && Array.from($('strategy').options).some((o) => o.value === alias[1])) {
    f = { ...f, strategy: alias[1], inverse: '1' };
  }
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
  for (const id of ['from', 'to', 'risk', 'balance', 'leverage']) if (f[id]) $(id).value = f[id];
  // a set made before these were on this page had no news rule
  $('newsBefore').value = f.newsBefore || '';
  $('newsAfter').value = f.newsAfter || '';
  $('newsImpacts').value = f.newsImpacts || 'high';
  for (const input of $('grid-strategy').querySelectorAll('input')) {
    if (f[input.dataset.name] !== undefined) input.value = f[input.dataset.name];
  }
  for (const name of ['maxStop', 'session', 'maxBars', 'slScale', 'tpScale', 'trailPips']) {
    if (f[name]) $('g-' + name).value = f[name];
  }
  for (const name of FLAGS) fillFlag(name, f[name]);
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
  // locked from the click: the count and the estimate can take a while,
  // and the page must not take a second click meanwhile
  lock(true);
  try {
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
          + ` from ${fields.from || 'the start'} to ${fields.to || 'the end'}`
          + (ahead.fine ? ` (${ahead.granularity} candles and the ${ahead.fine} bars under them)` : '')
          + (ahead.measured ? `.\n\nAt the ${ahead.rate.toLocaleString()} bars a second the last run managed,`
            : `.\n\nAt ${ahead.rate.toLocaleString()} bars a second, a guess until a run has been timed here,`)
          + ` that is about ${howLong(ahead.seconds * combos)} in all.\n\nRun it anyway?`;
        if (!await askUser(question)) return;
      }
    } catch (error) { /* no estimate, no warning, still a sweep */ }

    try {
      state.rows = [];
      state.pick = state.pinned = null;
      state.fields = fields;
      follow(await post('api/sweep', { fields, grid, name: $('sweep-name').value }));
      keepAddress();
    } catch (error) {
      message(String(error.message || error));
    }
  } finally {
    lock(false);
  }
}

/* One status, and the next poll if the sweep is still going. */
function follow(job) {
  state.job = job;
  if (job.since === 0) state.rows = [];
  state.rows.push(...(job.done || []));
  const running = job.running;
  $('run').disabled = running;
  $('stop').hidden = $('pause').hidden = !running;
  $('rerun').hidden = running || !job.total;
  $('delete').hidden = running || !job.id;
  if (running) {
    $('stop').disabled = $('pause').disabled = !!job.cancel;
    showPaused(job.paused);
  }
  if (job.error) message(job.error);
  else if (running) {
    // just the values that change from run to run, the rest is in the title
    const c = job.current;
    const params = c ? Object.fromEntries((job.varied || []).map((k) => [k, c.params[k]])) : {};
    const text = paramsText(params);
    // the phase this run is in, with its own share: each series read or taken
    // from memory, its bars prepared (data/replay.py), then the simulation
    const p = job.progress;
    const share = (done, total) => total ? ` ${Math.min(100, Math.floor(100 * done / total))}%` : '';
    const phase = !p ? ' · starting'
      : p.loading ? ` · ${p.stage || 'reading the candles'}${share(p.read, p.toRead)}`
      : ` · simulating${share(p.bars, p.total)}`;
    message(`run ${c ? c.n : state.rows.length} of ${job.total}${phase}` + (job.paused ? ' · paused' : '')
      + (text ? ` · ${text}` : ''), 'info');
  }
  else if (job.total) message(job.cancel ? `stopped after ${state.rows.length} of ${job.total} runs` : '');
  const f = job.fields || {};
  $('sim-title').textContent = (job.id ? `[${job.id}] ` : '') + (job.name ? `${job.name} · ` : '')
    + `${f.strategy} on ${f.instrument} ${f.granularity}`
    + ` · ${f.from} .. ${f.to} · ${state.rows.length} of ${job.total} runs`;
  renderCurrent();
  renderTable();
  renderKpi();
  renderLight();
  renderEffects();
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

// the set on show, from the list of sets (api/sweeps/<id>); the page then
// starts over empty, as if no set had been run
$('delete').addEventListener('click', async () => {
  const job = state.job;
  if (!job || !job.id) return;
  if (!await askUser(`Delete the set ${job.id}${job.name ? ' (' + job.name + ')' : ''}? It cannot be undone.`, 'delete')) return;
  try {
    await post('api/sweeps/' + job.id, { delete: true });
    location.reload();
  } catch (error) { message(String(error.message || error)); }
});

// pause and resume are one button, saying what a click does next
function showPaused(paused) {
  $('pause').textContent = paused ? 'resume' : 'pause';
  $('pause').dataset.icon = paused ? 'play' : 'pause';
}
$('pause').addEventListener('click', async () => {
  const job = state.job;
  $('pause').disabled = true;
  try {
    job.paused = (await post('api/sweep/' + (job.paused ? 'resume' : 'pause'))).paused;
    showPaused(job.paused);
  } catch (error) { message(String(error.message || error)); }
  finally { $('pause').disabled = false; }
});

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
        td.textContent = paramValue(c.slice(2), row.params[c.slice(2)]);
      } else if (row.error) {
        td.textContent = c === 's:trades' ? row.error : '';
        td.className = 'bad';
      } else {
        const [label, get, format] = STATS.find(([l]) => 's:' + l === c);
        const v = get(row);
        td.textContent = format(v);
        td.className = 'num' + (label === 'net' ? (v > 0 ? ' good' : v < 0 ? ' bad' : '') : '');
        if (label === 'margin') marginCell(td, row.margin);
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

// the way out of a row; a click anywhere else on it only picks its curve
function actions(more = []) {
  const td = document.createElement('td');
  td.className = 'run-actions';
  for (const [name, title] of [['view', 'this run in full, on its own page'], ...more]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.action = name;
    button.dataset.icon = name === 'analisi' ? 'analysis' : name;
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
  if (action === 'view') return openPage(n, event.currentTarget);
  if (action === 'analisi') return openAnalysis(n);
  if (action === 'entries') return openEntries(n).catch((error) => message(String(error.message || error)));
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

// one run's fields, as the run page reads them from its address
function runFields(n) {
  const row = state.rows.find((r) => r.n === n);
  const fields = { ...state.fields, ...row.params };
  for (const key of ['intraday', 'inverse', 'trailProfit']) if (fields[key] === '0') delete fields[key];
  for (const key of Object.keys(fields)) if (fields[key] === '') delete fields[key];
  return fields;
}

// where one run is drawn: the run page, with the set and number that find it
// on disk once the service has let its cache go, instead of running it again
function runAddress(n) {
  return 'run?' + new URLSearchParams({
    ...(state.job.id ? { sweep: state.job.id, run: n } : {}), ...runFields(n) });
}

// The set on show and how its tables are sorted, in the address: the run
// page is left with the back button, and comes back to the same table
function keepAddress() {
  const q = new URLSearchParams();
  if (state.job && state.job.id) q.set('set', state.job.id);
  q.set('sort', `${state.sort.col}:${state.sort.dir}`);
  q.set('kpi', `${state.kpiSort.col}:${state.kpiSort.dir}`);
  history.replaceState(null, '', '?' + q);
}

// the run on a page of its own. The runs of the table it was picked from go
// with it, in that table's order: the run page's prev and next walk them
function openPage(n, body) {
  const row = state.rows.find((r) => r.n === n);
  if (!row || row.error) return;
  keepAddress();
  const ok = new Set(state.rows.filter((r) => !r.error).map((r) => r.n));
  const runs = [...body.querySelectorAll('tr[data-n]')].map((tr) => Number(tr.dataset.n))
    .filter((m) => ok.has(m)).map((m) => runAddress(m));
  try { sessionStorage.setItem('run-list', JSON.stringify({ back: location.href, runs })); }
  catch (error) { /* no storage, no prev and next: the run still opens */ }
  location.href = runAddress(n);
}

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

  const p = palette();
  // grid and the axes' labels, recessive
  ctx.font = '11px ' + p.mono;
  ctx.lineWidth = 1;
  ctx.fillStyle = p.text3;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const v = low + (high - low) * (i / 4);
    const py = Math.round(y(v)) + 0.5;
    ctx.strokeStyle = p.grid;
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
  ctx.strokeStyle = p.border;
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(AXIS.left, y(start)); ctx.lineTo(width - AXIS.right, y(start)); ctx.stroke();
  ctx.setLineDash([]);

  // a step: the balance moves when a trade closes and not in between
  const line = (points, colour, widthPx, end, alpha = 1) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = widthPx;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    points.forEach(([ms, v], i) => {
      if (i === 0) ctx.moveTo(x(ms), y(v));
      else { ctx.lineTo(x(ms), y(points[i - 1][1])); ctx.lineTo(x(ms), y(v)); }
    });
    const last = points[points.length - 1];
    ctx.lineTo(x(end), y(last[1]));
    ctx.stroke();
    ctx.globalAlpha = 1;
  };
  // a value lit under the chart (renderLight): its runs in ink over the rest,
  // which fade further
  const lit = litRuns();
  for (const [n, points] of curves) {
    // finished runs, recessive: the one running now and the pick stand out below
    if (n !== state.pick && !lit.has(n)) line(points, p.text3, 1, to, lit.size ? 0.15 : 0.38);
  }
  for (const [n, points] of curves) {
    if (n !== state.pick && lit.has(n)) line(points, p.text, 1.25, to, 0.85);
  }
  if (live) line(live, p.text, 2, live[live.length - 1][0]);
  const picked = curves.find(([n]) => n === state.pick);
  if (picked) line(picked[1], p.entry, 2, to);
}

/*
 * The highlight under the chart: one of the parameters the set varies, and a
 * button per value it took, with how many runs took it. A value pressed
 * lights its runs' curves; pressed again, none is lit. Rebuilt only when
 * what it shows changes: the poll comes every PROGRESS_MS, and a button
 * replaced between the press and the release loses the click.
 */
function renderLight() {
  const varied = (state.job && state.job.varied) || [];
  const box = $('sim-light');
  box.hidden = !varied.length || !state.rows.length;
  if (!varied.includes(state.light.param)) state.light = { param: varied[0] || '', value: null };
  const counts = new Map();
  for (const row of state.rows) {
    if (row.error) continue;
    const v = String(row.params[state.light.param]);
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  if (!counts.has(state.light.value)) state.light.value = null;
  // by number where they are numbers, 'none' first
  const key = (v) => v === '' ? -Infinity : Number(v);
  const values = [...counts.keys()].sort((a, b) => (key(a) - key(b)) || a.localeCompare(b));
  const shown = JSON.stringify([varied, state.light, values.map((v) => [v, counts.get(v)])]);
  if (box.dataset.shown === shown) return;
  box.dataset.shown = shown;
  fill($('light-param'), varied);
  $('light-param').value = state.light.param;
  const buttons = $('light-values');
  buttons.textContent = '';
  for (const v of values) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.value = v;
    button.textContent = `${paramValue(state.light.param, v)} (${counts.get(v)})`;
    button.setAttribute('aria-pressed', String(v === state.light.value));
    buttons.append(button);
  }
}

// the runs of the value lit, by number: none with nothing lit
function litRuns() {
  const { param, value } = state.light;
  if (!param || value === null) return new Set();
  return new Set(state.rows.filter((r) => !r.error && String(r.params[param]) === value)
    .map((r) => r.n));
}

$('light-param').addEventListener('change', () => {
  state.light = { param: $('light-param').value, value: null };
  renderLight();
  draw();
});
$('light-values').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-value]');
  if (!button) return;
  state.light.value = state.light.value === button.dataset.value ? null : button.dataset.value;
  renderLight();
  draw();
});

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
  // with a value lit, the cursor picks among its runs only
  const lit = litRuns();
  for (const [n, points] of geometry.curves) {
    if (lit.size && !lit.has(n)) continue;
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

// Was: ranked by the targets met. Now: by the score, the rank of a run
// (report.score on the server), the targets met still a column to click
state.kpiSort = { col: 'score', dir: -1 };
const SCORE_PARTS = 'CAR 35% · max DD and Ulcer 25% · profit factor 20% · Sharpe 20%, less under 30 trades, 0 out of margin';

function kpiValue(row, col) {
  // the margin sorts by its peak, a run out of it last whichever way
  if (col === 'margin') return row.margin ? (row.margin.ok ? row.margin.peakMarginPct : Infinity) : Infinity;
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
    .concat([['score', 'score', '0-100: ' + SCORE_PARTS]])
    .concat(KPIS.map(([label, key, , , target]) => [label, key, target]))
    .concat([['margin', 'margin', 'peak margin of the capital; \u2717 out of margin scores 0']])
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
        td.textContent = paramValue(c.slice(2), row.params[c.slice(2)]);
      } else if (c === 'targets') {
        td.textContent = `${targets(row)} / ${KPIS.length - 1}`;
        td.className = 'num';
      } else if (c === 'margin') {
        marginCell(td, row.margin);
      } else if (c === 'score') {
        const v = row.kpi.score;
        td.textContent = v === null || v === undefined ? 'n/a' : v.toFixed(1);
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
    line.append(actions([['analisi', 'what these KPIs say about the run, in words'],
                         ['entries', 'where price went after each entry, against random entries at the same hour']]),
                starCell(row));
    body.appendChild(line);
  });
}

$('kpi-thead').addEventListener('click', (event) => {
  const col = event.target.dataset && event.target.dataset.col;
  if (!col) return;
  const lowFirst = col === 'maxDrawdownPct' || col === 'ulcer' || col === 'margin' || col === 'n'
    || col.startsWith('p:');
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

/* ---------------------------------------------------- parameter analysis */

// what the runs can be ranked on: the score first, then the KPIs that say how
// a run went. The last two are better low, as their first click in the table
const EFFECT_METRICS = [['score', 'score', (v) => v.toFixed(1)], ['CAR', 'car', kpiPct],
  ['ROI', 'roi', kpiPct], ['max DD', 'maxDrawdownPct', kpiPct], ['Sharpe', 'sharpe', kpiNum],
  ['profit factor', 'profitFactor', kpiNum], ['Ulcer', 'ulcer', kpiNum]];
const LOW_BETTER = ['maxDrawdownPct', 'ulcer'];
// ponytail: thresholds of my choosing, not a statistical test - a set of a few
// dozen runs does not carry one. eta² from which a parameter decides or matters
// little, and the consistency it has to hold to decide or under which the
// others decide for it
const VERDICT = { decides: 0.20, holds: 0.75, little: 0.05, depends: 0.60 };

// the window in equal slices, [[from, to], ...]: quarters, or months under a
// year, and never fewer than 4 or more than 12
function timeSlices(span) {
  if (!span) return [];
  const [from, to] = span, years = (to - from) / (365.25 * 864e5);
  const n = Math.min(12, Math.max(4, Math.round(years >= 1 ? years * 4 : years * 12)));
  return Array.from({ length: n }, (_, i) => [from + (to - from) * i / n,
                                              from + (to - from) * (i + 1) / n]);
}

/*
 * Which varied parameter moves the result, and which of its values does best,
 * four ways over the same runs:
 *
 *   effect       the best value's median less the worst's, in the metric;
 *   consistency  among the runs alike in every other varied parameter - a
 *                context - how often the best value is the first (a tie
 *                counts shared), and its edge over the rest there: whether
 *                the advantage is its own or the others' doing;
 *   eta2         the share of the metric's spread the values account for,
 *                between groups over the total sum of squares;
 *   slicesWon    per value, in how many slices of the window its runs grew
 *                the most - all along, or one lucky stretch. Off the capital
 *                curves, whatever the metric.
 *
 * Pure: `metric(row)` is the figure or null, `low` a lower one better, `span`
 * [from, to] in ms or null. Runs in error or without the figure are left out.
 * Sorted by effect, largest first.
 */
function paramEffects(rows, varied, metric, low, span) {
  const sign = low ? -1 : 1;
  const ok = rows.filter((r) => !r.error && Number.isFinite(metric(r)));
  const better = (r) => sign * metric(r);
  const median = (xs) => {
    const s = xs.slice().sort((a, b) => a - b), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };
  // as renderLight orders them: by number where they are numbers, none first
  const key = (v) => v === '' ? -Infinity : Number(v);
  const order = (a, b) => (key(a) - key(b)) || a.localeCompare(b);
  const slices = timeSlices(span);
  const growth = new Map(ok.map((r) => {
    if (!slices.length) return [r.n, []];
    const points = curveOf(r, span[0]);
    return [r.n, slices.map(([a, b]) => {
      const start = balanceAt(points, a);
      return start > 0 ? balanceAt(points, b) / start - 1 : 0;
    })];
  }));
  const mean = ok.reduce((s, r) => s + better(r), 0) / (ok.length || 1);
  const total = ok.reduce((s, r) => s + (better(r) - mean) ** 2, 0);

  return varied.map((param) => {
    const of = (r) => String(r.params[param]);
    const groups = new Map();
    for (const r of ok) groups.set(of(r), [...(groups.get(of(r)) || []), r]);
    const values = [...groups.keys()].sort(order).map((value) => {
      const runs = groups.get(value);
      return { value, runs: runs.length, median: median(runs.map(metric)),
               rank: median(runs.map(better)), first: 0, contexts: 0, slicesWon: 0 };
    });
    const best = values.reduce((a, v) => (a && a.rank >= v.rank ? a : v), null);
    const worst = values.reduce((a, v) => (a && a.rank <= v.rank ? a : v), null);

    const between = values.reduce((s, v) => {
      const m = groups.get(v.value).reduce((a, r) => a + better(r), 0) / v.runs;
      return s + v.runs * (m - mean) ** 2;
    }, 0);

    const contexts = new Map();
    for (const r of ok) {
      const alike = JSON.stringify(varied.filter((p) => p !== param).map((p) => r.params[p]));
      contexts.set(alike, [...(contexts.get(alike) || []), r]);
    }
    const edges = [];
    for (const runs of contexts.values()) {
      const here = new Map();
      for (const r of runs) here.set(of(r), [...(here.get(of(r)) || []), better(r)]);
      if (here.size < 2) continue;
      const scores = [...here].map(([v, xs]) => [v, median(xs)]);
      const top = Math.max(...scores.map(([, s]) => s));
      const tied = scores.filter(([, s]) => s === top).length;
      for (const [v, s] of scores) {
        const entry = values.find((e) => e.value === v);
        entry.contexts += 1;
        if (s === top) entry.first += 1 / tied;
      }
      const mine = scores.find(([v]) => v === best.value);
      const rest = scores.filter(([v]) => v !== best.value);
      if (mine) edges.push(mine[1] - rest.reduce((a, [, s]) => a + s, 0) / rest.length);
    }

    // a slice two values tie on is shared, as a context is
    slices.forEach((_, i) => {
      const grew = values.map((v) => median(groups.get(v.value).map((r) => growth.get(r.n)[i])));
      const top = Math.max(...grew);
      const tied = grew.filter((g) => g === top).length;
      values.forEach((v, k) => { if (grew[k] === top) v.slicesWon += 1 / tied; });
    });

    const eta2 = total > 0 ? between / total : 0;
    const consistency = best && best.contexts ? best.first / best.contexts : null;
    const verdict = values.length < 2 ? 'one value'
      : eta2 < VERDICT.little ? 'matters little'
      : consistency !== null && consistency < VERDICT.depends ? 'depends on the others'
      : eta2 >= VERDICT.decides && (consistency === null || consistency >= VERDICT.holds)
        ? 'decides' : 'middling';
    return { param, values, best, worst, effect: best ? best.rank - worst.rank : 0,
             consistency, edge: edges.length ? median(edges) : null, eta2,
             slices: slices.length, verdict };
  }).sort((a, b) => b.effect - a.effect);
}

/*
 * The analysis on the page: a row a parameter, and under it the values of the
 * one picked. A row picked lights its best value in the chart (renderLight),
 * a value row that value. Worked out again only when the runs, the metric or
 * the pick change, not on every poll.
 */
function renderEffects() {
  const varied = (state.job && state.job.varied) || [];
  const box = $('sim-effects');
  const pick = $('effects-metric');
  if (!pick.options.length) {
    fill(pick, EFFECT_METRICS.map(([, k]) => k), (k) => EFFECT_METRICS.find((m) => m[1] === k)[0]);
  }
  const metricKey = pick.value || 'score';
  const format = EFFECT_METRICS.find((m) => m[1] === metricKey)[2];
  const shown = [state.job && state.job.id, state.rows.length, metricKey,
                 state.effectsParam, varied.join()].join('|');
  if (box.dataset.shown === shown) return;
  box.dataset.shown = shown;
  box.hidden = !varied.length || !state.rows.some((r) => !r.error);
  if (box.hidden) return;

  const f = state.fields;
  const span = f ? [Date.parse(f.from + 'T00:00:00Z'), Date.parse(f.to + 'T23:59:59Z')] : null;
  const metric = (r) => (r.kpi ? r.kpi[metricKey] ?? null : null);
  const effects = paramEffects(state.rows, varied, metric, LOW_BETTER.includes(metricKey), span);
  const label = paramValue;
  const signed = (v) => (v > 0 ? '+' : '') + format(v);
  const count = (v) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
  const row = (body, cells, data) => {
    const line = document.createElement('tr');
    Object.assign(line.dataset, data);
    for (const [text, cls] of cells) {
      const td = document.createElement('td');
      td.textContent = text;
      if (cls) td.className = cls;
      line.appendChild(td);
    }
    body.appendChild(line);
    return line;
  };

  const body = $('effects-rows');
  body.textContent = '';
  if (!effects.length || !effects[0].values.length) {
    // a set saved before the score, on a service that has not added it yet
    const empty = row(body, [[`no run of this set has a ${EFFECT_METRICS.find((m) => m[1] === metricKey)[0]}`
      + ': pick another metric', 'hint']], {});
    empty.className = 'empty';
    empty.firstChild.colSpan = 8;
  }
  for (const e of effects) {
    if (!e.best) continue;
    const line = row(body, [
      [e.param],
      [`${label(e.param, e.best.value)} (${format(e.best.median)})`],
      [`${label(e.param, e.worst.value)} (${format(e.worst.median)})`],
      [format(e.effect), 'num'],
      [e.consistency === null ? 'n/a' : `${Math.round(e.consistency * 100)}% of ${e.best.contexts}`
        + (e.edge === null ? '' : ` · ${signed(e.edge)}`), 'num'],
      [`${Math.round(e.eta2 * 100)}%`, 'num'],
      [`${count(e.best.slicesWon)}/${e.slices}`, 'num'],
      [e.verdict, 'verdict ' + (e.verdict === 'decides' ? 'strong' : e.verdict === 'matters little' ? 'weak' : '')],
    ], { param: e.param, best: e.best.value });
    if (e.param === state.effectsParam) line.className = 'selected';
  }

  const picked = effects.find((e) => e.param === state.effectsParam && e.best);
  $('effects-values').hidden = !picked;
  const values = $('effects-value-rows');
  values.textContent = '';
  if (!picked) return;
  $('effects-values-param').textContent = picked.param;
  for (const v of picked.values) {
    row(values, [
      [label(picked.param, v.value)], [String(v.runs), 'num'], [format(v.median), 'num'],
      [v.contexts ? `${count(v.first)} of ${v.contexts}` : 'n/a', 'num'],
      [`${count(v.slicesWon)}/${picked.slices}`, 'num'],
      [[v === picked.best ? 'best' : v === picked.worst ? 'worst' : '',
        v.runs < 3 ? 'few runs' : ''].filter(Boolean).join(' · '), 'hint'],
    ], { value: v.value });
  }
}

$('effects-metric').addEventListener('change', renderEffects);
$('effects-rows').addEventListener('click', (event) => {
  const line = event.target.closest('tr[data-param]');
  if (!line) return;
  state.effectsParam = line.dataset.param;
  state.light = { param: line.dataset.param, value: line.dataset.best };
  renderLight();
  renderEffects();
  draw();
});
$('effects-value-rows').addEventListener('click', (event) => {
  const line = event.target.closest('tr[data-value]');
  if (!line) return;
  state.light = { param: state.effectsParam, value: line.dataset.value };
  renderLight();
  draw();
});

/* ---------------------------------------------------------- KPI analysis */

function openAnalysis(n) {
  const row = state.rows.find((r) => r.n === n);
  if (!row || !row.kpi) return;
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const body = $('analysis-body');
  body.textContent = '';
  // the parameters on top, each name over its value
  const params = Object.entries(row.params || {});
  if (params.length) {
    const dl = el('dl', undefined, 'run-params');
    for (const [k, v] of params) {
      const pair = el('div');
      pair.append(el('dt', k), el('dd', paramValue(k, v)));
      dl.append(pair);
    }
    body.append(dl);
  }
  body.append(...analysisNodes(row));
  runHead('analisi KPI', n);
  $('analysis-dialog').showModal();
}

/*
 * Where price went after the run's entries, against random entries at the
 * same UTC hour and side (scripts/entry_excursions.py, which prints the same;
 * api/sweeps/<id>/<n>/excursions). One N on show at a time, its buttons on top;
 * the box asks for other N.
 */
async function openEntries(n, bars = '') {
  if (!state.job || !state.job.id) return;
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const body = $('analysis-body');
  body.textContent = 'reading the M5 under the run\u2026 a long run takes up to a minute, once';
  runHead('entries', n);
  if (!$('analysis-dialog').open) $('analysis-dialog').showModal();
  let r;
  try {
    r = await ask(`api/sweeps/${state.job.id}/${n}/excursions` + (bars ? '?bars=' + encodeURIComponent(bars) : ''));
  } catch (error) { body.textContent = String(error.message || error); return; }
  body.textContent = '';
  const c = r.coherence || {};
  const checked = ['target', 'stop'].filter((k) => c[k] && c[k][0]).map((k) => `${k} ${c[k][1]}/${c[k][0]}`).join(', ');
  body.append(
    el('p', `${r.strategy} on ${r.instrument} ${r.granularity} \u00b7 ${r.trades} trades`
      + (checked ? ` \u00b7 the ledger's exits touched on the M5: ${checked}` : '')),
    el('p', 'From each fill, on the side that closes the trade: MFE is how far price went its way, MAE how far'
      + ' against, close where it stands after N bars. random: the same from bars at the same UTC hour and side,'
      + ' with a stop as many ATR wide. E[R]: the result per trade with the stop at 1R and the target at k R,'
      + ' or the close after N bars when neither is touched; an M5 touching both counts as the stop. Spread included.', 'hint'));
  const pick = el('div');
  pick.id = 'entries-bars';
  const view = el('div');
  const signed = (v) => v === null || v === undefined ? 'n/a'
    : (v > 0 ? '+' : v < 0 ? '\u2212' : '') + Math.abs(v).toFixed(2);
  const share = (v) => v === null || v === undefined ? 'n/a' : (100 * v).toFixed(0) + '%';
  const tone = (v) => 'num' + (v > 0 ? ' good' : v < 0 ? ' bad' : '');
  const table = (head, rows) => {
    const t = el('table');
    const tr = t.createTHead().insertRow();
    for (const h of head) tr.append(el('th', h));
    const tb = t.createTBody();
    for (const cells of rows) {
      const line = tb.insertRow();
      for (const [text, cls] of cells) line.append(el('td', text, cls));
    }
    return t;
  };
  const show = (x) => {
    for (const b of pick.querySelectorAll('button')) b.setAttribute('aria-pressed', String(Number(b.dataset.n) === x.N));
    view.textContent = '';
    if (!x.trades) { view.append(el('p', `no trade has ${x.N} bars after its entry`)); return; }
    view.append(el('h3', `after ${x.N} ${r.granularity} bars \u00b7 ${x.trades} entries, ${x.random} random`));
    view.append(table(['', 'MFE q10', 'MFE q50', 'MFE q90', 'MAE q10', 'MAE q50', 'MAE q90', 'close, mean', 'close > 0'],
      x.stats.map((s) => [[`${s.who === 'strategia' ? 'strategy' : 'random'} \u00b7 ${s.unit}`],
        ...s.mfe.map((v) => [signed(v), 'num']), ...s.mae.map((v) => [signed(v), 'num']),
        [signed(s.close), tone(s.close)], [share(s.up), 'num']])));
    view.append(el('h3', `stop at 1R, target at k R, else closed after ${x.N} bars`));
    view.append(table(['target', 'P(target)', 'P(stop)', 'E[R]', '95% CI', 'random E[R]', 'edge', 'E[R] A', 'E[R] B'],
      x.grid.map((g) => [[`${g.k}R`], [share(g.target), 'num'], [share(g.stop), 'num'], [signed(g.e), tone(g.e)],
        [`${signed(g.lo)} .. ${signed(g.hi)}`, 'num'], [signed(g.base), tone(g.base)], [signed(g.edge), tone(g.edge)],
        [signed(g.a), tone(g.a)], [signed(g.b), tone(g.b)]])));
    view.append(el('p', `edge = E[R] less the random one. A and B: the entries up to ${stamp(x.half)} and after;`
      + ' the CI resamples blocks of 10 entries in a row.', 'hint'));
  };
  for (const x of r.bars) {
    const b = el('button', `N ${x.N}`);
    b.type = 'button';
    b.dataset.n = x.N;
    b.addEventListener('click', () => show(x));
    pick.append(b);
  }
  const box = el('input');
  box.type = 'text';
  box.placeholder = r.bars.map((x) => x.N).join(',');
  box.value = bars;
  box.title = 'other N, comma separated, up to 8';
  const again = el('button', 'measure');
  again.type = 'button';
  again.dataset.icon = 'entries';
  again.addEventListener('click', () => openEntries(n, box.value.trim()).catch((e) => message(String(e.message || e))));
  pick.append(box, again);
  body.append(pick, view);
  if (r.bars.length) show(r.bars[0]);
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
  // how many, in the title: empty until the list is in
  $('sets-count').textContent = '';
  if (!$('sets-dialog').open) $('sets-dialog').showModal();
  const { sweeps } = await ask('api/sweeps');
  $('sets-count').textContent = sweeps.length;
  const stopped = sweeps.filter((set) => set.stopped).length;
  $('sets-drop-stopped').hidden = !stopped;
  $('sets-drop-stopped').textContent = `delete stopped (${stopped})`;
  if (!sweeps.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 13;
    cell.textContent = 'no set saved yet: one is kept every time a simulation ends';
    return;
  }
  for (const set of sweeps) {
    const row = body.insertRow();
    row.dataset.id = set.id;
    const cells = [set.id + (set.origin ? ` (from ${set.origin})` : ''), fullStamp(set.saved), null, set.strategy, set.instrument,
      set.granularity, set.from, set.to, (set.varied || []).join(', '),
      `${set.runs}${set.runs < set.total ? ' of ' + set.total : ''}${set.stopped ? ' (stopped)' : ''}`,
      set.best === null || set.best === undefined ? '' : amount(set.best),
      set.bestScore === null || set.bestScore === undefined ? '' : set.bestScore.toFixed(1)];
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
    rerun.dataset.icon = 'rerun';
    rerun.textContent = 'rerun';
    rerun.title = 'run the same set again on the code and data there are now; the old one is kept';
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.className = 'set-delete';
    drop.dataset.icon = 'delete';
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
  keepAddress();
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
// every set stopped before its last run, after one question
$('sets-drop-stopped').addEventListener('click', async () => {
  try {
    const stopped = (await ask('api/sweeps')).sweeps.filter((set) => set.stopped);
    if (!stopped.length) return;
    if (!await askUser(`Delete the ${stopped.length} stopped set${stopped.length === 1 ? '' : 's'}? It cannot be undone.`, 'delete')) return;
    for (const set of stopped) await post('api/sweeps/' + set.id, { delete: true });
    await openSets();
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
  // both asked at once, and the sweep shown first: it is what the page is
  // opened to see - one going on without anybody watching - and the stores
  // take seconds to answer while it runs, the sweep status milliseconds
  const stores = ask('api/stores');
  // the address says which set was on show and how it was sorted, when the
  // page is come back to from a run: that one, else the last one run
  const address = new URLSearchParams(location.search);
  for (const [key, name] of [['sort', 'sort'], ['kpi', 'kpiSort']]) {
    // a column is itself 's:net', so the direction is after the last colon
    const text = address.get(key) || '', cut = text.lastIndexOf(':');
    if (cut > 0) state[name] = { col: text.slice(0, cut), dir: Number(text.slice(cut + 1)) || 1 };
  }
  let job = await ask('api/sweep?since=0')
    .catch((error) => { message(String(error.message || error)); return { total: 0 }; });
  const wanted = address.get('set');
  if (wanted && job.id !== wanted) job = await ask('api/sweeps/' + wanted).catch(() => job);
  if (job.total) {
    state.fields = job.fields || null;
    follow(job);
  }
  const s = await stores;
  state.instruments = s.instruments || [];
  state.forms = s.params || {};
  state.defaults = s.defaults || {};
  state.descriptions = s.descriptions || {};
  if (s.equity !== undefined && s.equity !== null) $('balance').value = s.equity;
  if (s.leverage) $('leverage').value = s.leverage;
  fill($('strategy'), s.strategies || []);
  fill($('instrument'), state.instruments.map((r) => r.instrument));
  onStrategy();
  // the form of the sweep going, or of the last one to finish
  if (job.total) fillForm({ ...(job.fields || {}), ...(job.grid || {}) });
  countLater();
  // not awaited: the stars can wait, the table cannot wait on them
  loadFavourites().catch((error) => message(String(error.message || error)));
}

start().catch((error) => message(String(error.message || error)));
