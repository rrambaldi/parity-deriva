/*
 * The settings page's access tab (web/access.py): the way chosen at the
 * first start, said and not changed; with sign-in, who may enter and the
 * providers; with an authority of this server's, a certificate for one more
 * person. Loaded after settings.js, whose $, ask and post it uses.
 */

const ACCESS_PROVIDERS = [['google', 'Google'], ['microsoft', 'Microsoft'], ['github', 'GitHub']];
const ACCESS_MODES = {
  cert: 'Who may enter: whoever holds a client certificate, checked by the proxy in front.',
  oauth: 'Who may enter: the people on the list below, signed in with a provider.',
  none: 'No sign-in: anyone who can reach the port is in. Only for a PC of your own.',
};

const accessSay = (text) => { $('access-note').textContent = text || ''; };

function accessProviders(access) {
  const box = $('access-providers');
  box.textContent = '';
  for (const [key, label] of ACCESS_PROVIDERS) {
    const held = (access.providers || {})[key] || {};
    const row = document.createElement('div');
    row.className = 'market-row';
    row.style.marginTop = '10px';
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
  }
}

function showAccess(access) {
  $('access-state').textContent = access.mode ? ACCESS_MODES[access.mode] || access.mode
    : 'Access is left to the proxy in front of this server: no setup was made here.';
  if (access.user) $('access-state').append(' ', `You are ${access.user}.`);
  $('access-oauth').hidden = access.mode !== 'oauth';
  $('access-cert').hidden = !(access.mode === 'cert' && access.ca === 'own');
  if (access.mode === 'oauth') {
    $('access-url').value = access.public_url || '';
    $('access-allow').value = (access.allow || []).join('\n');
    accessProviders(access);
  }
}

$('access-save').addEventListener('click', async () => {
  const allow = $('access-allow').value.split('\n').map((line) => line.trim()).filter(Boolean);
  if (!allow.length) { accessSay('Write who may enter: nobody could, otherwise.'); return; }
  // every provider is sent: an empty client id takes it away
  const providers = {};
  for (const [key] of ACCESS_PROVIDERS) {
    providers[key] = { id: $(`access-id-${key}`).value.trim(), secret: $(`access-secret-${key}`).value.trim() };
    if (key === 'microsoft') providers[key].tenant = $('access-tenant').value.trim() || 'common';
  }
  try {
    showAccess(await post('api/access/settings', JSON.stringify({
      allow, providers, public_url: $('access-url').value.trim().replace(/\/+$/, '') })));
    accessSay('Saved.');
  } catch (error) { accessSay(String(error.message || error)); }
});

$('access-cert-go').addEventListener('click', async () => {
  const name = $('access-cert-name').value.trim();
  if (!name) { accessSay('Write who the certificate is for.'); return; }
  try {
    const made = await post('api/access/cert', JSON.stringify({ name }));
    $('access-p12').href = `data:application/x-pkcs12;base64,${made.p12}`;
    $('access-p12').download = name.replace(/[^\w.-]+/g, '-') + '.p12';
    $('access-p12-pass').textContent = made.password;
    $('access-cert-out').hidden = false;
    accessSay('Import the file in the browser of the person it is for, with the password.');
  } catch (error) { accessSay(String(error.message || error)); }
});

ask('api/access/settings').then(showAccess).catch((error) => accessSay(String(error.message || error)));
