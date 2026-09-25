/*
 * A mix: runs of the saved sets on one account of the default capital -
 * their profits added up at every close, on one time axis, and the margin
 * the account needed for all of them. Or the runs traded together on it,
 * each sized on the shared capital (simulate together). The service does the
 * sums (web/service.py mix, mixTogether); this page picks the runs, keeps
 * the list (api/mixes), draws and reads the whole.
 *
 * Times are epoch milliseconds for a naive UTC instant, as in sim.js.
 */

const AXIS = { left: 92, right: 14, top: 12, bottom: 22 };
// a run is told apart by its dash, not a colour of its own: the data has
// three colours and a fourth does not pass (web/DESIGN.md § 3)

const state = {
  mixes: [],     // the saved ones, from api/mixes
  mix: null,     // the one on show: {id, name, items}, id '' until kept
  drawn: null,   // what api/mixes/<id> made of it
  pick: null,    // the run under the cursor in the table, by its index
  view: 'summed',   // or 'together', once simulated
  together: null,   // what api/mixes/<id>/together made of it
  sets: [],      // the saved simulations' summaries, from api/sweeps
  set: null,     // the one picked to add from, from api/sweeps/<id>
};

const $ = (id) => document.getElementById(id);
const canvas = $('mix-equity');
const ctx = canvas.getContext('2d');

function stamp(ms) {
  if (ms === null || ms === undefined) return '';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}
function amount(v) {
  if (v === null || v === undefined) return '';
  return Math.abs(v) >= 1 || v === 0 ? v.toFixed(2) : v.toPrecision(3);
}
const pct = (v) => v === null || v === undefined ? 'n/a' : v.toFixed(1) + '%';
const paramsText = (params) => Object.entries(params || {})
  .map(([k, v]) => `${k}=${paramValue(k, v)}`).join(' ');
const fail = (error) => message(String(error.message || error));

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

/* ------------------------------------------------------------ the mixes */

function fillMixes() {
  const pick = $('mix-pick');
  pick.textContent = '';
  for (const m of state.mixes) {
    pick.add(new Option(`${m.name || m.id} · ${m.items.length} run${m.items.length === 1 ? '' : 's'}`, m.id));
  }
  pick.add(new Option('new mix', ''));
  pick.value = state.mix.id;
}

async function openMix(id) {
  state.mix = state.mixes.find((m) => m.id === id) || { id: '', name: '', items: [] };
  state.pick = null;
  state.together = null;
  state.view = 'summed';
  $('mix-name').value = state.mix.name;
  $('mix-leverage').value = state.mix.leverage || '';
  $('mix-delete').hidden = !state.mix.id;
  $('mix-together').hidden = !state.mix.id;
  // the address says which, so a reload and the back button from a run come here
  history.replaceState(null, '', state.mix.id ? '?id=' + state.mix.id : location.pathname);
  fillMixes();
  state.drawn = state.mix.id ? await ask('api/mixes/' + state.mix.id) : null;
  message('');
  render();
}

// every change is kept at once; a new mix is made by its first run
async function keep(items) {
  const { mix, mixes } = await post('api/mixes',
    { id: state.mix.id || undefined, name: $('mix-name').value,
      leverage: $('mix-leverage').value || undefined, items });
  state.mixes = mixes;
  await openMix(mix.id);
}

$('mix-pick').addEventListener('change', () => openMix($('mix-pick').value).catch(fail));
$('mix-name').addEventListener('change', () => {
  state.mix.name = $('mix-name').value;
  if (state.mix.id) keep(state.mix.items).catch(fail);
});
$('mix-leverage').addEventListener('change', () => {
  if (state.mix.id) keep(state.mix.items).catch(fail);
});
$('mix-controls').addEventListener('submit', (event) => event.preventDefault());

// every run on one account at once: straight away when their trades are all
// saved, else after the runs with none are run again (api/mixes/together)
$('mix-together').addEventListener('click', async () => {
  const button = $('mix-together');
  button.disabled = true;
  try {
    let job = await post(`api/mixes/${state.mix.id}/together`);
    while (job.running) {
      const at = job.progress && job.progress.at ? ` · ${stamp(job.progress.at)}` : '';
      message(`running the runs with no saved trades: ${job.done} of ${job.total} done${at}`, 'info');
      await new Promise((resolve) => setTimeout(resolve, 1000));
      job = await ask('api/mixes/together');
    }
    if (job.error) throw new Error(job.error);
    if (job.mix !== state.mix.id) return;   // another mix opened meanwhile
    // the runs just run have their trades now: the summed mix has its margin too
    if (job.total) state.drawn = await ask('api/mixes/' + state.mix.id);
    state.together = job.result;
    state.view = 'together';
    message('');
    render();
  } catch (error) { fail(error); }
  finally { button.disabled = false; }
});
$('mix-views').addEventListener('click', (event) => {
  const view = event.target.dataset && event.target.dataset.view;
  if (!view || (view === 'together' && !state.together)) return;
  state.view = view;
  render();
});
$('mix-delete').addEventListener('click', async () => {
  if (!confirm(`Delete the mix ${state.mix.name || state.mix.id}? Its sets and their runs stay.`)) return;
  try {
    state.mixes = (await post(`api/mixes/${state.mix.id}`, { delete: true })).mixes;
    await openMix(state.mixes.length ? state.mixes[0].id : '');
  } catch (error) { fail(error); }
});

/* ------------------------------------------------------ adding a run */

// the saved simulations by strategy: pick one, then one of its simulations
async function loadSets() {
  state.sets = (await ask('api/sweeps')).sweeps;
  const box = $('add-strategy');
  box.textContent = '';
  for (const name of [...new Set(state.sets.map((s) => s.strategy))].sort()) box.add(new Option(name, name));
  await pickStrategy();
}

async function pickStrategy() {
  const box = $('add-set');
  box.textContent = '';
  for (const s of state.sets.filter((s) => s.strategy === $('add-strategy').value)) {
    box.add(new Option(`${s.name || s.id} · ${s.instrument} ${s.granularity}`
      + ` · ${s.from} .. ${s.to} · ${s.runs} runs`, s.id));
  }
  await pickSet();
}

const scoreOf = (row) => row.kpi && typeof row.kpi.score === 'number' ? row.kpi.score : null;
const variedOf = (row) => Object.fromEntries((state.set.varied || []).map((k) => [k, row.params[k]]));

async function pickSet() {
  const id = $('add-set').value;
  state.set = null;
  if (id) {
    const set = await ask('api/sweeps/' + id);
    if ($('add-set').value !== id) return;   // another set picked meanwhile
    state.set = set;
  }
  renderRuns();
}

function ranked() {
  return state.set.done.filter((row) => !row.error)
    .sort((a, b) => (scoreOf(b) ?? -1) - (scoreOf(a) ?? -1) || a.n - b.n);
}

// a viewer plugin declares no opening capital: it is the last one less the net
// (curveOf in sim.js)
const startOf = (row) => row.balance !== null && row.balance !== undefined ? row.balance
  : row.final - ((row.report && row.report.net) || 0);

// a run's capital as a step line, on the time axis every run of the set shares
function spark(row, from, to) {
  const W = 120, H = 24;
  // by the close, which is when the balance moves; the trades come by entry
  const points = [[from, startOf(row)]].concat((row.curve || []).slice().sort((a, b) => a[0] - b[0]));
  const values = points.map((p) => p[1]);
  const low = Math.min(...values), high = Math.max(...values);
  const x = (ms) => ((ms - from) / Math.max(1, to - from) * W).toFixed(1);
  const y = (v) => (high === low ? H / 2 : 1 + (high - v) / (high - low) * (H - 2)).toFixed(1);
  let d = `M${x(from)} ${y(points[0][1])}`;
  for (let i = 1; i < points.length; i++) d += `H${x(points[i][0])}V${y(points[i][1])}`;
  d += `H${W}`;
  return `<svg class="spark" width="${W}" height="${H}" aria-hidden="true">`
    + `<path d="${d}" fill="none" stroke="currentColor" stroke-width="1.25"/></svg>`;
}

// every run of the set picked, with its capital and its curve: add or remove
function renderRuns() {
  const body = $('runs-rows');
  body.textContent = '';
  const set = state.set;
  $('runs-panel').hidden = !set;
  if (!set) return;
  const rows = ranked();
  $('runs-title').textContent = `runs of ${set.name || set.id} · ${rows.length} · the best score first`;
  const times = rows.flatMap((row) => (row.curve || []).map((p) => p[0]));
  const fields = set.fields || {};
  const from = Math.min(...times, Date.parse(fields.from) || Infinity);
  const to = Math.max(...times, Date.parse(fields.to) || -Infinity);
  const items = state.mix ? state.mix.items : [];   // the sets may come before the mix
  const fixed = (v, digits) => v === null || v === undefined ? 'n/a' : v.toFixed(digits);
  for (const row of rows) {
    const k = row.kpi || {};
    const net = row.final - startOf(row);
    const inMix = items.some((i) => i.sweep === set.id && i.n === row.n);
    const line = body.insertRow();
    line.dataset.n = row.n;
    if (inMix) line.className = 'selected';
    const texts = [`#${row.n}`, paramsText(variedOf(row)), fixed(scoreOf(row), 1),
      String((row.report || {}).closedTrades ?? ''), pct(k.roi), fixed(k.profitFactor, 2),
      pct(k.maxDrawdownPct), fixed(k.sharpe, 2), amount(row.final), amount(net)];
    texts.forEach((text, i) => {
      const td = line.insertCell();
      td.textContent = text;
      if (i >= 2) td.className = 'num';
      if (i === 9) td.className += net > 0 ? ' good' : net < 0 ? ' bad' : '';
    });
    line.insertCell().innerHTML = times.length ? spark(row, from, to) : '';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = inMix ? 'remove' : 'add';
    button.title = inMix ? 'take it out of the mix' : 'add it to the mix';
    const actions = line.insertCell();
    actions.className = 'run-actions';
    actions.append(button);
  }
}

$('runs-rows').addEventListener('click', (event) => {
  const line = event.target.closest('tr[data-n]');
  if (!line || event.target.tagName !== 'BUTTON') return;
  const sweep = state.set.id, n = Number(line.dataset.n);
  const others = state.mix.items.filter((i) => !(i.sweep === sweep && i.n === n));
  keep(others.length < state.mix.items.length ? others : [...state.mix.items, { sweep, n }]).catch(fail);
});
$('add-strategy').addEventListener('change', () => pickStrategy().catch(fail));
$('add-set').addEventListener('change', () => pickSet().catch(fail));
$('mix-add').addEventListener('submit', (event) => event.preventDefault());

/* ------------------------------------------------------------ the table */

// where the run page draws one run: the set and number find it on disk
function runAddress(run) {
  const fields = Object.fromEntries(Object.entries(run.fields).filter(([k, v]) =>
    v !== null && v !== undefined && v !== ''
    && !(['intraday', 'inverse', 'trailProfit'].includes(k) && v === '0')));
  return 'run?' + new URLSearchParams({ sweep: run.sweep, run: run.n, ...fields });
}

// what the page shows: the summed mix, or the runs traded together. Each
// part is [index in the table, the run, its curve, its opening capital]
function view() {
  const d = state.drawn;
  if (!d || !d.total) return null;
  if (state.view === 'together' && state.together) {
    const t = state.together;
    return { total: t.total, analysis: t.analysis, together: true,
      parts: t.runs.map((p) => [d.runs.findIndex((r) => r.sweep === p.sweep && r.n === p.n), p, p.curve, p.start]) };
  }
  return { total: d.total, analysis: d.analysis, together: false,
    parts: d.runs.map((run, i) => [i, run]).filter(([, run]) => !run.error && run.summary.start !== null)
      .map(([i, run]) => [i, run, run.curve, run.summary.start]) };
}

function render() {
  const d = state.drawn;
  const runs = d ? d.runs : [];
  const v = view();
  const t = v && v.total;
  $('mix-title').textContent = !runs.length ? 'an empty mix: pick a strategy and a simulation above, and add its runs'
    : !t ? `${state.mix.name || 'mix'} · none of its runs can be read`
    : `${state.mix.name || 'mix'} · ${runs.length} run${runs.length === 1 ? '' : 's'}`
      + ` · ${stamp(t.from)} .. ${stamp(t.to)} · capital ${amount(t.start)} → ${amount(t.final)}`
      + ` · max drawdown ${amount(t.maxDrawdown)}`;
  $('mix-views').hidden = !state.together;
  for (const button of $('mix-views').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.view === (v && v.together ? 'together' : 'summed')));
  }
  $('mix-margin').textContent = !t ? ''
    : t.margin ? marginText(t.margin) + (v.together && state.together.refused
      ? ` · ${state.together.refused} trade${state.together.refused === 1 ? '' : 's'} refused for want of margin` : '')
    : `margin: n/a, ${d.missing.length} run${d.missing.length === 1 ? ' has' : 's have'} no saved trades`
      + ' - simulate together runs them again';
  $('mix-margin').className = 'hint' + (t && t.margin && !t.margin.ok ? ' bad' : '');
  const body = $('mix-rows');
  body.textContent = '';
  const cell = (line, text, cls) => {
    const td = line.insertCell();
    td.textContent = text;
    if (cls) td.className = cls;
    return td;
  };
  const money = (line, s) => {
    const net = s.final - s.start;
    cell(line, String(s.trades ?? ''), 'num');
    cell(line, s.winRate === null || s.winRate === undefined ? 'n/a' : pct(s.winRate * 100), 'num');
    cell(line, amount(s.start), 'num');
    cell(line, amount(s.final), 'num');
    cell(line, amount(net), 'num' + (net > 0 ? ' good' : net < 0 ? ' bad' : ''));
  };
  runs.forEach((run, i) => {
    const line = body.insertRow();
    line.dataset.i = i;
    if (i === state.pick) line.className = 'selected';
    const swatch = cell(line, '');
    swatch.innerHTML = `<span class="k series">${dashSample(DASHES[i % DASHES.length])}</span>`;
    cell(line, `${run.sweep}/${run.n}` + (run.name ? ` · ${run.name}` : ''));
    if (run.error) {
      cell(line, run.error, 'bad').colSpan = 12;
    } else {
      const s = run.summary;
      cell(line, run.fields.strategy + (run.fields.inverse === '1' ? ' inverse' : ''));
      cell(line, `${s.instrument} ${s.granularity}`);
      cell(line, paramsText(Object.fromEntries((run.varied || []).map((k) => [k, run.params[k]]))));
      cell(line, `${stamp(s.from)} .. ${stamp(s.to)}`);
      money(line, s);
      cell(line, pct(s.roi), 'num');
      cell(line, pct(s.maxDrawdownPct), 'num');
      cell(line, s.sharpe === null || s.sharpe === undefined ? 'n/a' : s.sharpe.toFixed(2), 'num');
    }
    const actions = line.insertCell();
    actions.className = 'run-actions';
    for (const [name, title] of [['view', 'this run in full, on its own page'],
                                 ['remove', 'take it out of the mix']]) {
      if (name === 'view' && run.error) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.action = name;
      button.textContent = name;
      button.title = title;
      actions.append(button, ' ');
    }
  });
  if (t) {
    const line = body.insertRow();
    line.className = 'mix-total';
    cell(line, '').innerHTML = `<span class="k series mix-total">${dashSample(DASHES[0])}</span>`;
    cell(line, v.together ? 'the mix, together' : 'the mix');
    cell(line, '');
    cell(line, '');
    cell(line, v.together ? 'every run on the one capital, sized on it' : 'every run\'s profit on the one capital');
    cell(line, `${stamp(t.from)} .. ${stamp(t.to)}`);
    money(line, t);
    cell(line, pct(t.kpi.roi), 'num');
    cell(line, pct(t.kpi.maxDrawdownPct), 'num');
    cell(line, t.kpi.sharpe === null || t.kpi.sharpe === undefined ? 'n/a' : t.kpi.sharpe.toFixed(2), 'num');
    cell(line, '');
  }
  const legend = $('mix-legend');
  legend.textContent = '';
  if (t) {
    const key = (text, dash, extra) => {
      const span = document.createElement('span');
      span.className = 'k series' + (extra ? ' ' + extra : '');
      span.innerHTML = dashSample(dash);
      span.append(text);
      legend.append(span);
    };
    key('the mix: capital', DASHES[0], 'mix-total');
    for (const [i, run] of v.parts) key(`${run.sweep}/${run.n}`, DASHES[i % DASHES.length]);
    legend.append('each run: its own profit, drawn from the mix\'s start');
  }
  renderAnalysis(v);
  renderRuns();
  draw();
}

// the mix read as a whole: its KPIs in words, its runs' parts, how they move together
function renderAnalysis(v) {
  $('mix-analysis').hidden = !v;
  if (!v) return;
  const t = v.total, a = v.analysis;
  $('mix-analysis-view').textContent = v.together ? 'the runs traded together' : 'the runs\' profits added up';
  const warn = $('mix-warning');
  warn.hidden = a.currencies.length < 2;
  warn.textContent = `the runs are in ${a.currencies.join(' and ')}: their profits are added up as they are, not converted`;
  $('mix-kpi').replaceChildren(...analysisNodes({ kpi: t.kpi, report: { closedTrades: t.trades }, margin: t.margin }));
  const signed = (x) => x === null || x === undefined ? 'n/a' : (x < 0 ? '\u2212' : '+') + Math.abs(x).toFixed(2);
  const rows = $('mix-parts-rows');
  rows.textContent = '';
  for (const r of a.runs) {
    const line = rows.insertRow();
    const texts = [`${r.sweep}/${r.n}`, (r.net < 0 ? '\u2212' : '+') + amount(Math.abs(r.net)),
      r.share === null ? 'n/a' : pct(r.share), amount(r.maxDrawdown), signed(r.withRest),
      v.together ? String(r.refused ?? 0) : ''];
    texts.forEach((text, i) => {
      const td = line.insertCell();
      td.textContent = text;
      if (i) td.className = 'num' + (i === 1 ? (r.net > 0 ? ' good' : r.net < 0 ? ' bad' : '') : '');
    });
  }
  $('mix-diversification').textContent = a.diversification === null ? ''
    : `max drawdown of the mix ${amount(t.maxDrawdown)}, against ${amount(a.drawdownAlone)} for the runs' worst falls added up: `
      + (a.diversification > 0 ? `trading them together saved ${pct(a.diversification)} of it`
        : 'their worst falls came together, nothing saved');
  const matrix = $('mix-matrix');
  matrix.textContent = '';
  if (a.runs.length < 2) return;
  // a run by the end of its set's id and its number: the full name is on its row
  const tag = (r) => `${r.sweep.slice(-6)}/${r.n}`;
  const head = matrix.createTHead().insertRow();
  head.insertCell();
  for (const r of a.runs) Object.assign(head.insertCell(), { textContent: tag(r), className: 'num', title: `${r.sweep}/${r.n}` });
  const tbody = matrix.createTBody();
  a.matrix.forEach((row, i) => {
    const line = tbody.insertRow();
    Object.assign(line.insertCell(), { textContent: tag(a.runs[i]), title: `${a.runs[i].sweep}/${a.runs[i].n}` });
    row.forEach((x, j) => {
      const td = line.insertCell();
      td.className = 'num';
      // the word says it, not the colour: two that move together spread nothing
      td.textContent = i === j ? '' : signed(x) + (x !== null && Math.abs(x) >= 0.5 ? ' together' : '');
    });
  });
}

$('mix-rows').addEventListener('click', (event) => {
  const line = event.target.closest('tr[data-i]');
  if (!line) return;
  const i = Number(line.dataset.i);
  const run = state.drawn.runs[i];
  if (event.target.dataset.action === 'remove') {
    keep(state.mix.items.filter((item) => !(item.sweep === run.sweep && item.n === run.n))).catch(fail);
  } else if (event.target.dataset.action === 'view') {
    // the runs of this table go with it, for the run page's prev and next
    const runs = state.drawn.runs.filter((r) => !r.error).map(runAddress);
    try { sessionStorage.setItem('run-list', JSON.stringify({ back: location.href, runs })); }
    catch (error) { /* no storage, no prev and next: the run still opens */ }
    location.href = runAddress(run);
  }
});
$('mix-rows').addEventListener('mouseover', (event) => {
  const line = event.target.closest('tr[data-i]');
  const i = line ? Number(line.dataset.i) : null;
  if (i !== state.pick) { state.pick = i; draw(); }
});
$('mix-rows').addEventListener('mouseleave', () => { state.pick = null; draw(); });

/* ------------------------------------------------------------ the chart */

let geometry = null;   // what the last draw mapped, for the readout

function balanceAt(points, ms) {
  let v = points[0][1];
  for (const [t, b] of points) { if (t > ms) break; v = b; }
  return v;
}

function draw() {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = 340;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = height + 'px';
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  geometry = null;
  const v = view();
  if (!v) return;
  const t = v.total;
  const { from, to, start } = t;
  const mix = [[from, start]].concat(t.curve);
  // each run as its own profit on the mix's start, so they share its axis
  const parts = v.parts.map(([i, run, curve, own]) =>
    [i, run, [[from, start]].concat(curve.map(([ms, b]) => [ms, start + b - own])), own, curve]);
  let low = Infinity, high = -Infinity;
  for (const points of [mix, ...parts.map((p) => p[2])]) for (const [, value] of points) {
    low = Math.min(low, value);
    high = Math.max(high, value);
  }
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.06;
  high += pad; low -= pad;
  const plotW = width - AXIS.left - AXIS.right, plotH = height - AXIS.top - AXIS.bottom;
  const x = (ms) => AXIS.left + (ms - from) / Math.max(1, to - from) * plotW;
  const y = (value) => AXIS.top + (high - value) / (high - low) * plotH;
  geometry = { from, to, plotW, mix, parts };

  const p = palette();
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
  ctx.strokeStyle = p.border;
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(AXIS.left, y(start)); ctx.lineTo(width - AXIS.right, y(start)); ctx.stroke();
  ctx.setLineDash([]);

  // a step: a balance moves when a trade closes and not in between; a run's
  // dash (not its colour) says which one it is - the mix's own curve is solid
  const line = (points, colour, widthPx, dash = []) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = widthPx;
    ctx.setLineDash(dash);
    ctx.beginPath();
    points.forEach(([ms, v], i) => {
      if (i === 0) ctx.moveTo(x(ms), y(v));
      else { ctx.lineTo(x(ms), y(points[i - 1][1])); ctx.lineTo(x(ms), y(v)); }
    });
    ctx.lineTo(x(to), y(points[points.length - 1][1]));
    ctx.stroke();
    ctx.setLineDash([]);
  };
  ctx.globalAlpha = state.pick === null ? 0.7 : 0.25;
  for (const [i, , points] of parts) if (i !== state.pick) line(points, p.text3, 1, DASHES[i % DASHES.length]);
  ctx.globalAlpha = 1;
  const picked = parts.find(([i]) => i === state.pick);
  if (picked) line(picked[2], p.entry, 2, DASHES[picked[0] % DASHES.length]);
  line(mix, p.text, 2);
}

// what the mix and each run held at the cursor's time, each on its own capital
canvas.addEventListener('mousemove', (event) => {
  if (!geometry) return;
  const { from, to, plotW, mix, parts } = geometry;
  const ms = from + (event.clientX - canvas.getBoundingClientRect().left - AXIS.left) / plotW * (to - from);
  $('mix-readout').textContent = `${stamp(ms)} · mix ${amount(balanceAt(mix, ms))}`
    + parts.map(([, run, , own, curve]) => ` · ${run.sweep}/${run.n} ${amount(balanceAt(
      [[from, own]].concat(curve), ms))}`).join('');
});

window.addEventListener('resize', draw);

/* ---------------------------------------------------------------- start */

async function start() {
  loadSets().catch(fail);
  state.mixes = (await ask('api/mixes')).mixes;
  const wanted = new URLSearchParams(location.search).get('id');
  await openMix(state.mixes.some((m) => m.id === wanted) ? wanted
    : state.mixes.length ? state.mixes[0].id : '');
}

start().catch(fail);
