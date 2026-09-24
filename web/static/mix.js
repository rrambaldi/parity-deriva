/*
 * A mix: runs of the saved sets traded side by side, each on its own
 * capital, and the capital of the whole - theirs added up at every close, on
 * one time axis. The service adds them up (web/service.py mix); this page
 * picks the runs, keeps the list (api/mixes) and draws.
 *
 * Times are epoch milliseconds for a naive UTC instant, as in sim.js.
 */

const AXIS = { left: 92, right: 14, top: 12, bottom: 22 };
// a colour a run; the mix is the page's blue, over them
const COLOURS = ['#e8a33d', '#3fb68b', '#c8a2ff', '#ff9f45', '#e2555a', '#f2f2f2', '#6fd3d3', '#d38fbf'];
const MIX = '#58a6ff';

const state = {
  mixes: [],     // the saved ones, from api/mixes
  mix: null,     // the one on show: {id, name, items}, id '' until kept
  drawn: null,   // what api/mixes/<id> made of it
  pick: null,    // the run under the cursor in the table, by its index
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
  .map(([k, v]) => `${k}=${v === '' ? 'none' : v}`).join(' ');
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
  $('mix-name').value = state.mix.name;
  $('mix-delete').hidden = !state.mix.id;
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
    { id: state.mix.id || undefined, name: $('mix-name').value, items });
  state.mixes = mixes;
  await openMix(mix.id);
}

$('mix-pick').addEventListener('change', () => openMix($('mix-pick').value).catch(fail));
$('mix-name').addEventListener('change', () => {
  state.mix.name = $('mix-name').value;
  if (state.mix.id) keep(state.mix.items).catch(fail);
});
$('mix-controls').addEventListener('submit', (event) => event.preventDefault());
$('mix-delete').addEventListener('click', async () => {
  if (!confirm(`Delete the mix ${state.mix.name || state.mix.id}? Its sets and their runs stay.`)) return;
  try {
    state.mixes = (await post(`api/mixes/${state.mix.id}`, { delete: true })).mixes;
    await openMix(state.mixes.length ? state.mixes[0].id : '');
  } catch (error) { fail(error); }
});

/* ------------------------------------------------------ adding a run */

async function loadSets() {
  const box = $('add-set');
  box.textContent = '';
  for (const s of (await ask('api/sweeps')).sweeps) {
    box.add(new Option(`${s.name || s.id} · ${s.strategy} ${s.instrument} ${s.granularity}`
      + ` · ${s.from} .. ${s.to} · ${s.runs} runs`, s.id));
  }
  await pickSet();
}

// the runs of the set picked, with the values that vary in it
async function pickSet() {
  const id = $('add-set').value;
  const box = $('add-run');
  box.textContent = '';
  if (!id) return;
  const set = await ask('api/sweeps/' + id);
  if ($('add-set').value !== id) return;   // another set picked meanwhile
  const varied = set.varied || [];
  for (const row of set.done) {
    if (row.error) continue;
    const params = Object.fromEntries(varied.map((k) => [k, row.params[k]]));
    box.add(new Option(`#${row.n} ${paramsText(params)} · capital ${amount(row.final)}`, row.n));
  }
}

$('add-set').addEventListener('change', () => pickSet().catch(fail));
$('mix-add').addEventListener('submit', (event) => {
  event.preventDefault();
  const sweep = $('add-set').value, n = Number($('add-run').value);
  if (!sweep || !n) return;
  if (state.mix.items.some((i) => i.sweep === sweep && i.n === n)) {
    message('that run is in the mix already', 'info');
    return;
  }
  keep([...state.mix.items, { sweep, n }]).catch(fail);
});

/* ------------------------------------------------------------ the table */

// where the run page draws one run: the set and number find it on disk
function runAddress(run) {
  const fields = Object.fromEntries(Object.entries(run.fields).filter(([k, v]) =>
    v !== null && v !== undefined && v !== ''
    && !(['intraday', 'inverse', 'trailProfit'].includes(k) && v === '0')));
  return 'run?' + new URLSearchParams({ sweep: run.sweep, run: run.n, ...fields });
}

function render() {
  const d = state.drawn;
  const runs = d ? d.runs : [];
  const t = d && d.total;
  $('mix-title').textContent = !runs.length ? 'an empty mix: pick a set and a run above, and add it'
    : !t ? `${state.mix.name || 'mix'} · none of its runs can be read`
    : `${state.mix.name || 'mix'} · ${runs.length} run${runs.length === 1 ? '' : 's'}`
      + ` · ${stamp(t.from)} .. ${stamp(t.to)} · capital ${amount(t.start)} → ${amount(t.final)}`
      + ` · max drawdown ${amount(t.maxDrawdown)}`;
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
    swatch.innerHTML = '<span class="k"></span>';
    swatch.firstChild.style.color = COLOURS[i % COLOURS.length];
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
    cell(line, '').innerHTML = '<span class="k"></span>';
    line.cells[0].firstChild.style.color = MIX;
    cell(line, 'the mix');
    cell(line, '');
    cell(line, '');
    cell(line, 'every run on its own capital, added up');
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
    const key = (text, colour) => {
      const span = document.createElement('span');
      span.className = 'k';
      span.style.color = colour;
      span.textContent = text;
      legend.append(span);
    };
    key('the mix: capital', MIX);
    runs.forEach((run, i) => { if (!run.error) key(`${run.sweep}/${run.n}`, COLOURS[i % COLOURS.length]); });
    legend.append('each run: its own profit, drawn from the mix\'s start');
  }
  draw();
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
  const t = state.drawn && state.drawn.total;
  if (!t) return;
  const { from, to, start } = t;
  const mix = [[from, start]].concat(t.curve);
  // each run as its own profit on the mix's start, so they share its axis
  const parts = state.drawn.runs.map((run, i) => [run, i]).filter(([run]) => !run.error)
    .map(([run, i]) => [i, run, [[from, start]].concat(run.curve.map(([ms, b]) => [ms, start + b - run.summary.start]))]);
  let low = Infinity, high = -Infinity;
  for (const points of [mix, ...parts.map((p) => p[2])]) for (const [, v] of points) {
    low = Math.min(low, v);
    high = Math.max(high, v);
  }
  if (high === low) { high += 0.5; low -= 0.5; }
  const pad = (high - low) * 0.06;
  high += pad; low -= pad;
  const plotW = width - AXIS.left - AXIS.right, plotH = height - AXIS.top - AXIS.bottom;
  const x = (ms) => AXIS.left + (ms - from) / Math.max(1, to - from) * plotW;
  const y = (v) => AXIS.top + (high - v) / (high - low) * plotH;
  geometry = { from, to, plotW, mix, parts };

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
  ctx.strokeStyle = '#3a414d';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(AXIS.left, y(start)); ctx.lineTo(width - AXIS.right, y(start)); ctx.stroke();
  ctx.setLineDash([]);

  // a step: a balance moves when a trade closes and not in between
  const line = (points, colour, widthPx) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = widthPx;
    ctx.beginPath();
    points.forEach(([ms, v], i) => {
      if (i === 0) ctx.moveTo(x(ms), y(v));
      else { ctx.lineTo(x(ms), y(points[i - 1][1])); ctx.lineTo(x(ms), y(v)); }
    });
    ctx.lineTo(x(to), y(points[points.length - 1][1]));
    ctx.stroke();
  };
  ctx.globalAlpha = state.pick === null ? 0.7 : 0.25;
  for (const [i, , points] of parts) if (i !== state.pick) line(points, COLOURS[i % COLOURS.length], 1);
  ctx.globalAlpha = 1;
  const picked = parts.find(([i]) => i === state.pick);
  if (picked) line(picked[2], COLOURS[picked[0] % COLOURS.length], 2);
  line(mix, MIX, 2.5);
}

// what the mix and each run held at the cursor's time, each on its own capital
canvas.addEventListener('mousemove', (event) => {
  if (!geometry) return;
  const { from, to, plotW, mix, parts } = geometry;
  const ms = from + (event.clientX - canvas.getBoundingClientRect().left - AXIS.left) / plotW * (to - from);
  $('mix-readout').textContent = `${stamp(ms)} · mix ${amount(balanceAt(mix, ms))}`
    + parts.map(([, run]) => ` · ${run.sweep}/${run.n} ${amount(balanceAt(
      [[from, run.summary.start]].concat(run.curve), ms))}`).join('');
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
