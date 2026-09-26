/*
 * The settings page's access tab (web/access.py): how people get in (a
 * certificate, an account, both, or nothing), the accounts' providers, this
 * server's certificate authority, and who may enter, each read only or
 * authorizing. The server refuses what would lock out the one saving and says
 * why: the page shows it. Loaded after settings.js, whose $, ask and post it uses.
 */

const ACCESS_PROVIDERS = [['google', 'Google'], ['microsoft', 'Microsoft 365'], ['github', 'GitHub']];
const ACCESS_NEEDS = {
  cert: 'Needs an authority, and your certificate imported in this browser.',
  oauth: 'Needs a provider, the public address, and you signed in with an account on the list.',
  both: 'Needs all of it: an authority, your certificate in this browser, a provider, the public address, and you signed in.',
  none: 'Needs this page opened from this PC, not through a proxy.',
};

const accessSay = (text) => { $('access-note').textContent = text || ''; };
const accessFailed = (error) => accessSay(String(error.message || error));
const accessMode = () => (document.querySelector('input[name="access-mode"]:checked') || {}).value || '';
const accessSpan = (text) => { const span = document.createElement('span'); span.textContent = text; return span; };

function accessNeeds() {
  $('access-needs').textContent = ACCESS_NEEDS[accessMode()] || '';
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
  // what this browser brings: what the modes with a certificate or an account look at
  $('access-brings').replaceChildren(
    accessSpan(access.certificate ? `your certificate: ${access.certificate}` : 'no client certificate on this request'),
    ' · ',
    accessSpan(access.signed ? `signed in as ${access.signed}` : 'not signed in'));

  $('access-url').value = access.public_url || '';
  accessProviders(access);

  $('access-ca-none').hidden = !!access.ca;
  $('access-ca-own').hidden = access.ca !== 'own';
  $('access-ca-external').hidden = access.ca !== 'external';
  const locked = mode === 'cert' || mode === 'both';
  $('access-ca-locked').hidden = !locked;

  $('access-rows').textContent = '';
  for (const entry of access.allow || []) accessRow(typeof entry === 'string' ? { who: entry, can: 'write' } : entry);
  if (!(access.allow || []).length) accessRow({});

  const readOnly = access.can === 'read';
  $('access-readonly').hidden = !readOnly;
  for (const button of $('access-panel').querySelectorAll('button')) button.disabled = readOnly;
  for (const el of [$('access-ca-make'), $('access-ca-load'), $('access-ca-pem')]) el.disabled = readOnly || locked;
}

document.querySelectorAll('input[name="access-mode"]').forEach((radio) => radio.addEventListener('change', accessNeeds));

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
