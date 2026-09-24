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
  if (!files.length) { dataLog(['choose a CSV on this computer to upload first']); return; }
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

showCalendar();
loadStores().then(loadImports).catch((error) => dataLog([String(error.message || error)]));
// an import started earlier, from this page or another, is picked up
ask('api/imports/status').then((status) => {
  if (status.running) dataAction(() => followImport(status));
}).catch(() => {});
