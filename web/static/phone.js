/*
 * The phone's page (phone.html, web/phone.py): paired by the code of the
 * settings page's QR, then the sessions of this server and their open
 * trades, every 30 s while it is on screen, and the switch for the
 * notifications. Read only.
 */
const $ = (id) => document.getElementById(id);
const REFRESH = 30000;
let current = null;

async function api(path, body) {
  const answer = await fetch(path, body === undefined ? { credentials: 'same-origin' } : {
    method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', 'X-Parity-Deriva': '1' },
  });
  const data = await answer.json().catch(() => ({}));
  if (!answer.ok) {
    const error = new Error(data.error || answer.statusText);
    error.status = answer.status;
    throw error;
  }
  return data;
}

const money = (v) => v === null || v === undefined ? '' : (v > 0 ? '+' : '') + Number(v).toFixed(2);
const tone = (v) => v > 0 ? 'good' : v < 0 ? 'bad' : '';
const when = (ms) => ms ? new Date(ms).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';

function line(className, text) {
  const box = document.createElement('div');
  if (className) box.className = className;
  box.textContent = text;
  return box;
}

function number(label, value, cls) {
  const box = document.createElement('span');
  box.textContent = `${label} `;
  const v = document.createElement('b');
  v.textContent = value;
  if (cls) v.className = cls;
  box.appendChild(v);
  return box;
}

function draw(s) {
  current = s;
  $('pair').hidden = true;
  $('title').textContent = s.title;
  $('kind').hidden = false;
  $('kind').textContent = s.accounts === 'real' ? 'REAL MONEY' : 'demo';
  $('kind').className = 'kind' + (s.accounts === 'real' ? ' real' : '');
  $('notify-box').hidden = s.notifications && typeof Notification !== 'undefined'
    && Notification.permission === 'granted';

  const list = $('sessions');
  list.textContent = '';
  $('sessions-box').hidden = false;
  if (!s.sessions.length) list.appendChild(line('dim', 'no session running'));
  for (const one of s.sessions) {
    const box = line('session', '');
    box.appendChild(line('name', `${one.strategy} · ${one.instrument} ${one.granularity} · ${one.provider}`));
    const state = one.exited ? 'stopped by itself' : one.running ? 'running' : 'stopped';
    const nums = line('nums', '');
    nums.append(number('today', money(one.today), tone(one.today)), number('total', money(one.net), tone(one.net)),
                number('', state, one.exited ? 'bad' : ''));
    box.appendChild(nums);
    if (one.lastBar) box.appendChild(line('dim', `last candle ${when(one.lastBar.time)}`));
    for (const t of one.open) {
      box.appendChild(line('trade', `${t.units > 0 ? '▲ long' : '▼ short'} ${Math.abs(t.units)}`
        + ` @ ${t.price} · since ${when(t.time)}`));
    }
    if (!one.open.length) box.appendChild(line('dim', 'no trade open'));
    list.appendChild(box);
  }

  const alerts = $('alerts');
  alerts.textContent = '';
  $('alerts-box').hidden = !s.alerts.length;
  for (const a of s.alerts) {
    alerts.appendChild(line(`alert ${a.level}${a.resolved ? ' resolved' : ''}`, `${when(a.at)} · ${a.text}`));
  }
  $('status').textContent = `updated ${when(s.at)}`;
}

function unpaired(message) {
  for (const id of ['notify-box', 'sessions-box', 'alerts-box']) $(id).hidden = true;
  $('pair').hidden = false;
  $('status').textContent = message || '';
}

async function refresh() {
  try {
    draw(await api('api/phone/state'));
  } catch (error) {
    if (error.status === 401) unpaired();
    else $('status').textContent = `not reached: ${error.message}`;
  }
}

async function pair(code) {
  const name = /iPhone|iPad/.test(navigator.userAgent) ? 'iPhone'
    : /Android/.test(navigator.userAgent) ? 'Android' : 'phone';
  try {
    await api('api/phone/pair', { code, name });
    history.replaceState(null, '', location.pathname);
    await refresh();
  } catch (error) {
    unpaired(error.message);
  }
}

// the key the push services check the server's signature with
function keyBytes(text) {
  const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - text.length % 4) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function notifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    $('notify-about').textContent = 'This browser has no web notifications. On an iPhone: iOS 16.4 or '
      + 'later, and open this page from its icon on the home screen (share, "Add to Home Screen").';
    return;
  }
  try {
    const worker = await navigator.serviceWorker.register('phone-sw.js');
    if (await Notification.requestPermission() !== 'granted') {
      $('notify-about').textContent = 'Notifications are off for this page: turn them on in the browser\'s settings.';
      return;
    }
    const subscription = await worker.pushManager.subscribe(
      { userVisibleOnly: true, applicationServerKey: keyBytes(current.vapid) });
    await api('api/phone/subscribe', { subscription: subscription.toJSON() });
    await refresh();
  } catch (error) {
    $('notify-about').textContent = `Notifications not turned on: ${error.message}`;
  }
}

$('pair-form').addEventListener('submit', (event) => {
  event.preventDefault();
  pair($('pair-code').value);
});
$('notify-on').addEventListener('click', notifications);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden) refresh(); }, REFRESH);

const asked = new URLSearchParams(location.search).get('pair');
if (asked) {
  $('pair-code').value = asked;
  pair(asked);
} else {
  refresh();
}
