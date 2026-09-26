/*
 * The journal page (journal.html, web/journal.py): every strategy's journal,
 * a search across them, and one journal - where it is now (its versions and
 * their states, its sessions), its history with the notes on it, the code of
 * two versions side by side, and its live trades week by week.
 */
const $ = (id) => document.getElementById(id);

async function ask(url) {
  const response = await fetch(url);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', body: JSON.stringify(body),
                                      headers: { 'X-Parity-Deriva': '1' } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

const state = { strategy: null, entries: [], cards: [], sessions: [], ticked: [] };

// the journal a strategy writes to: an uploaded one's versions share their name's (web/journal.py)
const family = (code) => code.replace(/ \d+$/, '');

function stamp(ms) {
  return ms ? new Date(ms).toISOString().slice(0, 16).replace('T', ' ') : '';
}

const num = (v, places = 2) => v === null || v === undefined || v === '' ? 'n/a' : Number(v).toFixed(places);
const MARK = { up: '\u{1F44D}', down: '\u{1F44E}', flat: '➖' };

// what an entry says, from its numbers: one sentence a kind
const SAY = {
  idea: (d) => (d.description ? `the idea: ${d.description}` : 'a new strategy'),
  draft: (d) => `a draft from an assistant: ${d.name}`,
  enabled: (d) => `enabled on the settings page: ${d.name}`,
  code: (d) => `the code changed: ${Object.keys(d.source || {}).length} files`,
  sweep: (d) => (d.stopped ? `set ${d.name || d.id} stopped: ${d.runs} of ${d.total} runs, best score ${num(d.bestScore, 1)}`
    : `set ${d.name || d.id}: ${d.runs} runs, best score ${num(d.bestScore, 1)}`)
    + (d.pf === null || d.pf === undefined ? '' : ` \u00b7 profit factor ${num(d.pf)}`),
  'sweep-deleted': (d) => `set ${d.name || d.id} deleted`,
  run: (d) => (d.net === null || d.net === undefined ? `a run: ${d.trades} trades`
    : `a run: ${d.trades} trades, net ${num(d.net)}, profit factor ${num(d.pf)}`),
  favourite: (d) => `starred: ${d.trades} trades, net ${num(d.net)}, profit factor ${num(d.pf)}`,
  unfavourite: () => 'the star taken off',
  'favourite-note': (d) => `the favourite's note: ${d.note}`,
  mix: (d) => `in the mix ${d.name || d.id}`,
  'mix-deleted': (d) => `the mix ${d.name || d.id} deleted`,
  verify: (d) => (d.ok ? `verify: the same ${d.trades} trades here` : `verify: trade ${d.first} is not the same here`),
  version: (d) => `a new version: ${d.label}`,
  state: (d) => `${d.label}: ${d.from} → ${d.to}` + (d.why ? ` · ${d.why}` : ''),
  push: (d) => (d.verdict ? `pushed to ${d.server} (${d.accounts}): ${d.verdict.ok ? 'promoted' : 'not promoted'}`
    : `pushed to ${d.server} (${d.accounts})`),
  'session-start': (d) => `session started on ${d.provider} ${d.account}, ${d.demo ? 'demo' : 'REAL MONEY'}, capital ${d.capital}`,
  'session-stop': (d) => `session stopped on ${d.provider} ${d.account}: ${d.trades} trades, net ${num(d.net)}`,
  alert: (d) => `alert: ${d.text}`,
  note: (d) => (d.mark ? `${MARK[d.mark]} ${d.text}` : d.text),
};

function say(entry) {
  const speak = SAY[entry.kind];
  return speak ? speak(entry.data || {}) : entry.kind;
}

function href(link) {
  if (!link) return null;
  if (link.kind === 'sweep') return `./?set=${encodeURIComponent(link.id)}`;
  if (link.kind === 'mix') return `mix?id=${encodeURIComponent(link.id)}`;
  if (link.kind === 'run') return 'run?' + new URLSearchParams(link.fields || {});
  if (link.kind === 'sweep-run') return 'run?' + new URLSearchParams({ sweep: link.id, run: link.n, ...(link.fields || {}) });
  if (link.kind === 'session') return 'live';
  return null;
}

/* ---------------------------------------------------------------- the list */

function showList(rows, found) {
  const body = $('journal-list-rows');
  body.textContent = '';
  if (found) {
    $('journal-list').querySelector('thead').hidden = true;
    for (const entry of found) {
      const row = document.createElement('tr');
      row.dataset.strategy = entry.strategy;
      for (const text of [entry.strategy, stamp(entry.at), say(entry)]) {
        const cell = document.createElement('td');
        cell.textContent = text;
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
    $('journal-note').textContent = found.length ? '' : 'nothing found';
    return;
  }
  $('journal-list').querySelector('thead').hidden = false;
  for (const j of rows) {
    const row = document.createElement('tr');
    row.dataset.strategy = j.strategy;
    for (const text of [j.strategy, j.instruments.join(', '), String(j.entries), stamp(j.last)]) {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  $('journal-note').textContent = rows.length ? ''
    : 'no journal yet: one starts with the first set, run or favourite of a strategy';
}

$('journal-list-rows').addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-strategy]');
  if (row) open(row.dataset.strategy);
});

let searching = null;
$('journal-search').addEventListener('input', () => {
  clearTimeout(searching);
  searching = setTimeout(async () => {
    const q = $('journal-search').value.trim();
    try {
      if (q) showList([], (await ask('api/journals/search?q=' + encodeURIComponent(q))).entries);
      else showList((await ask('api/journals')).journals);
    } catch (error) {
      $('journal-note').textContent = String(error.message || error);
    }
  }, 250);
});

/* -------------------------------------------------------------- a journal */

async function open(strategy, keep) {
  try {
    const [held, live] = await Promise.all([ask('api/journal?strategy=' + encodeURIComponent(strategy)),
                                            ask('api/live').catch(() => ({ sessions: [] }))]);
    state.strategy = strategy;
    state.entries = held.entries;
    state.cards = held.cards;
    state.holdouts = held.holdouts || [];
    state.sessions = live.sessions.filter((s) => family((s.fields || {}).strategy || '') === family(strategy));
    if (!keep) {
      state.ticked = [];
      $('journal-diff').hidden = true;
      history.replaceState(null, '', '?strategy=' + encodeURIComponent(strategy));
    }
    draw();
  } catch (error) {
    $('journal-note').textContent = String(error.message || error);
  }
}

function button(text, icon, action) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  if (icon) b.dataset.icon = icon;
  b.addEventListener('click', action);
  return b;
}

// the moves a card may make, as its buttons (web/cards.py MOVES)
const MOVES = { SIM: ['DEMO', 'DEAD'], DEMO: ['LIVE', 'SIM', 'DEAD'], LIVE: ['SUSPENDED', 'DEAD'],
                SUSPENDED: ['DEMO', 'SIM', 'DEAD'], DEAD: [] };
// by hand: the platform moves to DEMO and LIVE with a push or a promotion
const BY_HAND = { DEAD: 'discard', SIM: 'back to SIM', DEMO: 'back to demo' };

function drawNow() {
  const now = $('journal-now');
  now.textContent = '';
  for (const card of state.cards) {
    const box = document.createElement('div');
    box.className = 'journal-card';
    const title = document.createElement('div');
    const f = card.fields || {};
    title.textContent = `${card.label} · ${f.instrument} ${f.granularity} · `;
    const s = document.createElement('span');
    s.className = `state ${card.state}`;
    s.textContent = card.state;
    title.appendChild(s);
    box.appendChild(title);
    for (const to of MOVES[card.state] || []) {
      if (!BY_HAND[to] || (to === 'DEMO' && card.state !== 'SUSPENDED')) continue;
      box.appendChild(button(BY_HAND[to], to === 'DEAD' ? 'delete' : 'rerun', async () => {
        if (to === 'DEAD' && !window.confirm(`Discard ${card.label}? It stays in the journal, and no page offers it again.`)) return;
        const why = window.prompt('why? (optional)') || '';
        try {
          await post('api/cards/move', { id: card.id, to, why });
          open(state.strategy, true);
        } catch (error) {
          $('journal-note').textContent = String(error.message || error);
        }
      }));
    }
    now.appendChild(box);
  }
  for (const s of state.sessions.filter((one) => one.running)) {
    const box = document.createElement('div');
    box.className = 'journal-card';
    box.textContent = `session ${s.provider} ${s.account} · ${(s.fields || {}).instrument} `
      + `${(s.fields || {}).granularity} · ${s.demo ? 'demo' : 'REAL MONEY'} · net ${num(s.net)}`;
    now.appendChild(box);
  }
  if (!now.children.length) now.textContent = 'no version past the gate and no session running';
  for (const h of state.holdouts) now.appendChild(holdoutBox(h));
}

// the holdout the strategy's runs stop at, on each instrument it was tried on
// (web/holdout.py): the instrument's, or a cut of its own set here (D4)
function holdoutBox(h) {
  const box = document.createElement('div');
  box.className = 'journal-card';
  const line = document.createElement('div');
  line.textContent = h.own ? `holdout on ${h.instrument} from ${h.cut}, its own \u00b7 opened ${h.openings} times`
    : `holdout on ${h.instrument} from ${h.cut}, the instrument's \u00b7 opened ${h.openings} times`;
  box.appendChild(line);
  const when = document.createElement('input');
  when.type = 'date';
  when.value = h.cut;
  when.setAttribute('aria-label', `its own holdout on ${h.instrument} from`);
  const move = async (cut) => {
    try {
      await post('api/holdout', { instrument: h.instrument, cut, strategy: state.strategy });
      open(state.strategy, true);
    } catch (error) {
      $('journal-note').textContent = String(error.message || error);
    }
  };
  box.append(when, button('its own cut', 'yes', () => move(when.value)));
  if (h.own) box.appendChild(button("the instrument's cut", 'rerun', () => move('')));
  return box;
}

function noteForm(entry) {
  const form = document.createElement('form');
  form.className = 'note-form';
  const text = document.createElement('textarea');
  text.setAttribute('aria-label', 'the note');
  const mark = document.createElement('select');
  mark.setAttribute('aria-label', 'mark');
  for (const [value, label] of [['', 'no mark'], ['up', MARK.up], ['down', MARK.down], ['flat', MARK.flat]]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    mark.appendChild(option);
  }
  const save = document.createElement('button');
  save.type = 'submit';
  save.textContent = 'add the note';
  form.append(text, mark, save);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await post('api/journal/note', { strategy: state.strategy, about: entry.id, text: text.value, mark: mark.value });
      open(state.strategy, true);
    } catch (error) {
      $('journal-note').textContent = String(error.message || error);
    }
  });
  return form;
}

function drawHistory() {
  const level = $('journal-level').value;
  const instrument = $('journal-instrument').value;
  const notes = {};
  for (const e of state.entries) {
    if (e.kind === 'note' && e.data.about) (notes[e.data.about] = notes[e.data.about] || []).push(e);
  }
  const body = $('journal-rows');
  body.textContent = '';
  const shown = state.entries.filter((e) => !(e.kind === 'note' && e.data.about))
    .filter((e) => level === 'all' || e.level === level || e.kind === 'note' || e.kind === 'idea')
    .filter((e) => !instrument || !e.instrument || e.instrument === instrument);
  for (const e of shown.slice().reverse()) {
    const row = document.createElement('tr');
    row.className = e.kind === 'note' ? 'note' : (e.level || '');
    const when = document.createElement('td');
    when.className = 'when';
    when.textContent = stamp(e.at);
    const where = document.createElement('td');
    where.className = 'where';
    where.textContent = [e.instrument, e.granularity].filter(Boolean).join(' ');
    const what = document.createElement('td');
    what.className = 'what';
    const link = href(e.link);
    if (link) {
      const a = document.createElement('a');
      a.href = link;
      a.textContent = say(e);
      what.appendChild(a);
    } else {
      what.textContent = say(e);
    }
    for (const n of notes[e.id] || []) {
      const line = document.createElement('div');
      line.className = 'dim';
      line.textContent = `${stamp(n.at)} · ${say(n)}`;
      what.appendChild(line);
    }
    const tools = document.createElement('td');
    if (e.kind === 'code') {
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.checked = state.ticked.includes(e.id);
      tick.setAttribute('aria-label', 'compare this code');
      tick.addEventListener('change', () => {
        state.ticked = tick.checked ? state.ticked.concat(e.id).slice(-2) : state.ticked.filter((id) => id !== e.id);
        drawHistory();
      });
      tools.appendChild(tick);
    }
    if (e.kind !== 'note') {
      tools.appendChild(button('note', 'add', () => {
        if (!what.querySelector('form')) what.appendChild(noteForm(e));
      }));
    }
    row.append(when, where, what, tools);
    body.appendChild(row);
  }
  $('journal-compare').disabled = state.ticked.length !== 2;
}

/* ------------------------------------------------------- two code versions */

// the lines of a and b, the same, taken out and put in: a longest common
// subsequence, enough for a strategy's few hundred lines
function diff(a, b) {
  const n = a.length, m = b.length;
  const table = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { out.push([' ', a[i]]); i++; j++; }
    else if (j < m && (i === n || table[i][j + 1] >= table[i + 1][j])) out.push(['+', b[j++]]);
    else out.push(['-', a[i++]]);
  }
  return out;
}

$('journal-compare').addEventListener('click', () => {
  const [a, b] = state.ticked.map((id) => state.entries.find((e) => e.id === id))
    .sort((x, y) => x.at - y.at);
  const box = $('journal-diff');
  box.textContent = '';
  const parts = [...new Set(Object.keys(a.data.source || {}).concat(Object.keys(b.data.source || {})))].sort();
  for (const part of parts) {
    const lines = diff(((a.data.source || {})[part] || '').split('\n'), ((b.data.source || {})[part] || '').split('\n'));
    if (!lines.some(([sign]) => sign !== ' ')) continue;
    const head = document.createElement('div');
    head.textContent = `=== ${part}`;
    box.appendChild(head);
    lines.forEach(([sign, text], k) => {
      // only the changes and three lines around them
      const near = lines.slice(Math.max(0, k - 3), k + 4).some(([s]) => s !== ' ');
      if (!near) return;
      const line = document.createElement('div');
      line.className = sign === '+' ? 'add' : sign === '-' ? 'del' : '';
      line.textContent = `${sign} ${text}`;
      box.appendChild(line);
    });
  }
  if (!box.children.length) box.textContent = 'the same code: only comments or blank lines differ';
  box.hidden = false;
});

/* ------------------------------------------------------ live, by the week */

function monday(ms) {
  const d = new Date(ms);
  d.setUTCHours(0, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.getTime();
}

function drawWeeks() {
  const weeks = {};
  for (const s of state.sessions) {
    for (const t of s.closed || []) {
      if (t.pl === null || t.pl === undefined) continue;
      const w = weeks[monday(t.time)] = weeks[monday(t.time)] || { sessions: new Set(), trades: 0, won: 0, net: 0 };
      w.sessions.add(s.id);
      w.trades += 1;
      w.won += t.pl > 0 ? 1 : 0;
      w.net += t.pl;
    }
  }
  const body = $('journal-weeks-rows');
  body.textContent = '';
  const keys = Object.keys(weeks).map(Number).sort((a, b) => b - a);
  for (const key of keys) {
    const w = weeks[key];
    const row = document.createElement('tr');
    for (const text of [stamp(key).slice(0, 10), String(w.sessions.size), String(w.trades), String(w.won), num(w.net)]) {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  if (!keys.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.textContent = 'no trade closed on an account yet';
    row.appendChild(cell);
    body.appendChild(row);
  }
}

function draw() {
  $('journal-panel').hidden = false;
  $('journals-panel').hidden = true;
  $('journal-title').textContent = state.strategy;
  const idea = state.entries.find((e) => e.kind === 'idea');
  $('journal-idea').textContent = idea && idea.data.description ? idea.data.description : '';
  const pick = $('journal-instrument');
  const was = pick.value;
  pick.textContent = '';
  for (const value of [''].concat([...new Set(state.entries.map((e) => e.instrument).filter(Boolean))].sort())) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value || 'all';
    pick.appendChild(option);
  }
  pick.value = [...pick.options].some((o) => o.value === was) ? was : '';
  drawNow();
  drawHistory();
  drawWeeks();
}

$('journal-back').addEventListener('click', () => {
  $('journal-panel').hidden = true;
  $('journals-panel').hidden = false;
  history.replaceState(null, '', location.pathname);
});
$('journal-level').addEventListener('change', drawHistory);
$('journal-instrument').addEventListener('change', drawHistory);
$('journal-free').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await post('api/journal/note', { strategy: state.strategy, text: $('journal-free-text').value,
                                     mark: $('journal-free-mark').value });
    $('journal-free-text').value = '';
    open(state.strategy, true);
  } catch (error) {
    $('journal-note').textContent = String(error.message || error);
  }
});

ask('api/journals').then((held) => showList(held.journals))
  .catch((error) => { $('journal-note').textContent = String(error.message || error); });
const wanted = new URLSearchParams(location.search).get('strategy');
if (wanted) open(wanted);
