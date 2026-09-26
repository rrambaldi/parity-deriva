/*
 * The first start's page (web/access.py, setup mode). The code from the
 * server's log opens it; then who may open the pages, what this server does
 * and where its market data comes from, all sent at the end in one go
 * (api/setup/finish). A profile - a file, or one of the server's examples -
 * fills the answers in; secrets are never in one and are asked again.
 */

const $ = (id) => document.getElementById(id);
const PROVIDERS = [['google', 'Google'], ['microsoft', 'Microsoft'], ['github', 'GitHub']];
const TOKEN_PC = 'a "pc" token made on the Archive';
const TOKEN_MIRROR = 'a "mirror" token made on the Archive';
let state = null;

async function call(url, body) {
  const options = body === undefined ? { cache: 'no-store' } : {
    method: 'POST', body: JSON.stringify(body),
    headers: { 'X-Parity-Deriva': '1', 'Content-Type': 'application/json' } };
  const reply = await fetch(url, options);
  const payload = await reply.json().catch(() => ({ error: `${reply.status} ${reply.statusText}` }));
  if (!reply.ok || payload.error) {
    const error = new Error(payload.error || `${reply.status} ${reply.statusText}`);
    error.status = reply.status;
    throw error;
  }
  return payload;
}

function say(id, text, bad) {
  $(id).textContent = text || '';
  $(id).classList.toggle('error', !!bad);
}

// a wrong answer, said next to the step it belongs to
function wrong(note, text) {
  const error = new Error(text);
  error.note = note;
  return error;
}

function link(href, text) {
  const a = document.createElement('a');
  a.href = href;
  a.textContent = text;
  return a;
}

const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
const mode = () => checked('mode')[0] || '';
const roles = () => checked('role');
function setRadio(name, value) {
  for (const radio of document.querySelectorAll(`input[name="${name}"]`)) radio.checked = radio.value === value;
}

/* ------------------------------------------------------------- the form */

function buildProviders() {
  for (const [key, label] of PROVIDERS) {
    const box = document.createElement('div');
    box.className = 'provider';
    box.innerHTML = `<label class="inline"><input type="checkbox" id="use-${key}"> ${label}</label>
      <div class="provider-fields" id="fields-${key}" hidden>
        <label>client id <input type="text" id="id-${key}" spellcheck="false" autocomplete="off"></label>
        <label>client secret <input type="password" id="secret-${key}" autocomplete="off"></label>
        ${key === 'microsoft' ? '<label>tenant <input type="text" id="tenant-microsoft" spellcheck="false" value="common"></label>' : ''}
        <p class="callback">callback address to register: <code id="cb-${key}"></code></p>
      </div>`;
    $('providers').appendChild(box);
  }
}

// what is on show follows the answers: each choice opens its own questions
function show() {
  const m = mode();
  // both: the certificate's questions and the accounts' ones
  $('mode-cert').hidden = m !== 'cert' && m !== 'both';
  $('mode-oauth').hidden = m !== 'oauth' && m !== 'both';
  $('mode-none').hidden = m !== 'none';
  const make = checked('ca')[0] !== 'pem';
  $('ca-make').hidden = !make;
  $('ca-pem-box').hidden = make;
  const base = $('public-url').value.trim().replace(/\/+$/, '');
  for (const [key] of PROVIDERS) {
    $(`fields-${key}`).hidden = !$(`use-${key}`).checked;
    $(`cb-${key}`).textContent = base ? `${base}/auth/callback/${key}` : ((state && state.callbacks) || {})[key] || '';
  }
  const held = roles();
  const archive = held.includes('archive');
  $('trade-box').hidden = !held.includes('trade');
  $('real-warn').hidden = checked('accounts')[0] !== 'real';
  $('archive-box').hidden = archive || !held.length;
  const kind = held.includes('test') ? TOKEN_PC : TOKEN_MIRROR;
  if ($('archive-token-kind').dataset.kind !== kind) {
    $('archive-token-kind').dataset.kind = kind;
    $('archive-token-kind').textContent = kind;
  }
  // an Archive has no Archive above it, and a broker's API is the Archive's to call
  $('candles').querySelector('[value="upstream"]').disabled = archive;
  $('calendar').querySelector('[value="upstream"]').disabled = archive;
  $('candles').querySelector('[value="providers"]').disabled = !archive;
  for (const select of [$('candles'), $('calendar')]) {
    if (select.selectedOptions[0].disabled) select.value = archive ? 'manual' : 'upstream';
  }
}

function apply(profile) {
  if (!profile || profile.profile !== 'parity-deriva') throw new Error('That file is not a parity-deriva profile.');
  const auth = profile.auth || {};
  const providers = auth.providers || {};
  $('name').value = profile.name || '';
  setRadio('mode', auth.mode);
  if (auth.mode === 'none' && state.proxied) setRadio('mode', '');
  // a list entry is an address or {who, can}: here everyone on it may change things
  $('allow').value = (auth.allow || []).map((a) => (typeof a === 'string' ? a : a.who)).join('\n');
  if (auth.public_url) $('public-url').value = auth.public_url;
  for (const [key] of PROVIDERS) {
    $(`use-${key}`).checked = !!providers[key];
    $(`id-${key}`).value = (providers[key] || {}).id || '';
    $(`secret-${key}`).value = '';
  }
  $('tenant-microsoft').value = (providers.microsoft || {}).tenant || 'common';
  for (const box of document.querySelectorAll('input[name="role"]')) box.checked = (profile.roles || []).includes(box.value);
  setRadio('accounts', profile.accounts === 'real' ? 'real' : 'demo');
  $('archive-url').value = (profile.archive || {}).url || '';
  $('archive-token').value = '';
  const market = profile.market || {};
  if (market.candles) $('candles').value = market.candles;
  if (market.calendar) $('calendar').value = market.calendar;
  show();
}

// the answers as api/setup/finish takes them, or the first wrong one
function answer() {
  const m = mode();
  if (!m) throw wrong('access-note', 'Choose who may open the pages.');
  const out = { name: $('name').value.trim(), auth: { mode: m }, roles: roles(),
    accounts: checked('accounts')[0] || 'demo',
    market: { candles: $('candles').value, calendar: $('calendar').value } };
  if (m === 'cert' || m === 'both') {
    if (checked('ca')[0] === 'pem') {
      const pem = $('ca-pem').value.trim();
      if (!pem.includes('BEGIN CERTIFICATE')) {
        throw wrong('access-note', "Paste your authority's certificate: the text that starts with -----BEGIN CERTIFICATE-----.");
      }
      out.ca = { pem };
    } else {
      const name = $('ca-name').value.trim();
      if (!name) throw wrong('access-note', 'Write who the first certificate is for.');
      out.ca = { make: true, name };
    }
  }
  if (m === 'oauth' || m === 'both') {
    const providers = {};
    for (const [key, label] of PROVIDERS) {
      if (!$(`use-${key}`).checked) continue;
      const id = $(`id-${key}`).value.trim();
      const secret = $(`secret-${key}`).value.trim();
      if (!id || !secret) throw wrong('access-note', `${label}: write its client id and client secret.`);
      providers[key] = key === 'microsoft' ? { id, secret, tenant: $('tenant-microsoft').value.trim() || 'common' } : { id, secret };
    }
    if (!Object.keys(providers).length) throw wrong('access-note', 'Tick at least one provider.');
    const allow = $('allow').value.split('\n').map((line) => line.trim()).filter(Boolean);
    if (!allow.length) throw wrong('access-note', 'Write who may enter: nobody could, otherwise.');
    const url = $('public-url').value.trim().replace(/\/+$/, '');
    if (!/^https?:\/\/./.test(url)) throw wrong('access-note', "Write this server's public address, https://...");
    Object.assign(out.auth, { allow, public_url: url, providers });
  }
  if (m === 'none' && state.proxied) throw wrong('access-note', 'No sign-in is not allowed here: this page came through a proxy.');
  if (!out.roles.length) throw wrong('role-note', 'Tick at least one role.');
  if (!out.roles.includes('archive')) {
    const url = $('archive-url').value.trim();
    const token = $('archive-token').value.trim();
    if (!url || !token) throw wrong('role-note', "Write the Archive's MCP address and its token.");
    out.archive = { url, token };
  }
  return out;
}

// the answers without a secret, to set up the next server like this one
function profileOf(body) {
  const auth = { mode: body.auth.mode };
  if (body.auth.mode === 'oauth' || body.auth.mode === 'both') {
    auth.allow = body.auth.allow;
    auth.public_url = body.auth.public_url;
    auth.providers = Object.fromEntries(Object.entries(body.auth.providers).map(
      ([key, p]) => [key, p.tenant ? { id: p.id, tenant: p.tenant } : { id: p.id }]));
  }
  const out = { profile: 'parity-deriva', version: 1, name: body.name, auth, roles: body.roles,
    accounts: body.accounts, market: body.market };
  if (body.archive) out.archive = { url: body.archive.url };
  return out;
}

function saveProfile(body) {
  const blob = new Blob([JSON.stringify(profileOf(body), null, 1) + '\n'], { type: 'application/json' });
  const a = link(URL.createObjectURL(blob), '');
  a.download = (body.name ? body.name.replace(/[^\w.-]+/g, '-') : 'parity-deriva') + '.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------ the steps */

async function enter() {
  state = await call('api/setup/state');
  $('step-code').hidden = true;
  $('steps').hidden = false;
  if (!$('public-url').value) $('public-url').value = state.base || '';
  if (state.proxied) {
    $('mode-none-radio').disabled = true;
    $('none-proxied').hidden = false;
  }
  if (!state.openssl) {
    // no openssl on this server: no authority can be made here
    document.querySelector('input[name="ca"][value="make"]').disabled = true;
    setRadio('ca', 'pem');
  }
  (state.profiles || []).forEach((row, i) => {
    $('profile-pick').add(new Option(row.profile.name || row.file, String(i)));
  });
  show();
}

function finished(body, done) {
  say('finish-note', 'Saved.');
  $('done').hidden = false;
  if (done.p12) {
    $('p12-link').href = `data:application/x-pkcs12;base64,${done.p12}`;
    $('p12-pass').textContent = done.password || '';
    $('done-cert').hidden = false;
  }
  if (done.promote) {
    $('done-mcp').textContent = done.mcp || '';
    $('done-token').textContent = done.promote;
    $('done-promote').hidden = false;
  }
  $('profile-save').addEventListener('click', () => saveProfile(body));
  // sent: the answers are the server's now
  for (const el of $('steps').querySelectorAll('input, select, textarea')) el.disabled = true;
  if (done.restart) waitRestart();
}

// the server starts again on its new answers: ask until it says it is set up
function waitRestart() {
  say('restart', 'Restarting…');
  const started = Date.now();
  const again = async () => {
    try {
      const access = await call('api/access');
      if (access.setup === false) {
        const oauth = access.mode === 'oauth' || access.mode === 'both';
        say('restart', 'Ready. ');
        $('restart').appendChild(link(oauth ? 'login' : './', oauth ? 'sign in' : 'open the app'));
        return;
      }
    } catch (error) { /* not back yet */ }
    if (Date.now() - started > 180000) {
      say('restart', 'The server did not come back. Look at its log: docker compose logs parity', true);
      return;
    }
    setTimeout(again, 2000);
  };
  // the old process answers for a moment before it goes
  setTimeout(again, 3000);
}

buildProviders();
document.addEventListener('change', show);
$('public-url').addEventListener('input', show);

$('code-go').addEventListener('click', async () => {
  say('code-note', '');
  try {
    await call('api/setup/code', { code: $('code').value.trim() });
    await enter();
  } catch (error) {
    say('code-note', error.status === 403 ? 'That is not the code in the log.' : error.message, true);
  }
});
$('code').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('code-go').click(); });

$('profile-pick').addEventListener('change', () => {
  const row = state.profiles[Number($('profile-pick').value)];
  if (!row) return;
  try {
    apply(row.profile);
    say('profile-note', `Filled in from ${row.file}.`);
  } catch (error) { say('profile-note', error.message, true); }
});
$('profile-file').addEventListener('change', async () => {
  const file = $('profile-file').files[0];
  if (!file) return;
  try {
    apply(JSON.parse(await file.text()));
    $('profile-pick').value = '';
    say('profile-note', `Filled in from ${file.name}.`);
  } catch (error) {
    say('profile-note', error instanceof SyntaxError ? 'That file is not JSON.' : error.message, true);
  }
});

$('archive-check').addEventListener('click', async () => {
  say('archive-note', 'Checking…');
  try {
    const found = await call('api/setup/archive', { url: $('archive-url').value.trim(), token: $('archive-token').value.trim() });
    say('archive-note', `The Archive answers: ${found.instruments} instruments.`);
  } catch (error) { say('archive-note', error.message, true); }
});

$('finish').addEventListener('click', async () => {
  for (const id of ['access-note', 'role-note', 'finish-note']) say(id, '');
  let body;
  try {
    body = answer();
  } catch (error) {
    const note = error.note || 'finish-note';
    say(note, error.message, true);
    $(note).scrollIntoView({ block: 'center' });
    return;
  }
  $('finish').disabled = true;
  say('finish-note', 'Saving…');
  try {
    finished(body, await call('api/setup/finish', body));
  } catch (error) {
    $('finish').disabled = false;
    say('finish-note', error.message, true);
  }
});

// set up already: nothing to do here. Else a page opened again, the code
// already given, goes straight on
call('api/access').then((access) => {
  if (!access.setup) {
    say('code-note', 'This server is set up already. ');
    $('code-note').appendChild(link('./', 'open the app'));
    $('code').disabled = $('code-go').disabled = true;
    return;
  }
  enter().catch(() => $('code').focus());
}).catch((error) => say('code-note', error.message, true));
