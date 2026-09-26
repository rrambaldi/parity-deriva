"""
The phones paired with this server (docs/PIANO-FASE1.md C8): a page that
shows its sessions and their open trades, read only, and its urgent alerts
(web/notify.py) as notifications.

Pairing: the settings page asks for a code (newCode), good once and for
PAIR_TTL, and shows it with the QR of <public url>/phone?pair=<code>. The
phone's page sends it back (pair) and gets a token of its own in a cookie -
HttpOnly, Secure, SameSite=Strict; this server keeps only its sha256, in
DATA_DIR/phones.json, and the settings page revokes it. The code can be typed
as well: on an iPhone the app on the home screen does not share Safari's
cookies, so it is paired from there.

Notifications are Web Push, the browsers' own standard, and the push is
empty: it wakes the phone's service worker (static/phone-sw.js), which asks
api/phone/alerts with its cookie what to show. Google's and Apple's push
services, which carry it, see no trade and no number, and an empty push needs
no encryption. Its VAPID signature (ES256) is made by the openssl command -
the Docker image has it for the setup's certificates - with the key in
DATA_DIR/vapid.pem, made the first time it is needed.

The phone's routes are open to the phone, which has no client certificate
nor a sign-in: they are served before the access gate (route), and every one
but the pairing wants the phone's token. The settings page's side of it
(settings) is behind the gate like the rest of the page.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from parity_deriva.web import access, livesessions, mcp, notify

PAIR_TTL = 600
COOKIE = 'pd_phone'
#: a phone's token lives as long as nobody revokes it; the cookie a year
COOKIE_TTL = 365 * 86400
#: how often a phone's last visit is written down
SEEN_EVERY = 60
#: the alerts the page lists
SHOWN = 20
TIMEOUT = 15
#: the routes the phone reaches, before the access gate
PAGES = ('/phone', '/phone-sw.js', '/phone-manifest.json')
API = '/api/phone/'

logger = logging.getLogger('parity_deriva.web')
_lock = threading.Lock()


def dataDir(setup):
	return getattr(setup, 'DATA_DIR', '') or '.'


def digest(text):
	return hashlib.sha256(text.encode()).hexdigest()


def now():
	return int(time.time() * 1000)


# ------------------------------------------------------------ phones.json

def load(setup):
	try:
		with open(os.path.join(dataDir(setup), 'phones.json')) as handle:
			held = json.load(handle)
	except (OSError, ValueError):
		held = {}
	return {'phones': held.get('phones') or [], 'pairing': held.get('pairing')}


def save(setup, held):
	name = os.path.join(dataDir(setup), 'phones.json')
	os.makedirs(os.path.dirname(name), exist_ok=True)
	with open(os.open(name + '.part', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
		json.dump(held, handle)
	os.replace(name + '.part', name)


def change(setup, how):
	with _lock:
		held = load(setup)
		out = how(held)
		save(setup, held)
		return out


def listed(setup):
	"""The phones as the settings page shows them: no hash, no subscription."""
	return [{'id': p['id'], 'name': p.get('name'), 'paired': p.get('paired'), 'seen': p.get('seen'),
			 'notifications': bool(p.get('subscription'))} for p in load(setup)['phones']]


def newCode(setup):
	"""A pairing code, good once for PAIR_TTL seconds; a new one replaces the last."""
	code = ''.join(secrets.choice(access.CODE_LETTERS) for _ in range(8))
	until = now() + PAIR_TTL * 1000

	def how(held):
		held['pairing'] = {'hash': digest(code), 'until': until}
	change(setup, how)
	return {'code': code, 'until': until}


def pair(setup, code, name):
	"""The phone's token for a good code, which is then spent; None for any other."""
	code = ''.join(str(code or '').split()).upper()
	token = secrets.token_urlsafe(32)

	def how(held):
		pending = held.get('pairing') or {}
		if not code or pending.get('hash') != digest(code) or pending.get('until', 0) < now():
			return None
		held['pairing'] = None
		held['phones'].append({'id': secrets.token_hex(4), 'name': str(name or 'phone')[:40],
							   'hash': digest(token), 'paired': now(), 'seen': now(),
							   'cursor': now(), 'subscription': None})
		return token
	return change(setup, how)


def revoke(setup, ident):
	def how(held):
		before = len(held['phones'])
		held['phones'] = [p for p in held['phones'] if p['id'] != ident]
		return before != len(held['phones'])
	return change(setup, how)


def phoneOf(handler):
	"""The paired phone that sent the request, or None."""
	token = access.cookies(handler).get(COOKIE)
	if not token:
		return None
	setup = handler.service.setup
	hashed = digest(token)
	for phone in load(setup)['phones']:
		if secrets.compare_digest(phone.get('hash') or '', hashed):
			if now() - (phone.get('seen') or 0) > SEEN_EVERY * 1000:
				def how(held):
					for p in held['phones']:
						if p['id'] == phone['id']:
							p['seen'] = now()
				change(setup, how)
			return phone
	return None


def subscribe(setup, ident, subscription):
	endpoint = str((subscription or {}).get('endpoint') or '')
	if not endpoint.startswith('https://'):
		raise ValueError("a push subscription has an https endpoint")

	def how(held):
		for p in held['phones']:
			if p['id'] == ident:
				p['subscription'] = {'endpoint': endpoint}
	change(setup, how)


def pending(setup, ident):
	"""The urgent alerts this phone has not been shown, oldest first; the cursor moves past them."""
	def how(held):
		for p in held['phones']:
			if p['id'] == ident:
				since = p.get('cursor') or 0
				out = [a for a in notify.read(setup)['recent'] if a['level'] == 'urgent' and a['at'] > since]
				if out:
					p['cursor'] = max(a['at'] for a in out)
				return out[::-1]
		return []
	return change(setup, how)


# ------------------------------------------------------------------ VAPID

def b64(data):
	return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def openssl(*args, data=None):
	return subprocess.run(('openssl',) + args, input=data, capture_output=True, check=True,
						  timeout=TIMEOUT).stdout


def vapid(setup):
	"""DATA_DIR/vapid.pem, made the first time, and its public key as the browsers take it."""
	name = os.path.join(dataDir(setup), 'vapid.pem')
	with _lock:
		if not os.path.exists(name):
			os.makedirs(os.path.dirname(name), exist_ok=True)
			openssl('ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', name + '.part')
			os.chmod(name + '.part', 0o600)
			os.replace(name + '.part', name)
	# the uncompressed point, 0x04 X Y, is the last 65 bytes of the DER key
	return name, openssl('ec', '-in', name, '-pubout', '-outform', 'DER')[-65:]


def joseSignature(der):
	"""An ECDSA signature from DER, a SEQUENCE of two INTEGERs, to JOSE's r || s."""
	i, out = 2, b''
	for _ in range(2):
		if der[i] != 2:
			raise ValueError("not a DER ECDSA signature")
		n = der[i + 1]
		out += der[i + 2:i + 2 + n].lstrip(b'\0').rjust(32, b'\0')
		i += 2 + n
	return out


def authorization(setup, endpoint):
	"""The Authorization header of a push to `endpoint`: a JWT for its origin, signed ES256."""
	name, public = vapid(setup)
	where = urllib.parse.urlsplit(endpoint)
	subject = getattr(setup, 'PUBLIC_URL', None) or ''
	claims = {'aud': '%s://%s' % (where.scheme, where.netloc), 'exp': int(time.time()) + 12 * 3600,
			  'sub': subject if subject.startswith('https://') else 'mailto:alerts@parity-deriva.invalid'}
	signing = '.'.join(b64(json.dumps(part, separators=(',', ':')).encode())
					   for part in ({'typ': 'JWT', 'alg': 'ES256'}, claims))
	signature = joseSignature(openssl('dgst', '-sha256', '-sign', name, data=signing.encode()))
	return 'vapid t=%s.%s, k=%s' % (signing, b64(signature), b64(public))


def push(setup, alert):
	"""Wake every phone that has notifications on (web/notify.py send): 'sent to n', or 'off'."""
	phones = [p for p in load(setup)['phones'] if p.get('subscription')]
	if not phones:
		return 'off'
	sent, gone = 0, []
	for phone in phones:
		endpoint = phone['subscription']['endpoint']
		request = urllib.request.Request(endpoint, data=b'', method='POST', headers={
			'TTL': '86400', 'Urgency': 'high', 'Authorization': authorization(setup, endpoint)})
		try:
			urllib.request.urlopen(request, timeout=TIMEOUT).read()
			sent += 1
		except urllib.error.HTTPError as exc:
			# the browser dropped the subscription: the phone turns them on again
			if exc.code in (404, 410):
				gone.append(phone['id'])
			logger.warning("push to phone %s: HTTP %d" % (phone['id'], exc.code))
		except (urllib.error.URLError, OSError) as exc:
			logger.warning("push to phone %s: %s" % (phone['id'], exc))
	if gone:
		def how(held):
			for p in held['phones']:
				if p['id'] in gone:
					p['subscription'] = None
		change(setup, how)
	return 'sent to %d of %d' % (sent, len(phones))


# ------------------------------------------------------------ the page

def state(service, phone):
	"""What the phone's page shows: the sessions, their open trades and the latest alerts."""
	setup = service.setup
	since = livesessions.today()
	sessions = []
	for s in service.live.sessions():
		if s.get('stopped') is not None and not s.get('open'):
			continue
		f = s.get('fields') or {}
		closed = s.get('closed') or []
		sessions.append({
			'id': s['id'], 'strategy': f.get('strategy'), 'instrument': f.get('instrument'),
			'granularity': f.get('granularity'), 'provider': s.get('provider'), 'demo': s.get('demo'),
			'running': bool(s.get('running')), 'exited': bool(s.get('exited')),
			'net': s.get('net'), 'today': sum(t['pl'] for t in closed
											  if t.get('pl') is not None and (t.get('time') or 0) >= since),
			'lastBar': s.get('lastBar'),
			'open': [{'units': t.get('units'), 'price': t.get('price'), 'time': t.get('time')}
					 for t in s.get('open') or []]})
	return {'title': notify.title(setup), 'accounts': livesessions.serverAccounts(),
			'phone': phone.get('name'), 'notifications': bool(phone.get('subscription')),
			'vapid': b64(vapid(setup)[1]), 'sessions': sessions,
			'alerts': notify.read(setup)['recent'][:SHOWN], 'at': now()}


def manifest(setup):
	name = notify.title(setup)
	return {'name': name, 'short_name': name, 'start_url': 'phone', 'scope': './',
			'display': 'standalone', 'background_color': '#f6f3ec', 'theme_color': '#1d2a36',
			'icons': [{'src': 'static/apple-touch-icon.png', 'sizes': '180x180', 'type': 'image/png'}]}


def asking(handler):
	"""The request's JSON object, or {}."""
	try:
		asked = json.loads(mcp.body(handler) or b'{}')
	except ValueError:
		return {}
	return asked if isinstance(asked, dict) else {}


def route(handler, method, path, query):
	"""The phone's own routes, before the access gate; False for any other."""
	if path not in PAGES and not path.startswith(API):
		return False
	setup = handler.service.setup
	if method == 'GET' and path == '/phone':
		handler.sendFile('phone.html')
		return True
	if method == 'GET' and path == '/phone-sw.js':
		# from the root, so that its scope is the whole service and not static/
		handler.sendFile('phone-sw.js')
		return True
	if method == 'GET' and path == '/phone-manifest.json':
		handler.sendJSON(manifest(setup))
		return True
	if handler.headers.get('X-Parity-Deriva') != '1' and method == 'POST':
		handler.sendError("missing X-Parity-Deriva header", 403)
		return True
	if method == 'POST' and path == API + 'pair':
		asked = asking(handler)
		token = pair(setup, asked.get('code'), asked.get('name'))
		if token is None:
			handler.sendError("that code is not one this server gave, or it is more than 10 minutes old", 403)
			return True
		cookie = '%s=%s; Path=%s; Max-Age=%d; HttpOnly; SameSite=Strict%s' % (
			COOKIE, token, access.cookiePath(handler), COOKIE_TTL, '; Secure' if access.secure(handler) else '')
		return mcp.reply(handler, 200, {'paired': True}, headers=[('Set-Cookie', cookie)])
	phone = phoneOf(handler)
	if phone is None:
		handler.sendError("this phone is not paired with this server", 401)
		return True
	if method == 'GET' and path == API + 'state':
		handler.sendJSON(state(handler.service, phone))
	elif method == 'GET' and path == API + 'alerts':
		handler.sendJSON({'title': notify.title(setup), 'alerts': pending(setup, phone['id'])})
	elif method == 'POST' and path == API + 'subscribe':
		try:
			subscribe(setup, phone['id'], asking(handler).get('subscription'))
		except ValueError as exc:
			handler.sendError(str(exc))
			return True
		handler.sendJSON({'notifications': True})
	else:
		handler.sendError("no such route", 404)
	return True


def settings(service, handler):
	"""The settings page's tab: the phones, the channels, and whether a trade server has its address."""
	setup = service.setup
	from parity_deriva.web import servers
	return {'phones': listed(setup), 'channels': notify.channels(setup),
			'publicUrl': getattr(setup, 'PUBLIC_URL', None) or '',
			'pairUrl': mcp.publicBase(handler) + '/phone',
			'tradeWithoutUrl': 'trade' in servers.roles(setup) and not getattr(setup, 'PUBLIC_URL', None)}
