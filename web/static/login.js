/*
 * The sign-in page: a button for each provider this server set up
 * (api/access), each one going to the provider and back to the page asked
 * for (?next=, a path of this app only). An account not on the server's list
 * comes back here with ?error=not-allowed.
 */

const $ = (id) => document.getElementById(id);
const NAMES = { google: 'Sign in with Google', microsoft: 'Sign in with Microsoft', github: 'Sign in with GitHub' };
const ERRORS = {
  'not-allowed': 'This account is not on the list of who may enter.',
  failed: 'The sign-in did not work. Try again.',
  // no sign-in to offer for these two: the page only says why not
  certificate: 'This server wants a client certificate: import yours in the browser, then reload.',
  proxy: 'This server lets in only the PC it runs on.',
};

const params = new URLSearchParams(location.search);
// a path of this app, never another site: //host is a site
const asked = params.get('next') || '/';
const next = /^\/(?!\/)/.test(asked) ? asked : '/';

if (params.get('error')) {
  $('login-error').textContent = ERRORS[params.get('error')] || ERRORS.failed;
  $('login-error').hidden = false;
}

function appLink(text) {
  const a = document.createElement('a');
  a.href = './';
  a.textContent = text;
  return a;
}

fetch('api/access').then((reply) => reply.json()).then((access) => {
  if (['certificate', 'proxy'].includes(params.get('error'))) return;
  if (access.mode !== 'oauth' && access.mode !== 'both') {
    $('login-note').append('This server does not sign people in: there is nothing to sign in to. ', appLink('open the app'));
    return;
  }
  if (access.user) {
    $('login-note').append(`Signed in as ${access.user}. `, appLink('open the app'));
    return;
  }
  for (const provider of access.providers || []) {
    const a = document.createElement('a');
    a.href = `auth/start/${encodeURIComponent(provider)}?next=${encodeURIComponent(next)}`;
    a.textContent = NAMES[provider] || provider;
    $('login-buttons').appendChild(a);
  }
  if (!(access.providers || []).length) $('login-note').textContent = 'No sign-in provider is set up on this server.';
}).catch((error) => { $('login-note').textContent = String(error.message || error); });
