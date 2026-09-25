/*
 * The live sessions, followed as they trade. Everything here is read off
 * /api/live, which reads each session's own event log: the page asks no
 * broker anything.
 */

const $ = (id) => document.getElementById(id);
const POLL_MS = 3000;
const SKEW_MS = 30000;   // api/live/skew is a pandas query: not every tick
const state = {
  sessions: [], pick: null, detail: null,
  skewTrades: { groups: [] },   // api/live/skew/trades
  skew: {},                     // 'instrument|granularity' -> api/live/skew payload
  skewAt: 0,                    // when the skew was last asked for
  group: null,                  // the group key the chart draws
  candles: { key: null, reference: null, feeds: {} },   // feed -> {provider, account, candles}
  pendingZoom: null,            // a trade clicked in a group the chart had not loaded yet
};

function stamp(ms) {
  if (ms === null || ms === undefined) return '';
  return new Date(ms).toISOString().slice(0, 16).replace('T', ' ');
}
function money(v) {
  return v === null || v === undefined ? '' : Number(v).toFixed(2);
}
function message(text) {
  $('message').textContent = text || '';
  $('message').hidden = !text;
}

async function ask(url) {
  const response = await fetch(url);
  const payload = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok || payload.error) throw new Error(payload.error || response.statusText);
  return payload;
}
async function post(url, body) {
  const response = await fetch(url, { method: 'POST', body: JSON.stringify(body || {}),
                                      headers: { 'X-Parity-Deriva': '1' } });
  const payload = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok || payload.error) throw new Error(payload.error || response.statusText);
  return payload;
}
function askUser(text) {
  $('ask-text').textContent = text;
  return new Promise((resolve) => {
    $('ask-dialog').addEventListener('close', () => resolve($('ask-dialog').returnValue === 'yes'),
                                     { once: true });
    $('ask-dialog').showModal();
  });
}

function status(s) {
  // stopped and still running: closing what it holds (scripts/live.py stopAndClose)
  if (s.running && s.stopped) return ['closing…', ''];
  if (s.running) return ['running', 'good'];
  if (s.exited) return ['exited', 'bad'];
  return ['stopped', ''];
}

// every parameter a form ran with, without the window it was backtested
// on: the strategy's own, a default where the form left one out (the value
// the strategy took, api/stores params), then the options the form set
let STRATEGY_PARAMS = {};   // filled by setupNew
const FORM_SKIP = new Set(['strategy', 'instrument', 'granularity', 'from', 'to', 'balance', 'capital', 'confirmed']);
const given = (v) => v !== '' && v !== null && v !== undefined;

function paramList(fields) {
  const f = fields || {};
  const own = STRATEGY_PARAMS[f.strategy] || [];
  const names = new Set(own.map((p) => p.name));
  const news = given(f.newsBefore) || given(f.newsAfter);   // the impacts mean nothing without a window
  return [
    ...own.map((p) => given(f[p.name]) ? { name: p.name, label: p.label || p.name, value: f[p.name] }
                                      : { name: p.name, label: p.label || p.name, value: p.value, isDefault: true }),
    ...Object.entries(f).filter(([k, v]) => !names.has(k) && !FORM_SKIP.has(k) && given(v)
                                           && (k !== 'newsImpacts' || news))
      .map(([k, v]) => ({ name: k, label: k, value: v })),
  ];
}

function paramsText(fields) {
  return paramList(fields).map((p) => `${p.name}=${paramValue(p.name, p.value)}`).join(' ');
}

function cell(row, text, cls) {
  const td = row.insertCell();
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

// Whoever looks must know at every moment whether real money is moving
// (web/DESIGN.md § 6): the badge in the header is live·real money as soon as
// one session actually trading (s.running, which also covers "closing…":
// it is still open until then) is on an account that is not demo. The
// HTML's default is the red one on purpose, for the moment before this runs.
function updateBadge() {
  const badge = $('env-badge');
  if (!badge) return;
  const live = state.sessions.some((s) => s.running && !s.demo);
  badge.className = 'badge ' + (live ? 'live' : 'practice');
  badge.textContent = live ? 'live · real money' : 'practice';
}

function renderTable() {
  updateBadge();
  const body = $('live-rows');
  body.textContent = '';
  const foot = $('live-foot');
  foot.textContent = '';
  if (!state.sessions.length) {
    const td = body.insertRow().insertCell();
    td.colSpan = 13;
    td.textContent = 'no session yet: start one below';
    return;
  }
  let running = 0;
  for (const s of state.sessions) {
    const row = body.insertRow();
    row.dataset.id = s.id;
    if (s.id === state.pick) row.className = 'selected';
    const f = s.fields || {};
    const [word, cls] = status(s);
    cell(row, s.id);
    cell(row, `${s.provider} ${s.account}${s.demo ? ' · demo' : ' · REAL'}`);
    cell(row, f.strategy).title = paramsText(f);
    cell(row, `${f.instrument} ${f.granularity}`);
    cell(row, stamp(s.started));
    cell(row, (s.running ? '● ' : '') + word + (s.errors ? ` · ${s.errors} errors` : ''), cls);
    cell(row, s.lastBar ? `${stamp(s.lastBar.time)} ${s.lastBar.close === undefined ? '' : Number(s.lastBar.close).toFixed(5)}` : 'waiting for a bar');
    cell(row, String(s.signals), 'num');
    cell(row, String(s.orders), 'num');
    cell(row, String(s.open.length), 'num');
    cell(row, `${s.closed.length} (${s.won}/${s.lost})`, 'num');
    cell(row, money(s.net), 'num' + (s.net > 0 ? ' good' : s.net < 0 ? ' bad' : ''));
    const actions = row.insertCell();
    actions.className = 'run-actions';
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.action = button.dataset.icon = s.running ? 'stop' : 'delete';
    button.textContent = s.running ? 'stop & close' : 'delete';
    button.disabled = !!(s.running && s.stopped);
    actions.appendChild(button);
    if (s.running) running += 1;
  }
  cell(foot.insertRow(), `${running} running of ${state.sessions.length}`).colSpan = 13;
}

$('live-rows').addEventListener('click', async (event) => {
  const row = event.target.closest('tr[data-id]');
  if (!row) return;
  const id = row.dataset.id;
  const action = event.target.dataset.action;
  try {
    if (action === 'stop') {
      if (!await askUser(`Stop session ${id} and close everything? Its resting orders are cancelled and its open trades closed at market. Other sessions on the same account keep theirs.`)) return;
      await post(`api/live/${id}/stop`);
      return refresh();
    }
    if (action === 'delete') {
      if (!await askUser(`Delete session ${id} and its log?`)) return;
      await post(`api/live/${id}/delete`);
      if (state.pick === id) { state.pick = null; $('live-detail').hidden = true; }
      return refresh();
    }
    state.pick = id;
    renderTable();
    await refreshDetail();
  } catch (error) { message(String(error.message || error)); }
});

/* ----------------------------------------------------------------- detail */

function renderDetail() {
  const d = state.detail;
  $('live-detail').hidden = !d;
  if (!d) return;
  const f = d.fields || {};
  $('detail-title').textContent = `${d.id} · ${f.strategy} on ${f.instrument} ${f.granularity}`
    + ` · ${d.provider} ${d.account} (${d.accountName || ''})`
    + (f.capital ? ` · capitale ${money(Number(f.capital))} (saldo del conto ${money(d.balance)} ${d.currency || ''})`
                 : ` · capital at start ${money(d.balance)} ${d.currency || ''}`)
    + (f.capital ? ` · risk taken on ${money(Number(f.capital))} ${d.currency || ''}` : '');
  $('detail-params').textContent = paramsText(f);

  const open = $('open-rows');
  open.textContent = '';
  for (const p of d.open) {
    const row = open.insertRow();
    cell(row, p.deal); cell(row, stamp(p.time)); cell(row, String(p.units), 'num'); cell(row, String(p.price), 'num');
  }
  if (!d.open.length) cell(open.insertRow(), 'none').colSpan = 4;

  const closed = $('closed-rows');
  closed.textContent = '';
  for (const t of d.closed.slice().reverse()) {
    const row = closed.insertRow();
    cell(row, t.deal); cell(row, stamp(t.opened)); cell(row, stamp(t.time));
    cell(row, String(t.units), 'num'); cell(row, String(t.entry ?? ''), 'num'); cell(row, String(t.exit ?? ''), 'num');
    cell(row, money(t.pl) + (t.estimated ? ' ~' : ''), 'num' + (t.pl > 0 ? ' good' : t.pl < 0 ? ' bad' : ''))
      .title = t.estimated ? 'estimated from the levels: the broker reported no P&L' : '';
    cell(row, t.reason || '');
  }
  if (!d.closed.length) cell(closed.insertRow(), 'none yet').colSpan = 8;

  const list = $('event-list');
  list.textContent = '';
  for (const e of d.events.slice().reverse().slice(0, 150)) {
    const line = document.createElement('div');
    line.className = 'event event-' + String(e._type || '').toLowerCase()
      // a parity status is not an error: the session kept trading, it only
      // found itself apart from the reference
      + (e._type === 'STATUS' && String(e.status || '').startsWith('PARITY') ? ' event-parity' : '');
    const when = e.time || e._created || '';
    const what = e._type === 'CANDLE' ? `close ${(e.mid || {}).c}`
      : e._type === 'TRANSACTION' ? `${e.type} ${e.dealId || ''} ${e.tradesClosed ? 'closed, P&L ' + e.pl : 'opened at ' + (e.price ?? '')}`
      : e._type === 'SIGNAL' || e._type === 'ORDER' ? `${e.units > 0 ? 'BUY' : 'SELL'} ${e.orderType || ''} ${Math.abs(e.units)} @${e.price} SL ${e.stopLoss} TP ${e.takeProfit}`
      : e._type === 'STATUS' ? e.status
      : Object.entries(e).filter(([k]) => !k.startsWith('_')).slice(0, 5).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
    line.textContent = `${String(when).replace('T', ' ').slice(0, 19)}  ${e._type}  ${what}`;
    list.appendChild(line);
  }
  if (!d.events.length) list.textContent = 'nothing logged yet';

  const pre = $('console');
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent = (d.console || []).join('\n');
  if (atBottom) pre.scrollTop = pre.scrollHeight;
  drawEquity(d);
}

function drawEquity(d) {
  const canvas = $('live-equity');
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = 220;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = height + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const p = palette();
  // held to now: the capital has not moved since the last close
  const curve = d.curve || [];
  const last = curve[curve.length - 1];
  const points = last && Date.now() > last[0] ? curve.concat([[Date.now(), last[1]]]) : curve;
  ctx.fillStyle = p.text3;
  ctx.font = '12px ' + p.mono;
  if (points.length < 2) { ctx.fillText('the capital curve starts with the first close', 12, 24); return; }
  const left = 70, right = 12, top = 12, bottom = 22;
  const t0 = points[0][0], t1 = points[points.length - 1][0];
  let lo = Math.min(...points.map((pt) => pt[1])), hi = Math.max(...points.map((pt) => pt[1]));
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = (t) => left + (t - t0) / Math.max(1, t1 - t0) * (width - left - right);
  const y = (v) => top + (hi - v) / (hi - lo) * (height - top - bottom);
  ctx.strokeStyle = p.line;
  ctx.beginPath(); ctx.moveTo(left, y(points[0][1])); ctx.lineTo(width - right, y(points[0][1])); ctx.stroke();
  ctx.fillText(money(hi), 4, top + 8);
  ctx.fillText(money(lo), 4, height - bottom);
  ctx.fillText(stamp(t0), left, height - 6);
  ctx.fillText(stamp(t1), width - right - 110, height - 6);
  ctx.strokeStyle = p.entry;
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach(([t, v], i) => {
    // a step: the capital moves at a close and holds until the next one
    if (i === 0) ctx.moveTo(x(t), y(v));
    else { ctx.lineTo(x(t), y(points[i - 1][1])); ctx.lineTo(x(t), y(v)); }
  });
  ctx.stroke();
}

/* ------------------------------------------------------------------- poll */

async function refresh() {
  try {
    ({ sessions: state.sessions } = await ask('api/live'));
    message('');
  } catch (error) { message(String(error.message || error)); }
  renderTable();
}

async function refreshDetail() {
  if (!state.pick) return;
  try { state.detail = await ask('api/live/' + state.pick); }
  catch (error) { message(String(error.message || error)); state.detail = null; }
  renderDetail();
}

async function loop() {
  await refresh();
  if (!state.pick && state.sessions.length) state.pick = state.sessions[0].id;
  await refreshDetail();
  renderTable();
  await refreshSkew();
  await refreshChart();
  $('live-clock').textContent = 'UTC ' + stamp(Date.now());
  setTimeout(loop, POLL_MS);
}

window.addEventListener('resize', () => state.detail && drawEquity(state.detail));
loop();

/* --------------------------------------------------- skew board and chart */

const PERIOD_MS = { M1: 60e3, M5: 300e3, M15: 900e3, M30: 1800e3, H1: 3600e3, H4: 14400e3, D: 86400e3 };
const periodOf = (granularity) => PERIOD_MS[granularity] || 300e3;
const clock = (ms) => new Date(ms).toISOString().slice(11, 19);
const pips = (v) => v === null || v === undefined ? '' : Number(v).toFixed(1);
const pct2 = (v) => v === null || v === undefined ? '' : `${Number(v).toFixed(2)}%`;
const feedOf = (s) => `${s.provider}:${s.account}`;
const groupKey = (f) => `${f.strategy}|${f.instrument}|${f.granularity}`;

// The groups the pairing knows; before it knows any, the sessions' own
// strategy/instrument/granularity, so the chart has candles to show from
// the first bar
function groupsOf() {
  if (state.skewTrades.groups.length) return state.skewTrades.groups;
  const seen = new Map();
  for (const s of state.sessions) {
    const f = s.fields || {};
    if (!seen.has(groupKey(f))) {
      seen.set(groupKey(f), { key: groupKey(f), strategy: f.strategy, instrument: f.instrument,
                              granularity: f.granularity, reference: null, sessions: [] });
    }
  }
  return [...seen.values()];
}

async function refreshSkew() {
  try { state.skewTrades = { groups: (await ask('api/live/skew/trades')).groups || [] }; }
  catch (error) { $('skew-note').textContent = `skew/trades: ${error.message || error}`; }
  const groups = groupsOf();
  if (Date.now() - state.skewAt >= SKEW_MS) {
    state.skewAt = Date.now();
    for (const key of new Set(groups.map((g) => `${g.instrument}|${g.granularity}`))) {
      const [instrument, granularity] = key.split('|');
      try {
        state.skew[key] = await ask(`api/live/skew?instrument=${encodeURIComponent(instrument)}`
                                    + `&granularity=${encodeURIComponent(granularity)}`);
      } catch (error) { $('skew-note').textContent = `skew: ${error.message || error}`; }
    }
  }
  renderGroups(groups);
  renderBoard(groups);
  renderTradeSkew();
}

function renderGroups(groups) {
  const select = $('chart-group');
  if ([...select.options].map((o) => o.value).join(',') !== groups.map((g) => g.key).join(',')) {
    select.textContent = '';
    for (const g of groups) select.add(new Option(`${g.strategy} · ${g.instrument} · ${g.granularity}`, g.key));
  }
  if (!groups.some((g) => g.key === state.group)) state.group = groups.length ? groups[0].key : null;
  select.value = state.group || '';
}

function renderBoard(groups) {
  const body = $('skew-rows');
  body.textContent = '';
  for (const g of groups) {
    const skew = state.skew[`${g.instrument}|${g.granularity}`] || {};
    const byFeed = new Map((skew.feeds || []).map((f) => [f.feed, f]));
    const line = (id, provider, account, session) => {
      const row = body.insertRow();
      if (!session) row.className = 'hint';
      const k = byFeed.get(`${provider}:${account}`) || {};
      cell(row, `${provider} · ${account}`).title = `${g.strategy} ${g.instrument} ${g.granularity} · ${id}`;
      cell(row, !session ? `riferimento${k.n === undefined ? '' : ` · ${k.n} barre`}`
        : k.mean === undefined || k.mean === null ? ''
        : `media |Δc| ${pips(k.mean)} pip · ultimo ${pips(k.last)} · p95 ${pips(k.p95)}`);
      cell(row, pips(k.spread), 'num');
      cell(row, pips(k.latency), 'num');
      const sm = (session || {}).summary || {};
      const unpaired = (sm.unpairedBroker || 0) + (sm.unpairedSim || 0);
      const parity = (session || {}).parity || {};
      const alarm = (parity.alarms || []).length > 0;
      cell(row, session ? String(sm.paired ?? 0) : '', 'num');
      cell(row, session ? pips(sm.meanEntryDiff) : '', 'num');
      cell(row, session ? String(sm.outcomeMismatch ?? 0) : '', 'num');
      cell(row, session ? String(unpaired) : '', 'num' + (unpaired ? ' bad' : ''));
      cell(row, session ? money(sm.plDiff) : '', 'num');
      cell(row, session ? pips(sm.plDiffPips) : '', 'num');
      cell(row, session ? pct2(sm.plDiffPct) : '', 'num');
      cell(row, session ? `${parity.divergences ?? 0} divergenze${alarm ? ' · ALARM' : ''}` : '', alarm ? 'bad' : '');
    };
    if (g.reference) line(g.reference.id, g.reference.provider, g.reference.account, null);
    for (const s of g.sessions || []) if (!g.reference || s.id !== g.reference.id) line(s.id, s.provider, s.account, s);
  }
  if (!body.rows.length) cell(body.insertRow(), 'nessuna sessione', 'hint').colSpan = 12;

  const td = Object.values(state.skew).map((s) => s.twelvedata).find(Boolean);
  const g = groups.find((x) => x.key === state.group) || groups[0];
  if (!td || !g) return;
  // the feeds are polled once the bar has closed, with a margin for the
  // provider to have it: the next multiple of the period, plus 20 s
  const period = periodOf(g.granularity);
  const next = (Math.floor(Date.now() / period) + 1) * period + 20000;
  $('skew-note').textContent = `Twelve Data: ${td.calls_today}/${td.limit} crediti oggi (riserva ${td.reserve})`
    + ` · ultima chiamata ${td.last_call ? clock(td.last_call) + ' UTC' : '—'} · prossimo poll ~${clock(next)}`;
  $('skew-note').className = td.calls_today >= td.limit - td.reserve ? 'bad' : 'hint';
}

function renderTradeSkew() {
  const box = $('trade-skew-tables');
  box.textContent = '';
  for (const g of state.skewTrades.groups) {
    const h = document.createElement('h3');
    h.textContent = `${g.strategy} · ${g.instrument} · ${g.granularity}`;
    const table = document.createElement('table');
    table.className = 'live-small';
    const head = table.createTHead().insertRow();
    for (const [label, cls] of [['segnale', ''], ['sim entry', 'num'], ['sim exit', 'num'], ['sim esito', ''], ['sim P&L', 'num'],
        ['broker', ''], ['entry', 'num'], ['exit', 'num'], ['esito', ''], ['P&L', 'num'], ['Δentry pip', 'num'],
        ['Δexit pip', 'num'], ['ΔP&L EUR', 'num'], ['ΔP&L pip', 'num'], ['ΔP&L %', 'num'],
        ['esito uguale', ''], ['spaiato', '']]) {
      const th = document.createElement('th');
      th.textContent = label;
      th.className = cls;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (const s of g.sessions || []) for (const r of s.rows || []) {
      const row = body.insertRow();
      row.dataset.signal = r.signal;
      row.dataset.group = g.key;
      if (r.unpaired || r.outcomeMatch === false) row.className = 'bad';
      const sim = r.sim || {}, bk = r.broker || {};
      // the group header already says strategy, instrument and granularity:
      // the row keeps the bar the signal fired on
      cell(row, String(r.signal || '').split(':').pop()).title = r.signal || '';
      cell(row, String(sim.entry ?? ''), 'num'); cell(row, String(sim.exit ?? ''), 'num');
      cell(row, sim.reason || ''); cell(row, money(sim.pl), 'num');
      cell(row, `${s.provider} · ${s.account}`);
      cell(row, String(bk.entry ?? ''), 'num'); cell(row, String(bk.exit ?? ''), 'num');
      cell(row, bk.reason || ''); cell(row, money(bk.pl), 'num');
      cell(row, pips(r.entryDiff), 'num'); cell(row, pips(r.exitDiff), 'num'); cell(row, money(r.plDiff), 'num');
      cell(row, pips(r.plDiffPips), 'num'); cell(row, pct2(r.plDiffPct), 'num');
      cell(row, r.outcomeMatch === null || r.outcomeMatch === undefined ? '' : r.outcomeMatch ? 'sì' : 'no');
      cell(row, r.unpaired || '');
    }
    if (!body.rows.length) cell(body.insertRow(), 'nessun trade', 'hint').colSpan = 17;
    box.append(h, table);
  }
  if (!box.children.length) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = 'nessun trade accoppiato ancora';
    box.appendChild(p);
  }
}

$('trade-skew-tables').addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-signal]');
  if (!row) return;
  for (const r of $('trade-skew-tables').querySelectorAll('tr.selected')) r.classList.remove('selected');
  row.classList.add('selected');
  $('chart-follow').checked = false;
  if (row.dataset.group === state.group) return LiveChart.zoomToTrade(row.dataset.signal);
  // another group's trade: switch the chart and zoom once its candles are in
  state.group = $('chart-group').value = row.dataset.group;
  state.pendingZoom = row.dataset.signal;
  refreshChart();
});

$('chart-group').addEventListener('change', () => { state.group = $('chart-group').value; refreshChart(); });

// The trades of every session in the group, as the chart marks them: an
// open position is an entry with no exit yet
function tradesOf(group, reference) {
  const out = [];
  for (const s of state.sessions) {
    if (groupKey(s.fields || {}) !== group.key) continue;
    const base = { feed: feedOf(s), provider: s.provider, isRef: feedOf(s) === reference };
    for (const p of s.open) out.push({ ...base, signal: p.signal, entryTime: p.time, exitTime: null,
                                       entry: p.price, exit: null, units: p.units, pl: null });
    for (const t of s.closed) out.push({ ...base, signal: t.signal, entryTime: t.opened ?? t.time, exitTime: t.time,
                                         entry: t.entry, exit: t.exit, units: t.units, pl: t.pl });
  }
  return out;
}

function pairsOf(group) {
  const out = [];
  for (const s of group.sessions || []) for (const r of s.rows || []) {
    const side = r.broker || r.sim;
    if (r.unpaired || !side || r.entryDiff === null || r.entryDiff === undefined) continue;
    out.push({ time: side.opened ?? side.time, entryDiff: r.entryDiff, provider: s.provider });
  }
  return out;
}

async function refreshChart() {
  const groups = groupsOf();
  const group = groups.find((g) => g.key === state.group);
  if (!group) { LiveChart.draw(); return; }   // an empty chart says so, a blank one says nothing
  const c = state.candles;
  if (c.key !== group.key) { c.key = group.key; c.reference = null; c.feeds = {}; }
  // from the last bar held less one period: a feed a bar behind the others
  // gets its bar on the next tick, and the forming bar is refreshed
  const last = Math.max(0, ...Object.values(c.feeds).map((f) => f.candles.length ? f.candles[f.candles.length - 1][0] : 0));
  const from = last ? last - periodOf(group.granularity) : Date.now() - 24 * 3600e3;
  try {
    const payload = await ask(`api/live/candles?instrument=${encodeURIComponent(group.instrument)}`
      + `&granularity=${encodeURIComponent(group.granularity)}&from=${new Date(from).toISOString().slice(0, 19)}`);
    if (c.key !== group.key) return;   // the group changed while this was in flight
    c.reference = payload.reference || c.reference;
    for (const f of payload.feeds || []) {
      const rows = f.candles || [];
      const mine = c.feeds[f.feed] || (c.feeds[f.feed] = { feed: f.feed, provider: f.provider, account: f.account, candles: [] });
      if (rows.length) mine.candles = mine.candles.filter((r) => r[0] < rows[0][0]).concat(rows);
    }
  } catch (error) { $('chart-hover').textContent = `candele: ${error.message || error}`; }
  const reference = c.reference || (group.reference ? feedOf(group.reference) : null);
  LiveChart.setData({ candlesPayload: { reference, feeds: Object.values(c.feeds) },
                      skewPayload: state.skew[`${group.instrument}|${group.granularity}`],
                      trades: tradesOf(group, reference), pairs: pairsOf(group) });
  LiveChart.draw();
  if (state.pendingZoom) { LiveChart.zoomToTrade(state.pendingZoom); state.pendingZoom = null; }
}

/* ------------------------------------------------------ new live session */

// Timeframes a live session can run on: the broker serves the bars, so this
// is not limited to what the local stores hold
const LIVE_GRANULARITIES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D'];

function fill(select, values, pick) {
  select.textContent = '';
  for (const v of values) select.add(new Option(v, v, false, v === pick));
}
function newSay(text) {
  $('new-message').textContent = text || '';
  $('new-message').hidden = !text;
}

/*
 * What goes live is a form somebody looked at the result of. The strategy
 * list is the favourites first - saved backtests and sweep runs starred on
 * their own pages, with what their simulation made (api/favourites) - then
 * the plain strategies, with their defaults and no simulation behind them.
 * pick.chosen is the form the start button sends: a favourite's, a run
 * picked from the "simulate" dialog whether starred or not, or a plain
 * strategy's name.
 */
const pick = { stores: null, favourites: [], chosen: null, tab: 'sweeps', job: null };

const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d);
const pct = (v) => v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%`;
const day = (ms) => ms === null || ms === undefined ? '' : new Date(ms).toISOString().slice(0, 10);

function favLabel(f) {
  const s = f.summary || {};
  const net = s.net === null || s.net === undefined ? '' : ` · ${s.net > 0 ? '+' : ''}${Number(s.net).toFixed(0)}`;
  const trades = s.trades === null || s.trades === undefined ? '' : ` (${s.trades} trade)`;
  return `★ ${s.strategy} · ${s.instrument} ${s.granularity}${net}${trades}${f.note ? ' · ' + f.note : ''}`;
}

// the favourite a simulated source is, if it is one
function favOf(source) {
  if (!source) return null;
  return pick.favourites.find((f) => f.source && f.source.kind === source.kind
    && String(f.source.id) === String(source.id)
    && (source.kind !== 'sweep' || Number(f.source.n) === Number(source.n))) || null;
}

async function loadFavourites() {
  try { ({ favourites: pick.favourites } = await ask('api/favourites')); }
  catch (error) { newSay(String(error.message || error)); pick.favourites = []; }
}

function fillStrategies() {
  const select = $('new-strategy');
  select.textContent = '';
  const c = pick.chosen;
  if (c && c.kind === 'picked') {
    // a simulated run picked but not starred: offered for this start only
    const s = c.summary || {};
    select.add(new Option(`scelta: ${s.strategy} · ${s.instrument} ${s.granularity}`
      + (c.source.kind === 'sweep' ? ` · sweep run #${c.source.n}` : ` · run ${String(c.source.id).slice(0, 8)}`),
      'picked'));
  }
  if (pick.favourites.length) {
    const group = document.createElement('optgroup');
    group.label = 'preferite';
    for (const f of pick.favourites) {
      const option = new Option(favLabel(f), 'fav:' + f.id);
      option.title = paramsText(f.fields);
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  const plain = document.createElement('optgroup');
  plain.label = pick.favourites.length ? 'tutte le strategie (senza simulazione)'
    : 'strategie (nessuna preferita: segna un run di una simulazione con ★)';
  for (const name of pick.stores.strategies) plain.appendChild(new Option(name, 'plain:' + name));
  select.appendChild(plain);
  select.value = !c ? select.options[0].value
    : c.kind === 'picked' ? 'picked' : c.kind === 'fav' ? 'fav:' + c.id : 'plain:' + c.fields.strategy;
}

function choose(kind, what) {
  if (kind === 'fav') {
    pick.chosen = { kind, id: what.id, fields: what.fields || {}, summary: what.summary || {},
                    source: what.source, note: what.note };
  } else if (kind === 'picked') {
    pick.chosen = { kind, fields: what.fields || {}, summary: what.summary || {}, source: what.source };
  } else {
    pick.chosen = { kind: 'plain', fields: { strategy: what }, summary: null, source: null };
  }
  applyChosen();
}

// the form's own controls follow the choice; they stay editable, and a
// change there is the operator's, said so in the aside
function applyChosen() {
  const c = pick.chosen;
  const stores = pick.stores;
  const instruments = [...new Set([...stores.instruments.map((i) => i.instrument),
    ...Object.values(stores.defaults || {}).map((d) => d.instrument),
    ...pick.favourites.map((f) => (f.fields || {}).instrument)])].filter(Boolean).sort();
  const d = c.kind === 'plain' ? ((stores.defaults || {})[c.fields.strategy] || {}) : c.fields;
  fill($('new-instrument'), instruments, d.instrument || instruments[0]);
  fill($('new-granularity'), LIVE_GRANULARITIES, d.granularity || 'H1');
  if (c.fields.risk) $('new-risk').value = c.fields.risk;
  // the capital the simulation ran on, the same on every account ticked
  $('new-capital').value = c.fields.capital || c.fields.balance || (c.summary || {}).start || stores.equity || '';
  $('new-description').textContent = (stores.descriptions || {})[c.fields.strategy] || '';
  fillStrategies();
  renderSummary();
}

function renderSummary() {
  const box = $('new-summary');
  box.textContent = '';
  const c = pick.chosen;
  if (!c) return;
  const head = document.createElement('h4');
  if (c.kind === 'plain') {
    head.textContent = `${c.fields.strategy} · parametri di default`;
    box.appendChild(head);
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = 'nessuna simulazione dietro questa scelta: la strategia parte con i suoi '
      + 'default. Per andare live su un form provato, segna un run di una simulazione con ★, '
      + 'oppure scegli tra le simulate.';
    box.appendChild(p);
    box.appendChild(paramsBlock(c.fields));
    return;
  }
  const s = c.summary || {};
  const star = document.createElement('button');
  star.type = 'button';
  star.className = 'fav-star';
  const starred = !!favOf(c.source);
  star.textContent = starred ? '★' : '☆';
  star.setAttribute('aria-pressed', String(starred));
  star.title = starred ? 'togli dalle preferite' : 'segna come preferita';
  star.addEventListener('click', () => toggleFavourite(c.source).catch((e) => newSay(String(e.message || e))));
  head.append(star, ` ${s.strategy} · ${s.instrument} ${s.granularity}`);
  box.appendChild(head);
  const from = document.createElement('p');
  from.className = 'hint';
  from.textContent = c.source.kind === 'sweep'
    ? `simulazione ${c.source.name ? '"' + c.source.name + '" ' : ''}${c.source.id} · run #${c.source.n}`
    : `backtest salvato ${c.source.id}`;
  box.appendChild(from);
  const dl = document.createElement('dl');
  const row = (k, v) => {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.append(dt, dd);
  };
  row('periodo', `${day(s.from)} .. ${day(s.to)}`);
  row('trade', s.trades === null || s.trades === undefined ? '—' : String(s.trades));
  row('netto', num(s.net));
  row('capitale', `${num(s.start)} → ${num(s.final)}`);
  row('ROI / CAR', `${pct(s.roi)} / ${pct(s.car)}`);
  row('win rate', s.winRate === null || s.winRate === undefined ? '—' : pct(s.winRate * 100));
  row('profit factor', num(s.profitFactor));
  row('max drawdown', `${num(s.maxDrawdown)}${s.maxDrawdownPct === null || s.maxDrawdownPct === undefined ? '' : ' (' + pct(s.maxDrawdownPct) + ')'}`);
  row('sharpe', num(s.sharpe));
  box.appendChild(dl);
  box.appendChild(paramsBlock(c.fields));
  const link = document.createElement('a');
  link.className = 'open-backtest';
  // a run of a set is found on disk by set and number; a saved run by its fields
  link.href = 'run?' + new URLSearchParams({
    ...(c.source.kind === 'sweep' ? { sweep: c.source.id, run: c.source.n } : {}),
    ...Object.fromEntries(Object.entries(c.fields)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')) }).toString();
  link.target = '_blank';   // the live page stays where it was
  link.rel = 'noopener';
  link.textContent = 'apri il run ↗';
  const foot = document.createElement('p');
  foot.className = 'hint';
  foot.textContent = 'il form va live com\'è stato simulato; strumento, timeframe e rischio qui sopra restano tuoi da cambiare';
  box.append(link, foot);
}

// the parameters as the simulation ran them, a default greyed and said so
function paramsBlock(fields) {
  const wrap = document.createElement('div');
  const list = paramList(fields);
  const h = document.createElement('h5');
  h.textContent = list.length ? 'parametri' : 'parametri: nessuno (la strategia non ne ha di liberi)';
  wrap.appendChild(h);
  if (!list.length) return wrap;
  const dl = document.createElement('dl');
  for (const p of list) {
    const dt = document.createElement('dt'); dt.textContent = p.label; dt.title = p.name;
    const dd = document.createElement('dd'); dd.textContent = String(p.value);
    if (p.isDefault) { dd.className = 'hint'; dd.textContent += ' (default)'; dd.title = 'il form non lo fissava: la strategia ha preso il suo default'; }
    dl.append(dt, dd);
  }
  wrap.appendChild(dl);
  return wrap;
}

async function toggleFavourite(source) {
  const have = favOf(source);
  if (have) await post(`api/favourites/${have.id}/delete`);
  else await post('api/favourites', { source });
  await loadFavourites();
  const c = pick.chosen;
  if (c && c.kind === 'picked' && favOf(c.source)) choose('fav', favOf(c.source));
  else if (c && c.kind === 'fav' && !favOf(c.source)) choose('picked', c);
  else { fillStrategies(); renderSummary(); }
  if ($('sim-dialog').open) renderSim().catch((e) => simSay(String(e.message || e)));
}

$('new-strategy').addEventListener('change', () => {
  const value = $('new-strategy').value;
  if (value === 'picked') return applyChosen();
  if (value.startsWith('fav:')) {
    const f = pick.favourites.find((x) => x.id === value.slice(4));
    if (f) return choose('fav', f);
  }
  choose('plain', value.replace(/^plain:/, ''));
});

/* --------------------------------------------- the simulated forms dialog */

function simSay(text) {
  $('sim-message').textContent = text || '';
  $('sim-message').hidden = !text;
}

function starCell(row, source) {
  const td = row.insertCell();
  const star = document.createElement('button');
  star.type = 'button';
  star.className = 'fav-star';
  const starred = !!favOf(source);
  star.textContent = starred ? '★' : '☆';
  star.setAttribute('aria-pressed', String(starred));
  star.title = starred ? 'togli dalle preferite' : 'segna come preferita';
  star.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleFavourite(source).catch((e) => simSay(String(e.message || e)));
  });
  td.appendChild(star);
}

function simTable(headers) {
  const table = document.createElement('table');
  table.className = 'live-small';
  const head = table.createTHead().insertRow();
  for (const h of headers) {
    const th = document.createElement('th');
    th.textContent = h;
    if (h.startsWith('#') || ['trade', 'run', 'capitale', 'netto', 'ROI', 'MDD%', 'migliore'].includes(h)) th.className = 'num';
    head.appendChild(th);
  }
  table.appendChild(document.createElement('tbody'));
  return table;
}

async function renderSim() {
  const list = $('sim-list');
  for (const b of $('sim-tabs').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.tab === pick.tab));
  list.textContent = '';
  simSay('');
  // how many rows the list has, in the title: empty until it is in
  const count = (n) => { $('sim-count').textContent = n; };
  count('');
  if (pick.tab === 'runs') {
    const { runs } = await ask('api/runs');
    count(runs.length);
    if (!runs.length) { list.textContent = 'nessun backtest salvato'; return; }
    const table = simTable(['', 'run', 'salvato', 'strategia', 'mercato', 'periodo', 'parametri', 'trade', 'capitale']);
    for (const run of runs) {
      const row = table.tBodies[0].insertRow();
      row.dataset.pick = run.id;
      starCell(row, { kind: 'run', id: run.id });
      cell(row, run.id); cell(row, stamp(run.saved)); cell(row, run.strategy);
      cell(row, `${run.instrument} ${run.granularity}${run.fine ? ' (' + run.fine + ')' : ''}`);
      cell(row, `${day(run.from)} .. ${day(run.to)}`);
      cell(row, paramsText(run.fields), 'params');
      cell(row, String(run.trades), 'num'); cell(row, num(run.balance), 'num');
      row.addEventListener('click', () => {
        choose('picked', { fields: run.fields, source: { kind: 'run', id: run.id },
          summary: { strategy: run.strategy, instrument: run.instrument, granularity: run.granularity,
                     from: run.from, to: run.to, trades: run.trades, final: run.balance } });
        $('sim-dialog').close();
      });
    }
    list.appendChild(table);
    return;
  }
  if (pick.job) {
    const job = pick.job;
    const back = document.createElement('button');
    back.type = 'button';
    back.textContent = '‹ tutte le simulazioni';
    back.addEventListener('click', () => { pick.job = null; renderSim().catch((e) => simSay(String(e.message || e))); });
    const title = document.createElement('p');
    title.textContent = `${job.name ? '"' + job.name + '" · ' : ''}${job.id} · ${(job.fields || {}).strategy} ${(job.fields || {}).instrument} ${(job.fields || {}).granularity} · ${job.done.length} run`;
    list.append(back, title);
    const table = simTable(['', '#', 'parametri', 'trade', 'netto', 'capitale', 'ROI', 'MDD%']);
    const rows = job.done.filter((r) => !r.error).sort((a, b) => (b.final || 0) - (a.final || 0));
    count(rows.length);
    for (const r of rows) {
      const row = table.tBodies[0].insertRow();
      row.dataset.pick = String(r.n);
      const source = { kind: 'sweep', id: job.id, n: r.n, name: job.name || null };
      starCell(row, source);
      const report = r.report || {}, kpi = r.kpi || {};
      cell(row, String(r.n), 'num');
      cell(row, paramsText({ ...(job.fields || {}), ...(r.params || {}) }), 'params');
      cell(row, String(report.closedTrades ?? '—'), 'num'); cell(row, num(report.net), 'num');
      cell(row, num(r.final), 'num'); cell(row, pct(kpi.roi), 'num'); cell(row, pct(kpi.maxDrawdownPct), 'num');
      row.addEventListener('click', () => {
        const fields = { ...(job.fields || {}), ...(r.params || {}) };
        const start = r.balance ?? (r.final !== undefined && report.net !== undefined ? r.final - report.net : null);
        choose('picked', { fields, source,
          summary: { strategy: fields.strategy, instrument: fields.instrument, granularity: fields.granularity,
                     from: Date.parse(fields.from + 'T00:00:00Z') || null, to: Date.parse(fields.to + 'T23:59:59Z') || null,
                     trades: report.closedTrades, net: report.net, winRate: report.winRate,
                     profitFactor: report.profitFactor, maxDrawdown: report.maxDrawdown, start, final: r.final,
                     roi: kpi.roi, car: kpi.car, maxDrawdownPct: kpi.maxDrawdownPct, sharpe: kpi.sharpe } });
        $('sim-dialog').close();
      });
    }
    list.appendChild(table);
    return;
  }
  const { sweeps } = await ask('api/sweeps');
  count(sweeps.length);
  if (!sweeps.length) { list.textContent = 'nessuna simulazione salvata'; return; }
  const table = simTable(['set', 'nome', 'salvato', 'strategia', 'mercato', 'periodo', 'run', 'migliore']);
  for (const set of sweeps) {
    const row = table.tBodies[0].insertRow();
    row.dataset.pick = set.id;
    cell(row, set.id); cell(row, set.name || ''); cell(row, stamp(set.saved)); cell(row, set.strategy);
    cell(row, `${set.instrument} ${set.granularity}`); cell(row, `${set.from || ''} .. ${set.to || ''}`);
    cell(row, `${set.runs}/${set.total}`, 'num'); cell(row, num(set.best), 'num');
    row.addEventListener('click', async () => {
      simSay('leggo la simulazione…');
      try { pick.job = await ask('api/sweeps/' + set.id); }
      catch (error) { simSay(String(error.message || error)); return; }
      renderSim().catch((e) => simSay(String(e.message || e)));
    });
  }
  list.appendChild(table);
}

$('choose-sim').addEventListener('click', () => {
  $('sim-dialog').showModal();
  renderSim().catch((e) => simSay(String(e.message || e)));
});
$('sim-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-tab]');
  if (!button) return;
  pick.tab = button.dataset.tab;
  pick.job = null;
  renderSim().catch((e) => simSay(String(e.message || e)));
});

async function setupNew() {
  try { pick.stores = await ask('api/stores'); }
  catch (error) { newSay(String(error.message || error)); return; }
  STRATEGY_PARAMS = pick.stores.params || {};
  await loadFavourites();
  if (pick.favourites.length) choose('fav', pick.favourites[0]);
  else choose('plain', pick.stores.strategies[0]);

  const box = $('new-accounts');
  let targets;
  try { ({ targets } = await ask('api/live/targets')); }
  catch (error) { box.querySelector('.hint').textContent = String(error.message || error); return; }
  box.querySelector('.hint').remove();
  for (const row of targets) {
    const note = !row.configured ? '' : row.error || (row.accounts.length ? '' : 'no account');
    if (!row.configured) continue;   // no credentials: nothing to offer
    for (const account of row.accounts) {
      const label = document.createElement('label');
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.dataset.provider = row.provider;
      tick.dataset.account = account.id;
      tick.dataset.real = account.demo ? '' : '1';
      label.append(tick, ` ${row.provider} · ${account.id} ${account.name || ''} · ${account.currency || ''} `
        + `${account.balance === null ? '' : money(account.balance)}`);
      const kind = document.createElement('span');
      kind.className = account.demo ? 'hint' : 'real';
      kind.textContent = account.demo ? ' demo' : ' REAL MONEY';
      label.appendChild(kind);
      box.appendChild(label);
    }
    if (note) {
      const p = document.createElement('p');
      p.className = 'hint';
      p.textContent = `${row.provider}: ${note}`;
      box.appendChild(p);
    }
  }
  if (!box.querySelector('input')) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = 'no account reachable: put a broker\'s credentials in .env';
    box.appendChild(p);
  }
}

$('new-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const ticked = [...$('new-accounts').querySelectorAll('input:checked')];
  if (!ticked.length) { newSay('tick at least one account'); return; }
  if (ticked.some((t) => t.dataset.real)
      && !await askUser('One of these is a REAL MONEY account. Trade real money?')) return;
  // the simulated form as it was, with the three controls the operator owns
  // over it; a plain strategy is just its name and those three
  const c = pick.chosen || { kind: 'plain', fields: { strategy: $('new-strategy').value.replace(/^plain:/, '') } };
  const fields = Object.fromEntries(Object.entries(c.kind === 'plain' ? { strategy: c.fields.strategy } : c.fields)
    .filter(([, v]) => v !== null && v !== undefined));
  fields.instrument = $('new-instrument').value;
  fields.granularity = $('new-granularity').value;
  fields.risk = $('new-risk').value;
  // the capital the risk is a percentage of: one number for every account,
  // a USD one taking it 1:1 (web/livesessions.start, scripts/live.py quoteBalance)
  fields.capital = $('new-capital').value;
  $('new-start').disabled = true;
  newSay('starting…');
  try {
    const { started } = await post('api/live', { fields,
      targets: ticked.map((t) => ({ provider: t.dataset.provider, account: t.dataset.account })) });
    newSay(`started ${started.length} session${started.length === 1 ? '' : 's'}`);
    for (const t of ticked) t.checked = false;
    if (started.length) state.pick = started[0].id;
    await refresh();
    await refreshDetail();
  } catch (error) { newSay(String(error.message || error)); }
  $('new-start').disabled = false;
});

setupNew();
