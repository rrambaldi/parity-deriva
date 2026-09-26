/*
 * The logs page: pick one of the logs the service lists (web/logs.py), read
 * its last lines, filter them, and follow it. The log open is in the address
 * (#<path>), so a link opens it.
 */

const $ = (id) => document.getElementById(id);
const GROUPS = { cron: 'cron jobs', service: 'this service', live: 'live sessions' };
// what web.log is: said here and not by the service, so the page puts it in its language
const SERVICE = "this service: requests, live sessions started and stopped, the market data's timers";
let known = [];
let timer = null;

const size = (n) => n === null ? t('not written yet')
  : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} kB` : `${(n / 1048576).toFixed(1)} MB`;
const when = (ms) => ms === null ? '' : new Date(ms).toLocaleString();

async function list() {
  const reply = await fetch('api/logs');
  known = (await reply.json()).logs || [];
  const pick = $('log-pick');
  const was = pick.value || decodeURIComponent(location.hash.slice(1));
  pick.textContent = '';
  for (const [group, label] of Object.entries(GROUPS)) {
    const rows = known.filter((l) => l.group === group);
    if (!rows.length) continue;
    const box = document.createElement('optgroup');
    box.label = t(label);
    for (const log of rows) {
      const option = document.createElement('option');
      option.value = log.path || '';
      option.disabled = !log.path;
      option.textContent = `${log.name}${log.path ? '' : ' ' + t('(no log file)')}`
        + (log.modified ? ` · ${when(log.modified)}` : '');
      box.appendChild(option);
    }
    pick.appendChild(box);
  }
  const first = known.find((l) => l.path);
  pick.value = known.some((l) => l.path && l.path === was) ? was : (first ? first.path : '');
}

async function read() {
  const pick = $('log-pick');
  if (!pick.value) {
    $('log-about').textContent = t('no log to show');
    return;
  }
  history.replaceState(null, '', '#' + encodeURIComponent(pick.value));
  const reply = await fetch(`api/logs/tail?path=${encodeURIComponent(pick.value)}&lines=${$('log-lines').value}`);
  const got = await reply.json();
  if (!reply.ok) {
    $('log-about').textContent = got.error || t('error {status}', { status: reply.status });
    $('log-text').textContent = '';
    return;
  }
  $('log-about').textContent = `${got.path} · ${size(got.size)}`
    + (got.modified ? ' · ' + t('last written {when}', { when: when(got.modified) }) : '')
    + '\n' + (got.group === 'service' ? t(SERVICE) : got.about);
  show(got.lines);
}

let shown = [];
function show(lines) {
  if (lines) shown = lines;
  const text = $('log-text');
  const bottom = text.scrollTop + text.clientHeight >= text.scrollHeight - 4;
  const wanted = $('log-filter').value.toLowerCase();
  const rows = wanted ? shown.filter((l) => l.toLowerCase().includes(wanted)) : shown;
  text.textContent = rows.length ? rows.join('\n') : t(wanted ? 'no line has that text' : 'the file is empty');
  // a log is read from the end: stay there, unless the reader scrolled up
  if (bottom || lines === undefined || $('log-follow').checked) text.scrollTop = text.scrollHeight;
}

function follow() {
  clearInterval(timer);
  timer = $('log-follow').checked ? setInterval(read, 5000) : null;
}

$('log-pick').addEventListener('change', () => { shown = []; read(); });
$('log-lines').addEventListener('change', read);
$('log-filter').addEventListener('input', () => show());
$('log-follow').addEventListener('change', follow);
$('log-refresh').addEventListener('click', async () => { await list(); read(); });
window.addEventListener('hashchange', () => {
  const path = decodeURIComponent(location.hash.slice(1));
  if (path && path !== $('log-pick').value) { $('log-pick').value = path; read(); }
});

list().then(read).catch((error) => {
  $('log-about').textContent = t('the service did not answer: {error}', { error: String(error) });
});
