/*
 * The data: what the stores hold, the candle exports waiting to be imported,
 * and the economic calendar. It was the backtest page's dialog; it is a page
 * of its own now that the simulation is the home page.
 *
 * Times are epoch milliseconds for a naive UTC instant, formatted back with
 * the getUTC* accessors, as in app.js.
 */

const $ = (id) => document.getElementById(id);

function day(ms) {
  if (ms === null || ms === undefined) return '';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}

async function ask(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

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

// the stores, for the head of the page: see showLoaded()
let instruments = [];
async function loadStores() {
  instruments = (await ask('api/stores')).instruments || [];
}

/* ----------------------------------------------------------- the market */

// Where the stores and the calendar are, and whether this server writes them
// (data/market.py). A server that only reads has nothing to import: the
// writer does that, and the buttons for it go. The writer's sources are
// data/sources.py; the last run of each is shown under them.
let marketRunning = false;
function showMarket(state) {
  $('market-dir').value = state.dir;
  $('market-writer').checked = state.writer;
  $('market-state').textContent = `candles and calendar in ${state.dir} \u00b7 `
    + (state.writer ? 'this server writes them'
      : 'read only: imports and pushes go to the server that writes them');
  for (const id of ['data-actions', 'calendar-how', 'calendar-actions', 'market-sources', 'market-run']) {
    $(id).hidden = !state.writer;
  }
  const provider = $('market-provider');
  if (!provider.options.length) {
    for (const name of state.providers) provider.add(new Option(name, name));
  }
  provider.value = state.provider || 'oanda';
  for (const kind of ['candles', 'calendar']) {
    $(`market-${kind}`).value = state[kind].source;
    $(`market-${kind}-every`).value = state[kind].every;
  }
  $('market-upstream').value = state.upstream.url;
  $('market-token').value = '';
  $('market-token').placeholder = state.upstream.token ? 'kept: type a new one to change it' : '';
  marketFields();
  const runs = Object.entries(state.runs).filter(([, run]) => run.at);
  $('market-runs').hidden = !runs.length;
  $('market-runs').textContent = runs.map(([kind, run]) => `${kind} ${new Date(run.at).toISOString().slice(0, 16).replace('T', ' ')} UTC`
    + ` ${run.running ? 'running' : run.ok ? 'ok' : 'failed'}\n` + run.lines.map((l) => `  ${l}`).join('\n')).join('\n');
  marketRunning = runs.some(([, run]) => run.running);
}

// the upstream's two fields when a kind comes from it, the broker when the candles do
function marketFields() {
  const sources = [$('market-candles').value, $('market-calendar').value];
  for (const box of document.querySelectorAll('#market-sources [data-for]')) {
    box.hidden = !sources.includes(box.dataset.for);
  }
}
$('market-candles').addEventListener('change', marketFields);
$('market-calendar').addEventListener('change', marketFields);

async function followMarket() {
  while (marketRunning) {
    await new Promise((resolve) => { setTimeout(resolve, 2000); });
    showMarket(await ask('api/market'));
  }
  await loadStores();
  await loadImports();
  showCalendar();
}

$('market-save').addEventListener('click', () => dataAction(async () => {
  const changes = { dir: $('market-dir').value, writer: $('market-writer').checked };
  if (changes.writer) {
    for (const kind of ['candles', 'calendar']) {
      changes[kind] = { source: $(`market-${kind}`).value, every: Number($(`market-${kind}-every`).value) };
    }
    const sources = [changes.candles.source, changes.calendar.source];
    if (sources.includes('upstream')) {
      changes.upstream = { url: $('market-upstream').value, token: $('market-token').value };
    }
    if (sources.includes('providers')) changes.provider = $('market-provider').value;
  }
  showMarket(await post('api/market', JSON.stringify(changes)));
  dataLog(['market data saved']);
  await loadStores();
  await loadImports();
  showCalendar();
}));

$('market-run').addEventListener('click', () => dataAction(async () => {
  const kinds = ['candles', 'calendar'].filter((kind) => $(`market-${kind}`).value !== 'manual');
  if (!kinds.length) { dataLog(['both are manual: nothing to pull']); return; }
  let state;
  for (const kind of kinds) state = await post('api/market/run', JSON.stringify({ kind }));
  showMarket(state);
  await followMarket();
}));

/* ----------------------------------------------------------- the server */

function showServer(s) {
  $('server-state').textContent = s.accounts === 'real'
    ? `REAL MONEY accounts only (PARITY_DERIVA_ACCOUNTS=real in .env: no page changes it) \u00b7 a form `
      + `trades here once promoted from a demo server with ${s.promoteDays} days, ${s.promoteTrades} `
      + `closed trades and no parity alarm \u00b7 every session stops at a day's loss of `
      + `${s.dailyLossPct}% of the capital traded` + (s.halted ? ' \u00b7 STOPPED today by that limit' : '')
    : 'demo accounts only (PARITY_DERIVA_ACCOUNTS=demo in .env: no page changes it) \u00b7 what '
      + 'worked here is promoted to a real money server from the live page';
  $('server-state').className = s.accounts === 'real' ? 'bad' : '';
  $('promote-box').hidden = s.accounts === 'real';
  $('promote-url').value = s.promoteTo.url;
  $('promote-token').value = '';
  $('promote-token').placeholder = s.promoteTo.token ? 'kept: type a new one to change it' : '';
}

$('promote-save').addEventListener('click', async () => {
  try {
    showServer(await post('api/server/promote-to', JSON.stringify(
      { url: $('promote-url').value, token: $('promote-token').value })));
    $('server-note').textContent = 'saved: the live page offers promote on every session';
  } catch (error) {
    $('server-note').textContent = String(error.message || error);
  }
});

/* --------------------------------------------------------- the calendar */

/*
 * The economic calendar is collected by the browser, not by the service: the
 * site refuses a datacentre address after a handful of requests. This page
 * cannot read forexfactory either - one site may not read another's pages,
 * which is the rule working as intended - so the collector runs on their
 * calendar page, where the weeks are same-origin, and hands back a file.
 *
 * All this end does is say what the file holds and take the one that comes
 * back. Nothing here talks to forexfactory.
 */
function calendarLine(state) {
  if (!state || !state.events) {
    return state && state.start
      ? `no calendar imported - the collector would start at ${state.start}`
      : 'no calendar imported';
  }
  const impacts = Object.keys(state.impacts || {})
    .sort((a, b) => state.impacts[b] - state.impacts[a])
    .map((name) => `${state.impacts[name]} ${name}`)
    .join(', ');
  return `${state.events.toLocaleString()} events, `
    + `${day(state.from)} .. ${day(state.to)}`
    + (impacts ? ` \u00b7 ${impacts}` : '')
    + (state.start ? ` \u00b7 collect from ${state.start}` : '');
}

async function showCalendar() {
  try {
    const state = await ask('api/calendar');
    $('calendar-state').textContent = calendarLine(state);
    // the service's suggestion, which the field is free to override: the
    // history first, and the weeks since once the history is there
    if (state.start && !$('calendar-from').value) {
      $('calendar-from').value = state.start;
    }
  } catch (error) {
    $('calendar-state').textContent = String(error.message || error);
  }
}

$('calendar-copy').addEventListener('click', async () => {
  const button = $('calendar-copy');
  try {
    // from the service and not from /static: the copy carries this service's
    // address and its token, so the weeks are pushed here as they are read
    // instead of being downloaded and imported by hand
    const source = await (await fetch(`api/collector`
      + `?origin=${encodeURIComponent(location.origin)}`
      + `&from=${encodeURIComponent($('calendar-from').value || '')}`)).text();
    await navigator.clipboard.writeText(source);
    button.textContent = 'copied - paste it in the console';
  } catch (error) {
    // the clipboard is refused on some browsers over plain http; the script
    // is still a page you can open and copy by hand
    window.open('api/collector?origin=' + encodeURIComponent(location.origin)
                + '&from=' + encodeURIComponent($('calendar-from').value || ''),
                '_blank');
    button.textContent = 'opened it - copy it by hand';
  }
  setTimeout(() => { button.textContent = 'copy the collector'; }, 6000);
});

$('calendar-import').addEventListener('click', () => dataAction(async () => {
  const file = ($('calendar-file').files || [])[0];
  if (!file) { dataLog(['choose the calendar.csv the collector downloaded']); return; }
  dataLog([`importing ${file.name} ...`]);
  const done = await post('api/calendar', await file.text());
  dataLog([`${done.read} rows read, ${done.added} new, `
           + `${done.events} in ${done.path}`]);
  showCalendar();
}));

function dataLog(lines) {
  const log = $('data-log');
  log.hidden = !lines.length;
  log.textContent = lines.join('\n');
}

function fileSize(bytes) {
  return bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB`
                          : `${Math.ceil(bytes / 1024)} KB`;
}

// the dialog's head: one line per store, the series it keeps and, dimmer,
// the ones built from them on request
function showLoaded() {
  const box = $('data-loaded');
  box.textContent = '';
  const rows = instruments;
  if (!rows.length) { box.textContent = 'nothing loaded: the stores are empty'; return; }
  for (const row of rows) {
    const line = document.createElement('div');
    const kept = row.granularities.filter((g) => !g.derivedFrom);
    const built = row.granularities.filter((g) => g.derivedFrom);
    line.textContent = `${row.instrument}  ` + kept.map((g) =>
      `${g.granularity} ${g.bars} bars ${day(g.from)} .. ${day(g.to)}`).join(' · ');
    if (built.length) {
      const dim = document.createElement('span');
      dim.className = 'dim';
      dim.textContent = `  + ${built.map((g) => g.granularity).join(' ')} built from ${built[0].derivedFrom}`;
      line.appendChild(dim);
    }
    box.appendChild(line);
  }
}

async function loadImports() {
  showLoaded();
  const { directory, sets } = await ask('api/imports');
  $('data-dir').textContent = `import folder: ${directory}`;
  const body = $('data-rows');
  body.textContent = '';
  $('data-all').checked = false;
  // one row per -ASK/-BID pair, the unit the import reads; a side that is
  // not there is named, since the import skips a set without both
  for (const s of sets) {
    const row = document.createElement('tr');
    // a set is picked here and imported by name; one missing a side cannot
    // be imported, so it has no box to tick
    const pick = document.createElement('td');
    if (s.complete) {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.className = 'data-pick';
      box.value = s.set;
      box.setAttribute('aria-label', `import ${s.set}`);
      pick.appendChild(box);
    }
    row.appendChild(pick);
    // a click anywhere on the row ticks its box, the way a list is used
    row.addEventListener('click', (event) => {
      const box = row.querySelector('input.data-pick');
      if (box && event.target !== box) box.checked = !box.checked;
    });
    for (const side of [null, 'instrument', 'granularity', 'ASK', 'BID']) {
      const cell = document.createElement('td');
      if (side === null) cell.textContent = s.set;
      else if (side === 'instrument' || side === 'granularity') cell.textContent = s[side];
      else if (s.sides[side] !== undefined) cell.textContent = fileSize(s.sides[side]);
      else { cell.textContent = 'missing'; cell.className = 'missing'; }
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  if (!sets.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.textContent = 'no import sets here yet';
    row.appendChild(cell);
    body.appendChild(row);
  }
}

// busy for the length of one action, so a second press cannot start another
async function dataAction(work) {
  const buttons = [$('data-upload'), $('data-import'), $('calendar-import')];
  for (const b of buttons) b.disabled = true;
  try {
    await work();
  } catch (error) {
    dataLog([String(error.message || error)]);
  } finally {
    for (const b of buttons) b.disabled = false;
  }
}

// Follow the background import until it ends: the bar is bytes of CSV read
// of the bytes the sets still to import hold. Sets already imported are not
// read at all, so a run of nothing new finishes at once.
async function followImport(status) {
  const box = $('data-progress-box');
  box.hidden = false;
  while (status.running) {
    const share = status.total ? status.done / status.total : 0;
    $('data-progress').value = share;
    $('data-progress-text').textContent = `${Math.round(share * 100)}%  ${status.text}`;
    await new Promise((resolve) => setTimeout(resolve, 500));
    status = await ask('api/imports/status');
  }
  box.hidden = true;
  dataLog(status.lines.concat(status.ok ? [] : ['import reported a problem, see above']));
  // the stores may hold new series or a longer range now
  await loadStores();
  showLoaded();
}

$('data-all').addEventListener('change', () => {
  for (const box of document.querySelectorAll('#data-rows input.data-pick')) {
    box.checked = $('data-all').checked;
  }
});

$('data-upload').addEventListener('click', () => dataAction(async () => {
  const files = Array.from($('data-file').files || []);
  if (!files.length) { dataLog(['choose a CSV or JSON on this computer to upload first']); return; }
  const lines = [];
  for (const file of files) {
    dataLog(lines.concat([`uploading ${file.name} ...`]));
    try {
      const done = await post(`api/imports/upload?name=${encodeURIComponent(file.name)}`, file);
      lines.push(`${done.name}: ${fileSize(done.bytes)} uploaded`);
    } catch (error) {
      lines.push(`${file.name}: ${error.message || error}`);
    }
  }
  $('data-file').value = '';
  dataLog(lines);
  await loadImports();
}));

$('data-import').addEventListener('click', () => dataAction(async () => {
  const sets = Array.from(document.querySelectorAll('#data-rows input.data-pick:checked'))
    .map((box) => box.value);
  if (!sets.length) { dataLog(['tick at least one import set in the table']); return; }
  dataLog([]);
  await followImport(await post('api/imports/run', JSON.stringify({ sets })));
}));

/* ------------------------------------------------------ the AI assistants */

/*
 * The MCP door (web/mcp.py): the token an assistant connects with, shown
 * once when it is made, and the strategies the assistants wrote. A draft is
 * only ever backtested in a sandbox; enabling it makes it a strategy like the
 * others - simulations, sets, live sessions - so that step is here, after
 * reading the code, where no assistant can reach.
 */
const mcpSay = (text) => { $('mcp-note').textContent = text; };

// what can be done with a strategy in its state: on its row, and but for view
// in the head of its code's dialog
function mcpButtons(s, view) {
  const actions = s.state === 'draft' ? [['enable', 'yes'], ['delete', 'delete']]
    : [['disable', 'stop'], ['delete', 'delete']];
  if (view) actions.unshift(['view', 'view']);
  if (s.state !== 'draft' && s.proposed && !s.pull) actions.push(['pull', 'upload', 'pull request']);
  return actions.map(([action, icon, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label || action;
    button.dataset.action = action;
    button.dataset.icon = icon;
    button.dataset.name = s.name;
    return button;
  });
}
// the rows last shown, by name: what the code's dialog says above it. An
// indicator's has kind 'indicators'
let mcpStrategies = {};

// the rows of one table, strategies or indicators
function mcpFill(rows, items) {
  rows.textContent = '';
  for (const s of items) {
    const tr = rows.insertRow();
    const drawn = s.kind !== 'indicators' ? ''
      : s.panel ? 'in its own strip \u00b7 ' : 'on the candles \u00b7 ';
    for (const text of [s.name, s.state, s.client || 'unknown', s.submitted ? day(s.submitted) : '',
      drawn + (s.description || '')]) {
      tr.insertCell().textContent = text;
    }
    // the server it was written on (its stamp), and where it is on its way
    // to the public repository: proposed by the assistant, then its pull request
    tr.cells[0].title = s.server ? `written on parity-deriva ${s.server}` : 'written before versions';
    if (s.pull) {
      const link = document.createElement('a');
      link.href = s.pull.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = `PR #${s.pull.number}`;
      tr.cells[1].append(' \u00b7 ', link);
    } else if (s.proposed) {
      tr.cells[1].append(' \u00b7 proposed');
      tr.cells[1].title = s.proposed.note;
    }
    tr.cells[4].title = 'double click: the whole description';
    tr.insertCell().append(...mcpButtons(s, true));
  }
}

async function showMcp(state) {
  state = state || await ask('api/mcp');
  $('mcp-url').textContent = new URL('mcp', location.href).href;
  $('mcp-state').textContent = (state.secret ? 'a token is set' : 'no token yet: make one to connect an assistant')
    + (state.clients.length ? ` \u00b7 connected: ${state.clients.join(', ')}` : '');
  $('mcp-disconnect').disabled = !state.clients.length;
  $('mcp-show').disabled = !state.secret;
  const keys = $('mcp-key-rows');
  keys.textContent = '';
  for (const k of state.keys || []) {
    const tr = keys.insertRow();
    tr.insertCell().textContent = k.name;
    tr.insertCell().textContent = k.role;
    const code = document.createElement('code');
    code.textContent = k.token;
    tr.insertCell().appendChild(code);
    const cell = tr.insertCell();
    for (const [label, icon, act] of [['copy', 'copy', 'copy'], ['revoke', 'delete', 'drop']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      Object.assign(button.dataset, { icon, act, name: k.name, token: k.token });
      cell.appendChild(button);
    }
  }
  $('mcp-keys').hidden = !(state.keys || []).length;
  const rows = $('mcp-rows');
  const indicators = (state.indicators || []).map((s) => ({ ...s, kind: 'indicators' }));
  mcpStrategies = Object.fromEntries([...state.strategies, ...indicators].map((s) => [s.name, s]));
  mcpFill(rows, state.strategies);
  $('mcp-strategies').hidden = !state.strategies.length;
  mcpFill($('mcp-indicator-rows'), indicators);
  $('mcp-indicators').hidden = !indicators.length;
  const asked = $('mcp-request-rows');
  asked.textContent = '';
  for (const r of state.requests || []) {
    const tr = asked.insertRow();
    tr.insertCell().textContent = r.id;
    tr.insertCell().textContent = day(r.submitted);
    tr.insertCell().textContent = r.strategy || '';
    const what = tr.insertCell();
    const title = document.createElement('strong');
    title.textContent = r.title;
    what.append(title, document.createElement('br'), r.description);
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'done';
    button.dataset.icon = 'yes';
    button.dataset.id = r.id;
    button.title = 'answered - made, or not to be made: it leaves the list';
    tr.insertCell().appendChild(button);
  }
  $('mcp-requests').hidden = !(state.requests || []).length;
  const told = $('mcp-news-rows');
  told.textContent = '';
  for (const n of state.news || []) {
    const tr = told.insertRow();
    tr.insertCell().textContent = n.id;
    tr.insertCell().textContent = day(n.posted);
    const what = tr.insertCell();
    const title = document.createElement('strong');
    title.textContent = n.title;
    what.append(title, n.text);
    what.title = 'double click: all of it';
    // how far the assistants got: a new version of a strategy is it done
    const done = n.strategies.filter((s) => s.updated).length;
    tr.insertCell().textContent = n.strategies.length
      ? `${done} of ${n.strategies.length} updated: `
        + n.strategies.map((s) => (s.updated ? '\u2713 ' : '') + (s.latest || `${s.name} (none)`)).join(', ')
      : '';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'remove';
    button.dataset.icon = 'delete';
    button.dataset.id = n.id;
    button.title = 'done with: it leaves the list, and the assistants are no longer told of it';
    tr.insertCell().appendChild(button);
  }
  $('mcp-news').hidden = !(state.news || []).length;
}

$('calendar-format-open').addEventListener('click', () => $('calendar-format').showModal());
$('draft-format-open').addEventListener('click', () => $('draft-format').showModal());

// a yes or no asked on the page, as in live.js; the yes says what it does
function askUser(text, yes, icon) {
  $('ask-text').textContent = text;
  $('ask-yes').textContent = yes;
  $('ask-yes').dataset.icon = icon;
  $('ask-dialog').returnValue = '';
  return new Promise((resolve) => {
    $('ask-dialog').addEventListener('close', () => resolve($('ask-dialog').returnValue === 'yes'),
                                     { once: true });
    $('ask-dialog').showModal();
    $('ask-yes').focus();
  });
}

$('mcp-news-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  try {
    await showMcp(await post('api/mcp/news/drop', JSON.stringify({ id: button.dataset.id })));
    mcpSay(`${button.dataset.id}: removed`);
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

$('mcp-request-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  try {
    await showMcp(await post('api/mcp/request', JSON.stringify({ id: button.dataset.id })));
    mcpSay(`${button.dataset.id}: done`);
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

// a strategy or a news is one line; a double click opens it on all of it
function openRow(event) {
  if (event.target.closest('button, a')) return;
  event.target.closest('tr').classList.toggle('open');
  getSelection().removeAllRanges();
}
$('mcp-rows').addEventListener('dblclick', openRow);
$('mcp-indicator-rows').addEventListener('dblclick', openRow);
$('mcp-news-rows').addEventListener('dblclick', openRow);

// the buttons on a row, and the same ones in the code's dialog, which closes
// once what they do is done
async function mcpAct(event) {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const { name, action } = button.dataset;
  try {
    if (action === 'view') {
      const found = await ask('api/mcp/source?name=' + encodeURIComponent(name));
      const s = mcpStrategies[name] || {};
      $('source-title').textContent = name;
      $('source-actions').replaceChildren(...mcpButtons(s, false));
      $('source-sub').textContent = [s.state, `by ${s.client || 'unknown'}`,
        s.submitted ? day(s.submitted) : '',
        found.problems.length ? `${found.problems.length} problems` : 'no problems found']
        .filter(Boolean).join(' \u00b7 ');
      $('mcp-source').textContent = (found.problems.length
        ? `# problems:\n# ${found.problems.join('\n# ')}\n\n` : '') + found.source;
      $('source-download').onclick = () => {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([found.source], { type: 'text/x-python' }));
        // as the file is on the server: NAME@N.py for the version NAME N
        link.download = `${name.replace(' ', '@')}.py`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      };
      $('source-dialog').showModal();
      return;
    }
    if (action === 'pull') {
      const s = mcpStrategies[name] || {};
      if (!confirm(`Open a pull request of ${name} on the public repository? Its code and its `
        + `description go out, public. The assistant's note:\n\n${(s.proposed || {}).note || ''}`)) return;
      mcpSay(`${name}: opening the pull request\u2026`);
      const state = await post('api/mcp/pull', JSON.stringify({ name }));
      $('source-dialog').close();
      await showMcp(state);
      const made = [...state.strategies, ...(state.indicators || [])].find((x) => x.name === name);
      mcpSay(made && made.pull ? `${name}: pull request ${made.pull.url}` : `${name}: done`);
      return;
    }
    const enabled = (mcpStrategies[name] || {}).state === 'enabled';
    const indicator = (mcpStrategies[name] || {}).kind === 'indicators';
    let asked = {
      enable: indicator ? `Enable ${name}? Strategies can take it then, and the enabled ones run it `
        + 'inside the service. Read its code first.'
        : `Enable ${name}? It will run inside the service: in the simulations, the sets and `
        + 'the live sessions. Read its code first.',
      disable: `Disable ${name}? It goes back to being a draft.`,
      delete: enabled ? `Delete ${name}? It is enabled: it is disabled first, then deleted. `
        + 'Its saved backtests stay.' : `Delete the draft ${name}? Its saved backtests stay.`,
    }[action];
    // turning off an enabled one: what trades it or simulates it now, said
    // first - nothing of it is stopped here (web/mcp.py uses)
    if (action === 'disable' || (action === 'delete' && enabled)) {
      const { uses } = await ask('api/mcp/uses?name=' + encodeURIComponent(name));
      if (uses.length) asked += `\n\nIn use now:\n- ${uses.join('\n- ')}\n\nTurn it off all the same?`;
    }
    if (!await askUser(asked, action, { enable: 'yes', disable: 'stop' }[action] || 'delete')) return;
    await showMcp(await post('api/mcp/strategy', JSON.stringify({ name, action })));
    $('source-dialog').close();
    mcpSay(`${name}: ${action}d`);
  } catch (error) {
    mcpSay(String(error.message || error));
  }
}
$('mcp-rows').addEventListener('click', mcpAct);
$('mcp-indicator-rows').addEventListener('click', mcpAct);
$('source-actions').addEventListener('click', mcpAct);

// each file named as download named it, NAME@N.py: back as NAME, whose next
// version it becomes (a browser's " (1)" for a second copy goes too)
async function mcpImport(input, kind) {
  const files = [...$(input).files];
  if (!files.length) { mcpSay('choose the .py files to import'); return; }
  const said = [];
  for (const file of files) {
    const name = file.name.replace(/\.py$/i, '').replace(/ \(\d+\)$/, '').replace(/[@ ]\d+$/, '');
    try {
      await post('api/mcp/import', JSON.stringify({ name, source: await file.text(), kind }));
      said.push(`${name}: imported`);
    } catch (error) {
      said.push(`${name}: ${error.message || error}`);
    }
  }
  $(input).value = '';
  mcpSay(said.join('\n'));
  await showMcp();
}
$('mcp-import').addEventListener('click', () => mcpImport('mcp-file', 'strategies'));
$('mcp-indicator-import').addEventListener('click', () => mcpImport('mcp-indicator-file', 'indicators'));

// the token in its box, with its copy button, and the button to hide it
function mcpShowToken(secret) {
  $('mcp-token-text').textContent = secret;
  $('mcp-token').hidden = false;
  $('mcp-show').textContent = 'hide token';
}

async function mcpNewToken() {
  try {
    mcpShowToken((await post('api/mcp/secret', '{}')).secret);
    mcpSay('copy it into the connector or the editor');
    await showMcp();
  } catch (error) {
    mcpSay(String(error.message || error));
  }
}

$('mcp-secret').addEventListener('click', async () => {
  const state = await ask('api/mcp');
  if (state.secret && !await askUser('A new token disconnects every assistant connected with the old one.',
    'new token', 'add')) return;
  await mcpNewToken();
});

// shown again on asking, hidden again on a second press; a token made before
// the page kept them cannot be, so a new one is offered instead
$('mcp-show').addEventListener('click', async () => {
  if (!$('mcp-token').hidden) {
    $('mcp-token').hidden = true;
    $('mcp-show').textContent = 'show token';
    return;
  }
  try {
    const { secret } = await post('api/mcp/secret/show', '{}');
    if (secret) { mcpShowToken(secret); mcpSay(''); return; }
    if (await askUser('This token was made before tokens could be shown again, so it cannot be. '
      + 'Make a new one? Every assistant connected with the old one will have to connect again.',
      'new token', 'add')) await mcpNewToken();
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

const mcpCopy = (text) => navigator.clipboard.writeText(text)
  .then(() => mcpSay('copied'), () => mcpSay('copy it by hand: the browser refused'));
$('mcp-copy').addEventListener('click', () => mcpCopy($('mcp-token-text').textContent));
$('mcp-url-copy').addEventListener('click', () => mcpCopy($('mcp-url').textContent));

$('mcp-key-add').addEventListener('click', async () => {
  try {
    await showMcp(await post('api/mcp/keys', JSON.stringify(
      { name: $('mcp-key-name').value, role: $('mcp-key-role').value })));
    $('mcp-key-name').value = '';
    mcpSay('made: copy its token into the program, with the connector address');
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

$('mcp-key-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.act === 'copy') { mcpCopy(button.dataset.token); return; }
  if (!await askUser(`Revoke the token of ${button.dataset.name}? It stops working at once.`,
    'revoke', 'delete')) return;
  try {
    await showMcp(await post('api/mcp/keys/drop', JSON.stringify({ name: button.dataset.name })));
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

$('mcp-disconnect').addEventListener('click', async () => {
  if (!await askUser('Disconnect every assistant? Each one will have to connect again with the token.',
    'disconnect all', 'close')) return;
  try {
    await showMcp(await post('api/mcp/disconnect', '{}'));
    mcpSay('disconnected');
  } catch (error) {
    mcpSay(String(error.message || error));
  }
});

showMcp().catch((error) => mcpSay(String(error.message || error)));
ask('api/server').then(showServer).catch((error) => { $('server-note').textContent = String(error.message || error); });
ask('api/market').then((state) => { showMarket(state); if (marketRunning) followMarket(); })
  .catch((error) => dataLog([String(error.message || error)]));
showCalendar();
loadStores().then(loadImports).catch((error) => dataLog([String(error.message || error)]));
// an import started earlier, from this page or another, is picked up
ask('api/imports/status').then((status) => {
  if (status.running) dataAction(() => followImport(status));
}).catch(() => {});
