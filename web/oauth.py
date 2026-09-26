"""
Who may use the MCP endpoint (web/mcp.py): the one secret, and the OAuth
that hands out tokens against it.

claude.ai, ChatGPT and Gemini call an MCP server from their own machines, so
a client certificate in the user's browser never reaches it; what they all
speak is OAuth 2.1 with dynamic client registration (RFC 7591) and PKCE.
This is the smallest authorization server that satisfies them:

* **the secret** is made on the settings page, which can show it again. It is the
  password of the consent page, and a client that takes a fixed header -
  Cursor, VS Code, a CLI - sends it as `Authorization: Bearer <secret>`.
* **register** takes any client, as the MCP specification expects. A client
  is only a name and its redirect addresses: registering grants nothing.
* **authorize** is a page in the user's browser asking for the secret.
* **token** trades the code, with its PKCE verifier, for an access token of
  a day and a refresh token of sixty, rotated on every use.

Hashes are kept in DATA_DIR/mcp.json (0600), but for the secret itself, so
the settings page can show it again: a copy of the file lets its holder in.
A new secret throws every token away.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
import urllib.parse

ACCESS_TTL = 24 * 3600
REFRESH_TTL = 60 * 24 * 3600
CODE_TTL = 600
#: registrations kept; the oldest goes. Anybody may register, so there is a cap
MAX_CLIENTS = 50


class OAuthError(Exception):
	"""An OAuth refusal: `code` is the RFC 6749 error."""

	def __init__(self, code, description, status=400):
		Exception.__init__(self, description)
		self.code, self.status = code, status


def digest(text):
	return hashlib.sha256(text.encode('utf-8')).hexdigest()


def loopback(uri):
	host = urllib.parse.urlsplit(uri).hostname
	return uri.startswith('http://') and host in ('localhost', '127.0.0.1')


def sameRedirect(asked, registered):
	"""The registered address, the port aside for a loopback one (RFC 8252)."""
	if asked == registered:
		return True
	if not (loopback(asked) and loopback(registered)):
		return False
	a, b = urllib.parse.urlsplit(asked), urllib.parse.urlsplit(registered)
	return (a.hostname, a.path, a.query) == (b.hostname, b.path, b.query)


class Authority(object):

	def __init__(self, path):
		self.path = path
		self.lock = threading.Lock()
		#: the codes waiting to be traded: ten minutes, lost on a restart
		self.codes = {}

	# ------------------------------------------------------------ the file

	def read(self):
		try:
			with open(self.path) as handle:
				state = json.load(handle)
		except (OSError, ValueError):
			state = {}
		state.setdefault('secret', None)
		state.setdefault('plain', None)
		state.setdefault('clients', {})
		state.setdefault('tokens', {})
		return state

	def write(self, state):
		now = time.time()
		state['tokens'] = dict((k, v) for k, v in state['tokens'].items()
							   if v['expires'] > now)
		os.makedirs(os.path.dirname(self.path), exist_ok=True)
		part = self.path + '.part'
		with open(part, 'w') as handle:
			json.dump(state, handle)
		os.chmod(part, 0o600)
		os.replace(part, self.path)

	# ---------------------------------------------------------- the secret

	def status(self):
		"""Is there a secret, and the names of the clients holding a token."""
		state = self.read()
		now = time.time()
		names = set(state['clients'].get(v['client'], {}).get('name') or 'unnamed client'
					for v in state['tokens'].values()
					if v['kind'] == 'refresh' and v['expires'] > now)
		return {'secret': state['secret'] is not None, 'clients': sorted(names)}

	def newSecret(self):
		"""A new secret; every token given so far stops working."""
		secret = secrets.token_urlsafe(32)
		with self.lock:
			state = self.read()
			state['secret'], state['plain'] = digest(secret), secret
			state['tokens'] = {}
			self.write(state)
		return secret

	def shownSecret(self):
		"""The secret, for the settings page; None when there is none, or it
		was made before it was kept."""
		return self.read()['plain']

	def disconnect(self):
		"""Every token goes; the secret stays."""
		with self.lock:
			state = self.read()
			state['tokens'] = {}
			self.write(state)

	def secretIs(self, text):
		held = self.read()['secret']
		return bool(held and text and hmac.compare_digest(held, digest(text)))

	def allowed(self, bearer):
		"""Is `bearer` the secret or a live access token?"""
		if not bearer:
			return False
		if self.secretIs(bearer):
			return True
		token = self.read()['tokens'].get(digest(bearer))
		return bool(token and token['kind'] == 'access' and token['expires'] > time.time())

	def holder(self, bearer):
		"""The name of the client `bearer` was given to; None for the secret."""
		state = self.read()
		token = state['tokens'].get(digest(bearer or ''))
		if not token:
			return None
		return state['clients'].get(token['client'], {}).get('name') or 'unnamed client'

	# --------------------------------------------------------- the metadata

	@staticmethod
	def resourceMetadata(base):
		"""RFC 9728: what the MCP endpoint is and who issues its tokens."""
		return {'resource': base + '/mcp', 'authorization_servers': [base],
				'bearer_methods_supported': ['header'], 'scopes_supported': ['parity'],
				'resource_name': 'parity-deriva'}

	@staticmethod
	def serverMetadata(base):
		"""RFC 8414, also served as the OpenID discovery document."""
		return {'issuer': base,
				'authorization_endpoint': base + '/oauth/authorize',
				'token_endpoint': base + '/oauth/token',
				'registration_endpoint': base + '/oauth/register',
				'response_types_supported': ['code'],
				'grant_types_supported': ['authorization_code', 'refresh_token'],
				'code_challenge_methods_supported': ['S256'],
				'token_endpoint_auth_methods_supported': ['none'],
				'scopes_supported': ['parity']}

	# ------------------------------------------------------------ register

	def register(self, body):
		uris = body.get('redirect_uris') if isinstance(body, dict) else None
		if not isinstance(uris, list) or not uris or not all(
				isinstance(u, str) and (u.startswith('https://') or loopback(u)) for u in uris):
			raise OAuthError('invalid_redirect_uri',
							 "redirect_uris: a list of https:// or loopback http:// addresses")
		client = {'name': str(body.get('client_name') or '')[:80], 'redirect_uris': uris,
				  'created': int(time.time())}
		clientId = secrets.token_urlsafe(16)
		with self.lock:
			state = self.read()
			state['clients'][clientId] = client
			for old in sorted(state['clients'], key=lambda k: state['clients'][k]['created'])[:-MAX_CLIENTS]:
				del state['clients'][old]
			self.write(state)
		return {'client_id': clientId, 'client_id_issued_at': client['created'],
				'client_name': client['name'], 'redirect_uris': uris,
				'token_endpoint_auth_method': 'none',
				'grant_types': ['authorization_code', 'refresh_token'],
				'response_types': ['code']}

	# ----------------------------------------------------------- authorize

	def request(self, params):
		"""The authorization request, checked: (client, params). Raises OAuthError."""
		client = self.read()['clients'].get(params.get('client_id') or '')
		if client is None:
			raise OAuthError('invalid_client', "this client is not registered: "
							 "remove the connector and add it again")
		uri = params.get('redirect_uri') or ''
		if not any(sameRedirect(uri, r) for r in client['redirect_uris']):
			raise OAuthError('invalid_request', "redirect_uri is not one the client registered")
		if params.get('response_type') != 'code':
			raise OAuthError('unsupported_response_type', "response_type must be code")
		if params.get('code_challenge_method') != 'S256' or not params.get('code_challenge'):
			raise OAuthError('invalid_request', "PKCE with S256 is required")
		return client, params

	def consentPage(self, client, params, wrong=False):
		"""The page that asks for the secret. Every value in it is escaped."""
		hidden = ''.join('<input type="hidden" name="%s" value="%s">'
						 % (html.escape(k), html.escape(params.get(k) or ''))
						 for k in ('client_id', 'redirect_uri', 'state', 'code_challenge',
								   'code_challenge_method', 'response_type', 'scope'))
		name = html.escape(client['name'] or 'An application')
		back = html.escape(urllib.parse.urlsplit(params.get('redirect_uri') or '').netloc)
		return ("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to parity-deriva</title>
<style>
:root { color-scheme: light dark; --bg: #F7F4EF; --panel: #FFFFFF; --text: #1B2A33; --muted: #5E6E78; --line: #D9D3C7; --brand: #B8742A; --down: #B3261E; }
@media (prefers-color-scheme: dark) { :root { --bg: #0F1A20; --panel: #16242C; --text: #E6EAEC; --muted: #8E9CA6; --line: #2B3B44; --brand: #D08A3C; --down: #F2B8B5; } }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 440px; margin: 10vh auto; padding: 24px 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
h1 { font-size: 20px; margin: 0 0 12px; }
p, li { color: var(--muted); }
input[type=password] { width: 100%%; box-sizing: border-box; padding: 8px; margin: 4px 0 16px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--text); font: inherit; }
button { padding: 8px 16px; border-radius: 6px; border: 1px solid var(--line); background: var(--panel); color: var(--text); font: inherit; cursor: pointer; }
button.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.wrong { color: var(--down); }
</style></head><body><main>
<h1>Connect %s to parity-deriva</h1>
<p>It will be able to read the strategies and the data, write draft strategies and run backtests.
It cannot trade, and it cannot enable a strategy: that stays on the settings page.</p>
<p>After this it goes back to <strong>%s</strong>.</p>
<form method="post">%s
<label for="secret">MCP token, from the settings page</label>
<input type="password" id="secret" name="secret" autocomplete="off" autofocus required>
%s
<button type="submit" name="decision" value="allow" class="primary">Allow</button>
<button type="submit" name="decision" value="deny" formnovalidate>Deny</button>
</form></main></body></html>"""
				% (name, back, hidden,
				   '<p class="wrong" role="alert">That is not the MCP token.</p>' if wrong else ''))

	def decide(self, form, issuer):
		"""
		The consent form sent back: (redirect address,) on a decision, or
		None when the secret was wrong and the page is to be shown again.
		"""
		client, params = self.request(form)
		back = params['redirect_uri']
		answer = {'state': params['state']} if params.get('state') else {}
		if form.get('decision') != 'allow':
			answer['error'] = 'access_denied'
		elif not self.secretIs(form.get('secret')):
			return None
		else:
			code = secrets.token_urlsafe(32)
			with self.lock:
				now = time.time()
				self.codes = dict((k, v) for k, v in self.codes.items() if v['expires'] > now)
				self.codes[code] = {'client': params['client_id'], 'redirect_uri': back,
									'challenge': params['code_challenge'], 'expires': now + CODE_TTL}
			answer.update(code=code, iss=issuer)
		return back + ('&' if '?' in back else '?') + urllib.parse.urlencode(answer)

	# --------------------------------------------------------------- token

	def token(self, form):
		grant = form.get('grant_type')
		if grant == 'authorization_code':
			with self.lock:
				code = self.codes.pop(form.get('code') or '', None)
			if code is None or code['expires'] < time.time():
				raise OAuthError('invalid_grant', "the code is unknown, used or expired")
			if form.get('client_id') and form['client_id'] != code['client']:
				raise OAuthError('invalid_grant', "the code was given to another client")
			if (form.get('redirect_uri') or code['redirect_uri']) != code['redirect_uri']:
				raise OAuthError('invalid_grant', "redirect_uri differs from the authorization's")
			verifier = (form.get('code_verifier') or '').encode('ascii', 'replace')
			challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b'=').decode()
			if not hmac.compare_digest(challenge, code['challenge']):
				raise OAuthError('invalid_grant', "the PKCE verifier does not match")
			return self.issue(code['client'])
		if grant == 'refresh_token':
			key = digest(form.get('refresh_token') or '')
			with self.lock:
				state = self.read()
				held = state['tokens'].pop(key, None)
				if held is None or held['kind'] != 'refresh' or held['expires'] < time.time() \
						or (form.get('client_id') and form['client_id'] != held['client']):
					raise OAuthError('invalid_grant', "the refresh token is unknown or expired")
				self.write(state)
			return self.issue(held['client'])
		raise OAuthError('unsupported_grant_type', "grant_type is authorization_code or refresh_token")

	def issue(self, client):
		access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
		now = time.time()
		with self.lock:
			state = self.read()
			state['tokens'][digest(access)] = {'client': client, 'kind': 'access',
											   'expires': now + ACCESS_TTL}
			state['tokens'][digest(refresh)] = {'client': client, 'kind': 'refresh',
												'expires': now + REFRESH_TTL}
			self.write(state)
		return {'access_token': access, 'token_type': 'Bearer', 'expires_in': ACCESS_TTL,
				'refresh_token': refresh, 'scope': 'parity'}
