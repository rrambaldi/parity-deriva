/*
 * The settings page's access tab (web/access.py): how people get in (a
 * certificate, an account, both, or nothing), the accounts' providers, this
 * server's certificate authority, and who may enter, each read only or
 * authorizing. The server refuses what would lock out the one saving and says
 * why: the page shows it. Loaded after settings.js, whose $, ask and post it uses.
 *
 * Each part is a drop-down: the ones the mode in use needs open by themselves
 * on the first load, and picking a mode opens the ones it will need.
 */

const ACCESS_PROVIDERS = [['google', 'Google'], ['microsoft', 'Microsoft 365'], ['github', 'GitHub']];
const ACCESS_NEEDS = {
  cert: 'Needs an authority, and your certificate imported in this browser.',
  oauth: 'Needs a provider, the public address, and you signed in with an account on the list.',
  both: 'Needs all of it: an authority, your certificate in this browser, a provider, the public address, and you signed in.',
  none: 'Needs this page opened from this PC, not through a proxy.',
};

const ACCESS_MODES = { cert: 'a client certificate', oauth: 'an account', both: 'certificate and account',
  none: 'nothing: only this PC' };
// the parts each mode needs, open when it is the one in use or the one picked
const ACCESS_PARTS = { cert: ['access-certs', 'access-who'], oauth: ['access-accounts', 'access-who'],
  both: ['access-certs', 'access-accounts', 'access-who'], none: [] };
let accessOpened = false;

const accessSay = (text) => { $('access-note').textContent = text || ''; };
const accessFailed = (error) => accessSay(String(error.message || error));
const accessMode = () => (document.querySelector('input[name="access-mode"]:checked') || {}).value || '';
const accessSpan = (text) => { const span = document.createElement('span'); span.textContent = text; return span; };

function accessNeeds() {
  $('access-needs').textContent = ACCESS_NEEDS[accessMode()] || '';
}

function accessOpen(mode) {
  for (const id of ACCESS_PARTS[mode] || []) $(id).open = true;
}

function accessIssued(rows) {
  const table = $('access-issued');
  table.textContent = '';
  if (!rows.length) {
    table.insertRow().insertCell().textContent = 'none issued yet';
    return;
  }
  for (const row of rows) {
    const tr = table.insertRow();
    tr.insertCell().textContent = row.when;
    tr.insertCell().textContent = row.name;
    tr.insertCell().textContent = String(row.serial || '').slice(0, 12);
  }
}

function accessProviders(access) {
  const box = $('access-providers');
  box.textContent = '';
  for (const [key, label] of ACCESS_PROVIDERS) {
    const held = (access.providers || {})[key] || {};
    const row = document.createElement('div');
    row.className = 'market-row';
    row.innerHTML = `<label>${label} client id
        <input type="text" id="access-id-${key}" spellcheck="false" autocomplete="off"></label>
      <label>client secret <input type="password" id="access-secret-${key}" autocomplete="off"></label>
      ${key === 'microsoft' ? '<label>tenant <input type="text" id="access-tenant" spellcheck="false"></label>' : ''}
      <span style="flex-basis: 100%; font-size: var(--fs-small); color: var(--text-3); overflow-wrap: anywhere">callback address to register:
        <code id="access-cb-${key}"></code></span>`;
    box.appendChild(row);
    $(`access-id-${key}`).value = held.id || '';
    // a secret is never shown again: empty keeps the one there is
    $(`access-secret-${key}`).placeholder = held.secret ? 'kept: write a new one to change it' : '';
    $(`access-cb-${key}`).textContent = (access.callbacks || {})[key] || '';
    if (key === 'microsoft') $('access-tenant').value = held.tenant || 'common';
    if (held.secret) {
      // signed in before the mode asks for it: the server wants that first
      const a = document.createElement('a');
      a.href = `auth/start/${key}?next=%2Fsettings%23access-panel`;
      a.textContent = `sign in with ${label} to try`;
      row.appendChild(a);
    }
  }
}

function accessRow(entry) {
  const row = document.createElement('div');
  row.className = 'market-row';
  row.innerHTML = `<label>who <input type="text" class="access-who" spellcheck="false" autocomplete="off"
      placeholder="someone@example.com"></label>
    <label>may <select class="access-can"><option value="write">authorizing</option>
      <option value="read">read only</option></select></label>
    <button type="button" class="access-drop" data-icon="delete">remove</button>`;
  row.querySelector('.access-who').value = entry.who || '';
  row.querySelector('.access-can').value = entry.can === 'read' ? 'read' : 'write';
  row.querySelector('.access-drop').addEventListener('click', () => row.remove());
  $('access-rows').appendChild(row);
}

function showAccess(access) {
  const mode = access.mode || '';
  for (const radio of document.querySelectorAll('input[name="access-mode"]')) radio.checked = radio.value === mode;
  $('access-legacy').hidden = !!mode;
  accessNeeds();
  if (!accessOpened) {
    // the first load only: afterwards what the user opened and closed stays
    accessOpen(mode);
    if (!mode && access.ca) $('access-certs').open = true;
    accessOpened = true;
  }
  $('access-how-now').textContent = ACCESS_MODES[mode] ? t(ACCESS_MODES[mode]) : t('left to the proxy');
  const set = ACCESS_PROVIDERS.filter(([key]) => ((access.providers || {})[key] || {}).secret).map(([, label]) => label);
  $('access-accounts-now').textContent = set.length ? set.join(', ') : t('none set up');
  $('access-who-now').textContent = t('{n} on the list', { n: (access.allow || []).length });
  $('access-certs-now').textContent = access.authority ? access.authority.name : t('no authority');
  // what this browser brings: what the modes with a certificate or an account look at
  $('access-brings').replaceChildren(
    accessSpan(access.certificate ? `your certificate: ${access.certificate}` : 'no client certificate on this request'),
    ' · ',
    accessSpan(access.signed ? `signed in as ${access.signed}` : 'not signed in'));

  $('access-url').value = access.public_url || '';
  accessProviders(access);

  const authority = access.authority;
  $('access-authority').textContent = authority
    ? t(access.ca === 'own' ? 'authority: {name} · valid until {until} · made on this server'
      : 'authority: {name} · valid until {until} · issued elsewhere', { name: authority.name, until: authority.until || '?' })
    : t('No authority yet.');
  // what reaches this server from this browser: behind a proxy, only what the proxy passes on
  $('access-mine').textContent = access.certificate ? t('your certificate: {name}', { name: access.certificate })
    : access.ca ? t("No client certificate reaches this server from this browser. Behind a proxy, the proxy must pass the certificate's name in X-Client-Subject.")
      : '';
  $('access-ca-own').hidden = access.ca !== 'own';
  if (access.ca === 'own') accessIssued(access.issued || []);
  $('access-ca-external').hidden = access.ca !== 'external';
  const locked = mode === 'cert' || mode === 'both';
  $('access-ca-locked').hidden = !locked;
  $('access-ca-change').hidden = locked;
  $('access-ca-change-note').textContent = access.ca
    ? t('Replace the authority: the certificates it issued stop working.') : t('Make one here, or load yours.');
  $('access-ca-make').textContent = t(access.ca ? 'make a new authority here' : 'make an authority here');

  $('access-rows').textContent = '';
  for (const entry of access.allow || []) accessRow(typeof entry === 'string' ? { who: entry, can: 'write' } : entry);
  if (!(access.allow || []).length) accessRow({});

  const readOnly = access.can === 'read';
  $('access-readonly').hidden = !readOnly;
  for (const button of $('access-panel').querySelectorAll('button')) button.disabled = readOnly;
  for (const el of [$('access-ca-make'), $('access-ca-load'), $('access-ca-pem')]) el.disabled = readOnly || locked;
}

document.querySelectorAll('input[name="access-mode"]').forEach((radio) => radio.addEventListener('change', () => {
  accessNeeds();
  accessOpen(accessMode());
}));

$('access-mode-save').addEventListener('click', async () => {
  const mode = accessMode();
  if (!mode) { accessSay('Choose how people get in.'); return; }
  try {
    showAccess(await post('api/access/settings', JSON.stringify({ mode })));
    accessSay('Changed.');
  } catch (error) { accessFailed(error); }
});

$('access-accounts-save').addEventListener('click', async () => {
  // every provider is sent: an empty client id takes it away
  const providers = {};
  for (const [key] of ACCESS_PROVIDERS) {
    providers[key] = { id: $(`access-id-${key}`).value.trim(), secret: $(`access-secret-${key}`).value.trim() };
    if (key === 'microsoft') providers[key].tenant = $('access-tenant').value.trim() || 'common';
  }
  try {
    showAccess(await post('api/access/settings', JSON.stringify({
      providers, public_url: $('access-url').value.trim().replace(/\/+$/, '') })));
    accessSay('Saved.');
  } catch (error) { accessFailed(error); }
});

$('access-ca-make').addEventListener('click', async () => {
  if (!$('access-ca-own').hidden || !$('access-ca-external').hidden) {
    if (!confirm(t('A new authority: the certificates of the one there is stop working. Go on?'))) return;
  }
  try {
    showAccess(await post('api/access/ca', JSON.stringify({ make: true })));
    accessSay('Authority made: now issue your certificate.');
  } catch (error) { accessFailed(error); }
});

$('access-ca-load').addEventListener('click', async () => {
  const pem = $('access-ca-pem').value.trim();
  if (!pem.includes('BEGIN CERTIFICATE')) {
    accessSay("Paste your authority's certificate: the text that starts with -----BEGIN CERTIFICATE-----.");
    return;
  }
  try {
    showAccess(await post('api/access/ca', JSON.stringify({ pem })));
    accessSay('Authority loaded.');
  } catch (error) { accessFailed(error); }
});

$('access-cert-go').addEventListener('click', async () => {
  const name = $('access-cert-name').value.trim();
  if (!name) { accessSay('Write who the certificate is for.'); return; }
  try {
    const made = await post('api/access/cert', JSON.stringify({ name, can: $('access-cert-can').value }));
    $('access-p12').href = `data:application/x-pkcs12;base64,${made.p12}`;
    $('access-p12').download = `parity-deriva-${name.replace(/[^\w.-]+/g, '-')}.p12`;
    $('access-p12-pass').textContent = made.password;
    $('access-cert-out').hidden = false;
    if (made.settings) showAccess(made.settings);
    accessSay('Import the file in the browser of the person it is for, with the password.');
  } catch (error) { accessFailed(error); }
});

$('access-add').addEventListener('click', () => accessRow({}));

$('access-who-save').addEventListener('click', async () => {
  const allow = [...$('access-rows').querySelectorAll('.market-row')]
    .map((row) => ({ who: row.querySelector('.access-who').value.trim(), can: row.querySelector('.access-can').value }))
    .filter((entry) => entry.who);
  try {
    showAccess(await post('api/access/settings', JSON.stringify({ allow })));
    accessSay('Saved.');
  } catch (error) { accessFailed(error); }
});

ask('api/access/settings').then(showAccess).catch(accessFailed);
