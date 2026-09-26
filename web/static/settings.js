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

/* ----------------------------------------------------- the data quality */

// what the archive holds, what to record, and two sources of one series set
// against each other (data/archive.py): the stores ('') or an archive's provider
function showQuality(state) {
  $('record-box').hidden = !state.writer;
  $('record-feeds').value = state.record.feeds.join('\n');
  $('record-every').value = state.record.every;
  $('archive-state').textContent = state.archives.length
    ? `the archive keeps the bars of ${state.archives.join(', ')} (MARKET/archive), each a source below`
    : 'the archive is empty: record a feed, or archive what the live sessions saw';
  for (const id of ['q-a', 'q-b']) {
    const box = $(id);
    const was = box.value;
    box.textContent = '';
    box.add(new Option('the stores (downloaded)', ''));
    for (const name of state.archives) box.add(new Option(`${name}, as served`, name));
    box.value = [...box.options].some((o) => o.value === was) ? was
      : (id === 'q-b' && state.archives[0]) || '';
  }
}

function qualityInstruments() {
  const box = $('q-instrument');
  if (!box.options.length) for (const row of instruments) box.add(new Option(row.instrument, row.instrument));
  const row = instruments.find((r) => r.instrument === box.value);
  const tf = $('q-granularity');
  const was = tf.value;
  tf.textContent = '';
  for (const g of (row ? row.granularities : [])) tf.add(new Option(g.granularity, g.granularity));
  if ([...tf.options].some((o) => o.value === was)) tf.value = was;
}
$('q-instrument').addEventListener('change', qualityInstruments);

function drawDays(days, standout) {
  const canvas = $('q-days');
  canvas.hidden = !days.length;
  if (!days.length) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = 140;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const top = Math.max(...days.map((d) => d.p95)) || 1;
  const left = 46, bottom = 16, w = (width - left) / days.length;
  const odd = new Set(standout.map((d) => d.day));
  ctx.fillStyle = color('--text-3');
  ctx.font = '11px ' + color('--font-mono');
  ctx.fillText(`${top.toFixed(2)} pip`, 0, 12);
  ctx.fillText('p95 |ΔClose| a day', left, height - 2);
  days.forEach((d, i) => {
    const h = (height - bottom - 8) * d.p95 / top;
    ctx.fillStyle = odd.has(d.day) ? color('--down') : color('--entry');
    ctx.fillRect(left + i * w, height - bottom - h, Math.max(1, w - 1), h);
  });
}

$('record-save').addEventListener('click', () => dataAction(async () => {
  const feeds = $('record-feeds').value.split('\n').map((l) => l.trim()).filter(Boolean);
  const state = await post('api/market', JSON.stringify({ record: { feeds, every: Number($('record-every').value) } }));
  showMarket(state);
  showQuality(state);
  dataLog([feeds.length ? `recording ${feeds.length} feed${feeds.length === 1 ? '' : 's'} every ${state.record.every} min`
    : 'recording nothing']);
}));

$('archive-now').addEventListener('click', () => dataAction(async () => {
  const state = await post('api/market/archive', '{}');
  showQuality(state);
  dataLog(state.lines);
}));

$('q-compare').addEventListener('click', async () => {
  $('q-note').textContent = 'comparing…';
  try {
    const r = await post('api/market/compare', JSON.stringify({
      instrument: $('q-instrument').value, granularity: $('q-granularity').value,
      from: $('q-from').value, to: $('q-to').value, a: $('q-a').value, b: $('q-b').value }));
    const name = (s) => s || 'the stores';
    const three = (s) => (s ? `median ${s.median} · p95 ${s.p95} · max ${s.max}` : 'n/a');
    $('q-numbers').hidden = false;
    $('q-numbers').textContent = [
      `${r.instrument} ${r.granularity} · ${name(r.b)} against ${name(r.a)} · ${r.from || '?'} .. ${r.to || '?'} · pip ${r.pip}`,
      `bars: ${r.bars.both} in both · ${r.bars.onlyA} only in ${name(r.a)} · ${r.bars.onlyB} only in ${name(r.b)}`,
      `ΔClose (pips): ${three(r.dClose)}`,
      `ΔHigh  (pips): ${three(r.dHigh)}`,
      `ΔLow   (pips): ${three(r.dLow)}`,
      `spread median (pips): ${name(r.a)} ${r.spread.a ?? 'n/a'} · ${name(r.b)} ${r.spread.b ?? 'n/a'}`,
      `clock: ${r.shift === null ? 'n/a' : r.shift === 0 ? 'the bars open at the same time'
        : `${name(r.b)} fits best moved ${r.shift} bar${Math.abs(r.shift) === 1 ? '' : 's'} - a bar-open convention, a DST or a server clock; the numbers are after that move`}`,
      `days that stand out (p95 over 3x the usual): ${r.standout.map((d) => day(d.day)).join(', ') || 'none'}`,
    ].join('\n');
    drawDays(r.days, r.standout);
    $('q-note').textContent = '';
  } catch (error) {
    $('q-numbers').hidden = $('q-days').hidden = true;
    $('q-note').textContent = String(error.message || error);
  }
});

$('q-impact').addEventListener('click', async () => {
  $('q-note').textContent = 'running the favourite on both sources…';
  try {
    const r = await post('api/market/impact', JSON.stringify(
      { favourite: $('q-favourite').value, a: $('q-a').value, b: $('q-b').value }));
    const side = (s) => `${s.source || 'the stores'}: ${s.trades} trades, net ${Number(s.net || 0).toFixed(2)}`;
    $('q-note').textContent = `${r.strategy} ${r.from} .. ${r.to} · ${side(r.a)} · ${side(r.b)} · `
      + (r.same ? 'the same trades' : `they part from trade ${r.first.trade}`);
  } catch (error) {
    $('q-note').textContent = String(error.message || error);
  }
});

ask('api/favourites').then(({ favourites }) => {
  for (const f of favourites) {
    const x = f.fields || {};
    $('q-favourite').add(new Option(`${x.strategy} ${x.instrument} ${x.granularity}${f.note ? ' · ' + f.note : ''}`, f.id));
  }
}).catch(() => {});

/* ------------------------------------------------------- the languages */

// every language the pages can be in, how much of them it says, and a
// catalogue to download, upload or drop (web/i18n.py)
async function showLanguages(state) {
  const { languages } = state || await ask('api/i18n');
  const body = $('lang-rows');
  body.textContent = '';
  for (const l of languages) {
    const row = body.insertRow();
    row.insertCell().textContent = l.code;
    row.insertCell().textContent = l.name + (l.uploaded ? ' · uploaded' : l.builtin && l.code !== 'en' ? ' · built in' : '');
    const share = row.insertCell();
    share.className = 'num';
    share.textContent = `${Math.round(100 * l.share)}%`;
    const get = row.insertCell();
    if (l.code !== 'en') {
      const link = document.createElement('a');
      link.href = `api/i18n/${encodeURIComponent(l.code)}/download`;
      link.download = `${l.code}.json`;
      link.textContent = 'download';
      get.appendChild(link);
    }
    const drop = row.insertCell();
    if (l.uploaded) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.code = l.code;
      button.dataset.icon = 'delete';
      button.textContent = 'delete';
      drop.appendChild(button);
    }
  }
}

$('lang-rows').addEventListener('click', async (event) => {
  const code = event.target.dataset && event.target.dataset.code;
  if (!code) return;
  if (!await askUser(`Delete the uploaded ${code} catalogue? A built-in one of that code stays.`, 'delete', 'delete')) return;
  try {
    await showLanguages(await post(`api/i18n/${encodeURIComponent(code)}/delete`, '{}'));
    $('lang-note').textContent = 'deleted';
  } catch (error) { $('lang-note').textContent = String(error.message || error); }
});

$('lang-upload').addEventListener('click', async () => {
  const file = ($('lang-file').files || [])[0];
  const code = $('lang-code').value.trim();
  if (!file || !code) { $('lang-note').textContent = "give the language's code and choose its catalogue"; return; }
  try {
    await showLanguages(await post(`api/i18n/${encodeURIComponent(code)}`, await file.text()));
    $('lang-note').textContent = `${code} uploaded: choose it in the header`;
  } catch (error) { $('lang-note').textContent = String(error.message || error); }
});

/* ----------------------------------------------------------- the server */

function showServer(s) {
  $('server-state').textContent = s.accounts === 'real'
    ? `REAL MONEY accounts only (PARITY_DERIVA_ACCOUNTS=real in .env: no page changes it) \u00b7 a form `
      + `trades here once promoted by the archive with ${s.promoteDays} days on demo, ${s.promoteTrades} `
      + `closed trades (or ${s.promoteMinTrades} after ${s.promoteSlowDays} days), a net of at least `
      + `${s.promoteMinNet} and no parity alarm \u00b7 every session stops at a day's loss of `
      + `${s.dailyLossPct}% of the capital traded` + (s.halted ? ' \u00b7 STOPPED today by that limit' : '')
    : 'demo accounts only (PARITY_DERIVA_ACCOUNTS=demo in .env: no page changes it)';
  $('server-state').className = s.accounts === 'real' ? 'bad' : '';
  for (const box of document.querySelectorAll('#roles-box input[name="role"]')) {
    box.checked = s.roles.includes(box.value);
  }
  $('trade-box').hidden = !s.roles.includes('archive');
  if (s.roles.includes('archive')) ask('api/trade-servers').then(showTradeServers).catch(serverSay);
}

const serverSay = (error) => { $('server-note').textContent = String(error.message || error); };

function showTradeServers({ servers }) {
  const rows = $('trade-rows');
  rows.textContent = '';
  for (const t of servers) {
    const tr = rows.insertRow();
    const status = t.status || {};
    tr.insertCell().textContent = t.name;
    tr.insertCell().textContent = t.url;
    const accounts = (status.server || {}).accounts;
    const kind = tr.insertCell();
    kind.textContent = accounts === 'real' ? 'REAL MONEY' : accounts === 'demo' ? 'demo' : '?';
    if (accounts === 'real') kind.className = 'bad';
    const read = tr.insertCell();
    read.textContent = !status.at ? 'not yet'
      : `${new Date(status.at).toISOString().slice(0, 16).replace('T', ' ')} UTC`
        + (status.ok ? ` \u00b7 ${(status.sessions || []).length} sessions` : ` \u00b7 ${status.error}`);
    if (status.at && !status.ok) read.className = 'bad';
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.dataset.icon = 'delete';
    drop.dataset.name = t.name;
    drop.textContent = 'remove';
    tr.insertCell().appendChild(drop);
  }
  $('trade-table').hidden = !servers.length;
}

$('roles-save').addEventListener('click', async () => {
  const roles = [...document.querySelectorAll('#roles-box input[name="role"]:checked')].map((b) => b.value);
  try {
    showServer(await post('api/server/roles', JSON.stringify({ roles })));
    $('server-note').textContent = 'saved: the menu and the pages follow at once';
  } catch (error) { serverSay(error); }
});

$('trade-save').addEventListener('click', async () => {
  try {
    showTradeServers(await post('api/trade-servers', JSON.stringify(
      { name: $('trade-name').value, url: $('trade-url').value, token: $('trade-token').value })));
    $('trade-token').value = '';
    $('server-note').textContent = 'saved: the live page pushes forms to it, and shows its sessions once read';
  } catch (error) { serverSay(error); }
});

/* where a set's runs go off this disk (api/storage, web/storage.py): the backup tab */

const backupSay = (error) => { $('backup-note').textContent = String(error.message || error); };

const STORAGE = { gdrive: 'a Google Drive', onedrive: 'a OneDrive' };

function showStorage(s) {
  $('storage-kind').value = s.kind || 'none';
  const b = s.s3 || {};
  $('storage-state').textContent = !s.kind ? 'nothing chosen: the runs stay on this disk'
    : s.from === 'env' ? `the S3 bucket ${b.bucket} of .env (PARITY_DERIVA_S3_*): a choice here goes before it`
      : s.kind === 's3' ? `the S3 bucket ${b.bucket} at ${b.endpoint}`
        : `the folder ${s.folder} of ${STORAGE[s.kind]}` + (s.rclone ? '' : ' - but rclone is not on this server');
  $('s3-endpoint').value = b.endpoint || '';
  $('s3-bucket').value = b.bucket || '';
  $('s3-access').value = b.accessKey || '';
  $('s3-region').value = b.region || '';
  $('s3-prefix').value = b.prefix || '';
  $('s3-secret').value = '';
  $('s3-secret').placeholder = b.secretKey && s.from === 'page' ? 'kept: type a new one to change it' : '';
  $('drive-folder').value = s.folder || 'parity-deriva';
  $('drive-client-id').value = s.clientId || '';
  $('drive-client-secret').value = '';
  $('drive-client-secret').placeholder = s.clientSecret ? 'kept: type it again to run the command' : '';
  $('drive-token').value = '';
  $('drive-token').placeholder = s.token ? 'logged in: paste a new one only to log in again' : '';
  showStorageKind();
  $('storage-sets-box').hidden = !s.kind;
  if (s.kind) readStorageSets();
}

const megabytes = (n) => (n ? `${(n / 1e6).toFixed(1)} MB` : '\u2014');

// what is in the storage, read there now, beside what is here
async function readStorageSets() {
  const body = $('storage-rows');
  body.textContent = '';
  const wait = body.insertRow().insertCell();
  wait.colSpan = 6;
  wait.textContent = 'reading what is there…';
  try { showStorageSets(await ask('api/storage/sets')); }
  catch (error) { wait.textContent = String(error.message || error); }
}

function showStorageSets({ sets }) {
  const body = $('storage-rows');
  body.textContent = '';
  if (!sets.length) {
    const td = body.insertRow().insertCell();
    td.colSpan = 6;
    td.textContent = 'no set saved yet';
    return;
  }
  for (const set of sets) {
    const tr = body.insertRow();
    tr.dataset.id = set.id;
    tr.insertCell().textContent = set.id + (set.name ? ` \u00b7 ${set.name}` : '');
    tr.insertCell().textContent = set.known ? `${set.strategy} \u00b7 ${set.instrument} ${set.granularity}`
      : 'not known here: another server sent it';
    tr.insertCell().textContent = set.saved ? day(set.saved) : '';
    const here = tr.insertCell();
    here.textContent = megabytes(set.here);
    here.className = 'num';
    const there = tr.insertCell();
    there.textContent = megabytes(set.there);
    there.className = 'num';
    if (set.away) there.title = `${set.away} runs there only`;
    const cell = tr.insertCell();
    for (const [label, icon, act, show, title] of [
      ['send', 'upload', 'send', set.known && set.here > 0, 'its runs there and off this disk: one opened comes back by itself'],
      ['bring here', 'import', 'bring', set.away > 0, 'what of it is there only, brought to this disk'],
      ['download', 'import', 'zip', set.known || set.table, 'the set whole in a zip, its runs from here or from there']]) {
      if (!show) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.title = title;
      Object.assign(button.dataset, { icon, act });
      cell.append(button, ' ');
    }
  }
}

$('storage-read').addEventListener('click', readStorageSets);
$('storage-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  const row = event.target.closest('tr[data-id]');
  if (!button || !row) return;
  const id = row.dataset.id;
  if (button.dataset.act === 'zip') {
    // made on the service first, then saved by the browser; a refusal stays on this page
    $('backup-note').textContent = `making the zip of ${id}…`;
    try {
      const response = await fetch(`api/sweeps/${id}/zip`);
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
      const link = document.createElement('a');
      link.href = URL.createObjectURL(await response.blob());
      link.download = `${id}.zip`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 60000);
      $('backup-note').textContent = `${id}.zip saved by the browser`;
    } catch (error) { backupSay(error); }
    return;
  }
  button.disabled = true;
  const send = button.dataset.act === 'send';
  $('backup-note').textContent = send ? `sending ${id}…` : `bringing ${id} here…`;
  try {
    const told = await post(`api/sweeps/${id}`, JSON.stringify(send ? { cold: true } : { warm: true }));
    $('backup-note').textContent = send ? `${id}: ${told.files} runs there, ${megabytes(told.freed)} freed here`
      : `${id}: ${told.files} runs brought here`;
  } catch (error) { backupSay(error); }
  readStorageSets();
});

// the command the user runs on a PC: rclone's own, for access to the files it makes only
function driveCommand() {
  if ($('storage-kind').value === 'onedrive') return 'rclone authorize "onedrive"';
  const id = $('drive-client-id').value.trim();
  const options = id ? { client_id: id, client_secret: $('drive-client-secret').value.trim(), scope: 'drive.file' }
    : { scope: 'drive.file' };
  return `rclone authorize "drive" "${btoa(JSON.stringify(options)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')}"`;
}

function showStorageKind() {
  const kind = $('storage-kind').value;
  $('storage-s3').hidden = kind !== 's3';
  $('storage-drive').hidden = !STORAGE[kind];
  $('storage-google').hidden = kind !== 'gdrive';
  $('drive-command').textContent = driveCommand();
}

$('storage-kind').addEventListener('change', showStorageKind);
for (const id of ['drive-client-id', 'drive-client-secret']) $(id).addEventListener('input', showStorageKind);
$('drive-copy').addEventListener('click', () => navigator.clipboard.writeText($('drive-command').textContent)
  .then(() => { $('backup-note').textContent = 'copied: run it on the PC'; },
    () => { $('backup-note').textContent = 'copy it by hand: the browser refused'; }));

$('storage-save').addEventListener('click', async () => {
  const kind = $('storage-kind').value;
  const body = kind === 's3' ? { kind, endpoint: $('s3-endpoint').value, bucket: $('s3-bucket').value,
    accessKey: $('s3-access').value, secretKey: $('s3-secret').value, region: $('s3-region').value,
    prefix: $('s3-prefix').value }
    : STORAGE[kind] ? { kind, token: $('drive-token').value, folder: $('drive-folder').value,
      clientId: $('drive-client-id').value, clientSecret: $('drive-client-secret').value }
      : { kind: 'none' };
  $('storage-save').disabled = true;
  $('backup-note').textContent = kind === 'none' ? '' : 'writing a test file there…';
  try {
    showStorage(await post('api/storage', JSON.stringify(body)));
    $('backup-note').textContent = kind === 'none' ? 'forgotten: the runs stay on this disk'
      : 'tested and saved: a file was written there, read back and deleted';
  } catch (error) { backupSay(error); }
  $('storage-save').disabled = false;
});

$('trade-rows').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (!await askUser(`Remove the trade server ${button.dataset.name}? Its sessions go on there; this archive stops reading them.`,
    'remove', 'delete')) return;
  try {
    showTradeServers(await post('api/trade-servers', JSON.stringify({ name: button.dataset.name, drop: true })));
  } catch (error) { serverSay(error); }
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
/* ---------------------------------------------------------- the holdout */

// each instrument's cut (web/holdout.py): where it is, how often the gate
// read past it, which saved sets and runs did, and a date to move it to
function showHoldout(held) {
  const rows = $('holdout-rows');
  rows.textContent = '';
  $('holdout-table').hidden = !held.instruments.length;
  $('holdout-say').textContent = held.instruments.length ? ''
    : 'no holdout yet: the first backtest or set on an instrument fixes its cut';
  for (const one of held.instruments) {
    const row = document.createElement('tr');
    const read = one.readBy.sets || one.readBy.runs
      ? `${one.readBy.sets} sets, ${one.readBy.runs} runs` : 'none';
    const own = Object.entries(one.strategies || {}).map(([name, s]) => `${name} ${s.cut}`).join(', ') || '-';
    for (const [text, cls] of [[one.instrument], [one.cut], [String(one.openings), 'num'], [own], [read]]) {
      const cell = document.createElement('td');
      cell.textContent = text;
      if (cls) cell.className = cls;
      row.appendChild(cell);
    }
    const cell = document.createElement('td');
    const when = document.createElement('input');
    when.type = 'date';
    when.value = one.cut;
    when.setAttribute('aria-label', `the holdout of ${one.instrument} from`);
    const go = document.createElement('button');
    go.type = 'button';
    go.textContent = 'move';
    go.addEventListener('click', () => post('api/holdout', JSON.stringify({ instrument: one.instrument, cut: when.value }))
      .then(showHoldout).catch((error) => { $('holdout-say').textContent = String(error.message || error); }));
    cell.append(when, go);
    row.appendChild(cell);
    rows.appendChild(row);
  }
}

/* ------------------------------------------------------ alerts and phones */

// where the urgent alerts go (web/notify.py) and the phones paired (web/phone.py)
function showPhones(p) {
  const on = p.phones.filter((phone) => phone.notifications).length;
  $('alert-channels').textContent = `email ${p.channels.email ? 'on' : 'off'} \u00b7 `
    + `Telegram ${p.channels.telegram ? 'on' : 'off'} \u00b7 ${on} of ${p.phones.length} phones with notifications`;
  $('phone-url').hidden = !p.tradeWithoutUrl;
  $('phone-table').hidden = !p.phones.length;
  const rows = $('phone-rows');
  rows.textContent = '';
  for (const phone of p.phones) {
    const row = document.createElement('tr');
    for (const text of [phone.name, stamp(phone.paired), stamp(phone.seen), phone.notifications ? 'on' : 'off']) {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.appendChild(cell);
    }
    const cell = document.createElement('td');
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.dataset.icon = 'delete';
    drop.textContent = 'revoke';
    drop.addEventListener('click', () => post('api/phones/revoke', JSON.stringify({ id: phone.id }))
      .then(showPhones).catch((error) => { $('phone-note').textContent = String(error.message || error); }));
    cell.appendChild(drop);
    row.appendChild(cell);
    rows.appendChild(row);
  }
}

function stamp(ms) {
  return ms ? new Date(ms).toISOString().slice(0, 16).replace('T', ' ') : '';
}

$('phone-pair').addEventListener('click', async () => {
  try {
    const got = await post('api/phones/pair', '{}');
    const url = `${got.url}?pair=${got.code}`;
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    $('phone-qr-img').src = qr.createDataURL(6, 2);
    $('phone-code').textContent = got.code;
    $('phone-link').textContent = url;
    $('phone-qr').hidden = false;
    $('phone-note').textContent = '';
  } catch (error) {
    $('phone-note').textContent = String(error.message || error);
  }
});

$('alert-test').addEventListener('click', async () => {
  $('alert-note').textContent = 'sending\u2026';
  try {
    const { sent } = await post('api/alerts/test', '{}');
    $('alert-note').textContent = Object.entries(sent).map(([channel, how]) => `${channel}: ${how}`)
      .join(' \u00b7 ');
    showPhones(await ask('api/phones'));
  } catch (error) {
    $('alert-note').textContent = String(error.message || error);
  }
});

/* -------------------------------------------------------------- the tabs */

// one section a tab, as the docs page has them: the address says which (a
// panel, or anything inside one), else the one last opened in this browser
const TAB_KEY = 'parity-deriva.settings-tab';
const tabs = [...document.querySelectorAll('#settings-tabs [role="tab"]')];

function openTab(tab, focus) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
  try { localStorage.setItem(TAB_KEY, tab.getAttribute('aria-controls')); } catch (error) { /* this visit only */ }
}

function follow() {
  let where = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (!where) {
    try { where = document.getElementById(localStorage.getItem(TAB_KEY) || ''); } catch (error) { where = null; }
  }
  const panel = where && where.closest('[role="tabpanel"]');
  if (!panel) return;
  openTab(tabs.find((t) => t.getAttribute('aria-controls') === panel.id));
  if (where !== panel) where.scrollIntoView();
}

for (const tab of tabs) {
  tab.addEventListener('click', () => {
    openTab(tab);
    history.replaceState(null, '', `#${tab.getAttribute('aria-controls')}`);
  });
  // the arrows move along the tabs, as a tab list does
  tab.addEventListener('keydown', (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
    openTab(next, true);
    history.replaceState(null, '', `#${next.getAttribute('aria-controls')}`);
  });
}
window.addEventListener('hashchange', follow);
follow();

showLanguages().catch((error) => { $('lang-note').textContent = String(error.message || error); });
ask('api/server').then(showServer).catch(serverSay);
ask('api/holdout').then(showHoldout).catch((error) => { $('holdout-say').textContent = String(error.message || error); });
ask('api/phones').then(showPhones).catch((error) => { $('phone-note').textContent = String(error.message || error); });
ask('api/storage').then(showStorage).catch(backupSay);
ask('api/market').then((state) => { showMarket(state); showQuality(state); if (marketRunning) followMarket(); })
  .catch((error) => dataLog([String(error.message || error)]));
showCalendar();
loadStores().then(() => { qualityInstruments(); return loadImports(); })
  .catch((error) => dataLog([String(error.message || error)]));
// an import started earlier, from this page or another, is picked up
ask('api/imports/status').then((status) => {
  if (status.running) dataAction(() => followImport(status));
}).catch(() => {});
