"""
Who may open the pages and what they may do there: a client certificate, a
Google, Microsoft or GitHub account on a list, both at once, or nobody but this
PC - and the first start's setup that chooses. The MCP endpoint is not in
here: it has its own door (web/oauth.py), and the servers talk to each other
through it with their tokens.

DATA_DIR/server.json keeps it under "auth" (web/servers.py keeps the rest):

	{"mode": "oauth",
	 "allow": [{"who": "someone@x.it", "can": "write"}, {"who": "@digithera.it", "can": "read"},
			   {"who": "github:rrambaldi", "can": "write"}, {"who": "cert:Mario Rossi", "can": "read"}],
	 "providers": {"google": {"id": "..."}, "microsoft": {"id": "...", "tenant": "common"},
				   "github": {"id": "..."}}}

	cert   a certificate of the authority in DATA_DIR/ca, which the proxy checks
	oauth  an account on the list
	both   a certificate and an account on the list: the account says who, the
		   certificate is a second door in case the first has a hole
	none   this PC only: a request through a proxy is refused

"can" is write - may change things - or read: sees everything, changes nothing
(every POST refused but the few that only read, READS). An exact entry wins
over an @domain one. A certificate is in with write while the list names no
"cert:" at all; once it names one, only the listed certificates are.

The secrets - the providers' client secrets, the cookie's key - are in
DATA_DIR/.env, 0600, with what the setup writes there for etc/settings.py (the
accounts, the public address, the archive's address and token). The Docker
image sources it before it starts; server.json is what a profile is made of,
and a profile is handed around.

No "auth" at all is how it always was: a proxy in front, nginx with its client
certificate, and nothing checked here. Unless PARITY_DERIVA_WIZARD=1, which the
Docker image sets: then every page is the setup's until it is done, and the
setup asks for a code printed in the log, so that whoever finds the port first
is not the one who sets the server up. The Access tab changes the mode later,
and refuses a change that would shut out the one making it (locked()).

The proxy verifies the certificate and says whose it is in X-Client-Subject:
Caddy as docker/Caddyfile.template has it, nginx with `ssl_verify_client
optional;` and `proxy_set_header X-Client-Subject $ssl_client_s_dn;`. The MCP
routes are open, as the assistants have no certificate.
"""

import base64
import glob
import hashlib
import hmac
import http.cookies
import io
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse

import requests

from parity_deriva.data import market, sources
from parity_deriva.etc import settings
from parity_deriva.web import mcp, servers

MODES = ('cert', 'oauth', 'both', 'none')
CAN = ('write', 'read')
PROVIDERS = {
	'google': {'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
			   'token': 'https://oauth2.googleapis.com/token',
			   'scope': 'openid email profile'},
	'microsoft': {'authorize': 'https://login.microsoftonline.com/%s/oauth2/v2.0/authorize',
				  'token': 'https://login.microsoftonline.com/%s/oauth2/v2.0/token',
				  'scope': 'openid email profile User.Read'},
	'github': {'authorize': 'https://github.com/login/oauth/authorize',
			   'token': 'https://github.com/login/oauth/access_token',
			   'scope': 'read:user user:email'},
}
#: DATA_DIR/.env's name of a provider's client secret
SECRET = 'PARITY_DERIVA_OAUTH_%s_SECRET'
COOKIE = 'pd_session'
SETUP_COOKIE = 'pd_setup'
SESSION_TTL = 30 * 24 * 3600
#: how long the way to a provider and back may take
STATE_TTL = 600
#: always open, whatever the mode: the assistants' door, the pages' files, the sign-in
PUBLIC = ('/mcp', '/login', '/logout', '/api/access')
PUBLIC_UNDER = ('/oauth/', '/.well-known/', '/static/', '/auth/')
#: the POSTs a read-only user may make: they read, they change nothing
READS = ('/api/market/compare', '/api/market/impact')
#: the client_auth block of docker/Caddyfile.template in cert mode; /parity is
#: where docker-compose.yml mounts DATA_DIR in the caddy container
CLIENT_AUTH = '\t\tclient_auth {\n\t\t\tmode verify_if_given\n\t\t\ttrust_pool file /parity/ca/ca.crt\n\t\t}'
HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

logger = logging.getLogger('parity_deriva.web')

_lock = threading.Lock()
#: the sign-ins on their way to a provider: state -> {provider, verifier, next, until}
_pending = {}
#: the setup's code, the key of its cookie, and whether it is done and the
#: process on its way out: new every start
_setup = {'code': None, 'key': secrets.token_bytes(32), 'done': False}


class AccessError(Exception):
	"""A setting or a setup answer refused: the message says why."""


def dataDir(setup):
	return getattr(setup, 'DATA_DIR', '') or '.'


# ------------------------------------------------------------ DATA_DIR/.env

def envPath(setup):
	return os.path.join(dataDir(setup), '.env')


def envRead(setup):
	"""DATA_DIR/.env as {KEY: value}: the setup's secrets and settings."""
	out = {}
	try:
		with open(envPath(setup)) as handle:
			for line in handle:
				words = shlex.split(line, comments=True)
				if words[:1] == ['export']:
					words = words[1:]
				if words and '=' in words[0]:
					key, _, value = words[0].partition('=')
					out[key] = value
	except (OSError, ValueError):
		pass
	return out


def envKeep(changes, setup):
	"""Set these keys in DATA_DIR/.env, None drops one; the other lines stay."""
	path = envPath(setup)
	try:
		with open(path) as handle:
			lines = handle.read().splitlines()
	except OSError:
		lines = ['# written by the setup and the settings page (web/access.py); sourced by the Docker image']
	left = dict(changes)
	out = []
	for line in lines:
		words = line.split()
		key = (words[1] if words[:1] == ['export'] and len(words) > 1 else (words or [''])[0]).partition('=')[0]
		if key in left:
			value = left.pop(key)
			if value is not None:
				out.append('export %s=%s' % (key, shlex.quote(str(value))))
			continue
		out.append(line)
	out.extend('export %s=%s' % (k, shlex.quote(str(v))) for k, v in left.items() if v is not None)
	with open(os.open(path + '.part', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
		handle.write('\n'.join(out) + '\n')
	os.replace(path + '.part', path)


def secret(key, setup):
	"""A secret: DATA_DIR/.env first, which the pages write, then the environment and parity_deriva/.env."""
	return envRead(setup).get(key) or settings.dotenv(key) or ''


def cookieKey(setup):
	with _lock:
		key = secret('PARITY_DERIVA_COOKIE_KEY', setup)
		if not key:
			key = secrets.token_urlsafe(32)
			envKeep({'PARITY_DERIVA_COOKIE_KEY': key}, setup)
	return key.encode()


# ------------------------------------------------------------------ config

def stored(setup):
	"""server.json's "auth" as it is, a mode or not: {} for none."""
	found = servers.read(setup).get('auth')
	return dict(found) if isinstance(found, dict) else {}


def config(setup):
	"""server.json's "auth", or None when it has no mode (an authority made before one was chosen)."""
	found = stored(setup)
	return found if found.get('mode') in MODES else None


def wizard():
	return os.environ.get('PARITY_DERIVA_WIZARD') == '1'


def mode(setup):
	"""cert, oauth, both, none; 'setup' until the Docker image's setup is done; None: nothing checked here."""
	# the setup's answers are written but this process has the old environment:
	# setup still, until the one Docker starts again answers
	if _setup['done']:
		return 'setup'
	kept = config(setup)
	if kept:
		return kept['mode']
	return 'setup' if wizard() else None


def providers(kept, setup):
	"""The providers one can sign in with: an id here and a secret in DATA_DIR/.env."""
	return sorted(name for name, row in ((kept or {}).get('providers') or {}).items()
				  if name in PROVIDERS and (row or {}).get('id') and secret(SECRET % name.upper(), setup))


# ------------------------------------------------------------------ who

def sign(key, payload):
	body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).rstrip(b'=').decode()
	return body + '.' + hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def unsign(key, text):
	"""The payload of a cookie this server signed and that has not expired, else None."""
	body, _, mac = (text or '').rpartition('.')
	# bytes: compare_digest refuses a str that is not ASCII, and a cookie is anybody's
	if not body or not hmac.compare_digest(mac.encode('utf-8', 'replace'), hmac.new(
			key, body.encode('utf-8', 'replace'), hashlib.sha256).hexdigest().encode()):
		return None
	try:
		payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
	except ValueError:
		return None
	return payload if isinstance(payload, dict) and payload.get('until', 0) > time.time() else None


def cookies(handler):
	jar = http.cookies.SimpleCookie()
	try:
		jar.load(handler.headers.get('Cookie') or '')
	except http.cookies.CookieError:
		return {}
	return dict((k, v.value) for k, v in jar.items())


def entries(allow):
	"""The list as [{'who', 'can'}], lower case to match; a plain string may write."""
	out = []
	for row in allow or ():
		row = {'who': row} if isinstance(row, str) else row
		if isinstance(row, dict) and str(row.get('who') or '').strip():
			out.append({'who': ' '.join(str(row['who']).split()).lower(),
						'can': 'read' if row.get('can') == 'read' else 'write'})
	return out


def match(identities, allow):
	"""
	(identity, can) for the first of an account's identities the list lets in,
	an exact entry before an @domain one; None when it lets in none of them.
	"""
	rows = entries(allow)
	ids = [str(i).lower() for i in identities]
	for who in ids:
		for row in rows:
			if row['who'] == who:
				return who, row['can']
	for who in ids:
		if who.startswith(('github:', 'cert:')) or '@' not in who:
			continue
		for row in rows:
			if row['who'].startswith('@') and who.endswith(row['who']):
				return who, row['can']
	return None


def allowed(identities, allow):
	"""The identity the list lets in, or None."""
	found = match(identities, allow)
	return found[0] if found else None


def subjectName(subject):
	found = re.search(r'(?:^|[,/])\s*CN=([^,/]+)', subject or '')
	return found.group(1).strip() if found else (subject or '').strip()


def certificate(handler):
	"""The name on this request's client certificate, as the proxy says it; None without one."""
	return subjectName(handler.headers.get('X-Client-Subject')) or None


def certCan(name, allow):
	"""What a certificate may do: write while the list names no certificate, else its entry's; None: out."""
	rows = [r for r in entries(allow) if r['who'].startswith('cert:')]
	if not rows:
		return 'write'
	found = match(['cert:' + name], rows)
	return found[1] if found else None


def signed(handler, allow):
	"""(account, can) of the account this browser signed in with, if the list lets it in."""
	payload = unsign(cookieKey(handler.service.setup), cookies(handler).get(COOKIE))
	return match(payload.get('ids') or [], allow) if payload else None


def who(handler):
	"""(name, can) of whoever this request is in the mode there is; None for nobody the list lets in."""
	setup = handler.service.setup
	current = mode(setup)
	allow = (config(setup) or {}).get('allow')
	if current in ('cert', 'both'):
		name = certificate(handler)
		if not name:
			return None
		if current == 'cert':
			can = certCan(name, allow)
			return (name, can) if can else None
	if current in ('oauth', 'both'):
		# the list read again: somebody taken off it is out at the next click
		return signed(handler, allow)
	return None


def user(handler):
	found = who(handler)
	return found[0] if found else None


def setupDone(handler):
	return bool(unsign(_setup['key'], cookies(handler).get(SETUP_COOKIE)))


# ----------------------------------------------------------------- the gate

def public(handler, method, route, query):
	if method == 'OPTIONS' or route in PUBLIC or route.startswith(PUBLIC_UNDER):
		return True
	if method == 'GET' and (route == '/api/i18n' or route.startswith('/api/i18n/')):
		return True
	# the collector on forexfactory's page: its token, since no cookie crosses sites
	from parity_deriva.web.service import COLLECTOR_ROUTE
	return route == COLLECTOR_ROUTE and (query.get('token') or [''])[0] == handler.service.token


def base(handler):
	return mcp.publicBase(handler)


def cookiePath(handler):
	return urllib.parse.urlsplit(base(handler)).path or '/'


def secure(handler):
	return base(handler).startswith('https://')


def setCookie(handler, name, value, ttl):
	return '%s=%s; Path=%s; Max-Age=%d; HttpOnly; SameSite=Lax%s' % (
		name, value, cookiePath(handler), ttl, '; Secure' if secure(handler) else '')


def refuse(handler, method, route, status, message, page=None):
	"""A page goes to `page` (login, setup), anything else hears why not."""
	if page and method == 'GET' and not route.startswith('/api/'):
		target = base(handler) + '/' + page
		if page == 'login':
			target += '?next=' + urllib.parse.quote(handler.path, safe='')
		return mcp.reply(handler, 302, b'', headers=[('Location', target)])
	return mcp.reply(handler, status, {'error': message})


def reading(handler, route):
	"""A POST that only reads: a saved run read again, a sweep's size, the market data compared."""
	if route in READS:
		return True
	if route not in ('/api/backtest', '/api/sweep'):
		return False
	raw = mcp.body(handler)
	# put back for the route that reads the body after us. ponytail: fine while
	# the handler speaks HTTP/1.0, one request a connection; with keep-alive the
	# next request would be read from this copy
	handler.rfile = io.BytesIO(raw)
	try:
		body = json.loads(raw or b'{}')
	except ValueError:
		return False
	return isinstance(body, dict) and bool(body.get('cachedOnly' if route == '/api/backtest' else 'dry'))


def gate(handler, method, route, query):
	"""Answer a request this server does not let through; True when answered."""
	current = mode(handler.service.setup)
	if current is None or public(handler, method, route, query):
		return False
	if current == 'setup':
		if route == '/setup' or route.startswith('/api/setup/'):
			return False
		return refuse(handler, method, route, 403, "this server is not set up yet: open setup", 'setup')
	if current == 'none':
		if handler.headers.get('X-Forwarded-For'):
			return refuse(handler, method, route, 403,
						  "this server lets in its own PC only, and this request came through a proxy",
						  'login?error=proxy')
		return False
	if current in ('cert', 'both') and not certificate(handler):
		return refuse(handler, method, route, 403, "this server wants a client certificate", 'login?error=certificate')
	found = who(handler)
	if not found:
		if current == 'cert':
			return refuse(handler, method, route, 403, "this certificate is not on the list", 'login?error=not-allowed')
		return refuse(handler, method, route, 401, "sign in first", 'login')
	if found[1] == 'read' and method == 'POST' and not reading(handler, route):
		return refuse(handler, method, route, 403, "read only: %s may look, not change" % found[0])
	return False


# ---------------------------------------------------------------- sign-in

def redirectUri(handler, name):
	return '%s/auth/callback/%s' % (base(handler), name)


def start(handler, name, query):
	"""Off to the provider, with a state to know the way back by, and PKCE."""
	setup = handler.service.setup
	kept = stored(setup)
	# any mode but the setup: the Access tab signs in to try before it switches to an account
	if mode(setup) == 'setup' or name not in providers(kept, setup):
		return mcp.reply(handler, 302, b'', headers=[('Location', base(handler) + '/login?error=failed')])
	back = (query.get('next') or ['/'])[0]
	# a path of this server's only: never //elsewhere
	back = back if back.startswith('/') and not back.startswith('//') else '/'
	state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
	now = time.time()
	with _lock:
		for old in [k for k, v in _pending.items() if v['until'] < now]:
			del _pending[old]
		# ponytail: in memory, one process; a restart loses the sign-ins on their way
		_pending[state] = {'provider': name, 'verifier': verifier, 'next': back, 'until': now + STATE_TTL}
	row = PROVIDERS[name]
	tenant = (kept['providers'][name].get('tenant') or 'common')
	params = {'client_id': kept['providers'][name]['id'], 'redirect_uri': redirectUri(handler, name),
			  'response_type': 'code', 'scope': row['scope'], 'state': state}
	if name != 'github':
		params.update(code_challenge=base64.urlsafe_b64encode(
			hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(), code_challenge_method='S256')
	if name == 'google':
		params['prompt'] = 'select_account'
	url = (row['authorize'] % tenant if '%s' in row['authorize'] else row['authorize'])
	return mcp.reply(handler, 302, b'', headers=[('Location', url + '?' + urllib.parse.urlencode(params))])


def identities(name, token):
	"""What the provider says the account is: its verified addresses, and github:<login>."""
	headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
	if name == 'google':
		found = requests.get('https://openidconnect.googleapis.com/v1/userinfo', headers=headers, timeout=20).json()
		return [found['email']] if found.get('email') and found.get('email_verified') else []
	if name == 'microsoft':
		found = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=20).json()
		# a guest's name in another tenant is not an address (#EXT#)
		return [a for a in (found.get('mail'), found.get('userPrincipalName')) if a and '#' not in a]
	found = requests.get('https://api.github.com/user', headers=headers, timeout=20).json()
	emails = requests.get('https://api.github.com/user/emails', headers=headers, timeout=20).json()
	return ['github:' + found['login']] * bool(found.get('login')) + [
		e['email'] for e in emails if isinstance(e, dict) and e.get('verified') and e.get('email')]


def callback(handler, name, query):
	"""Back from the provider: the code for a token, the token for who it is, the list for whether in."""
	setup = handler.service.setup
	kept = stored(setup)
	failed = [('Location', base(handler) + '/login?error=failed')]
	with _lock:
		pending = _pending.pop((query.get('state') or [''])[0], None)
	code = (query.get('code') or [''])[0]
	if not pending or pending['provider'] != name or pending['until'] < time.time() or not code \
			or mode(setup) == 'setup' or name not in providers(kept, setup):
		return mcp.reply(handler, 302, b'', headers=failed)
	tenant = kept['providers'][name].get('tenant') or 'common'
	url = PROVIDERS[name]['token']
	form = {'client_id': kept['providers'][name]['id'], 'client_secret': secret(SECRET % name.upper(), setup),
			'code': code, 'redirect_uri': redirectUri(handler, name), 'grant_type': 'authorization_code'}
	if name != 'github':
		form['code_verifier'] = pending['verifier']
	try:
		answer = requests.post(url % tenant if '%s' in url else url, data=form,
							   headers={'Accept': 'application/json'}, timeout=20).json()
		ids = identities(name, answer['access_token'])
	except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
		logger.warning("sign-in with %s failed: %s", name, exc)
		return mcp.reply(handler, 302, b'', headers=failed)
	who = allowed(ids, kept.get('allow'))
	if not who:
		logger.warning("sign-in with %s refused: %s", name, ', '.join(ids) + ' not on the list' if ids else 'no verified address')
		return mcp.reply(handler, 302, b'', headers=[('Location', base(handler) + '/login?error=not-allowed')])
	logger.info("signed in with %s: %s", name, who)
	value = sign(cookieKey(setup), {'ids': [i.lower() for i in ids], 'by': name,
									'until': int(time.time()) + SESSION_TTL})
	return mcp.reply(handler, 302, b'', headers=[('Location', base(handler) + pending['next']),
												 ('Set-Cookie', setCookie(handler, COOKIE, value, SESSION_TTL))])


# ------------------------------------------------------------ certificates

def caDir(setup):
	return os.path.join(dataDir(setup), 'ca')


def openssl(*args, env=None, stdin=None):
	try:
		done = subprocess.run(('openssl',) + args, input=stdin, capture_output=True, timeout=60,
							  env=dict(os.environ, **(env or {})))
	except (OSError, subprocess.TimeoutExpired) as exc:
		raise AccessError("openssl: %s" % exc)
	if done.returncode:
		raise AccessError("openssl: %s" % done.stderr.decode('utf-8', 'replace').strip()[-300:])
	return done.stdout


def makeCA(setup):
	"""A certificate authority of this server's, in DATA_DIR/ca: ten years."""
	where = caDir(setup)
	os.makedirs(where, mode=0o700, exist_ok=True)
	key = os.path.join(where, 'ca.key')
	openssl('req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-keyout', key,
			'-out', os.path.join(where, 'ca.crt'), '-days', '3650', '-sha256',
			'-subj', '/O=parity-deriva/CN=parity-deriva CA %s' % socket.gethostname()[:30])
	os.chmod(key, 0o600)


def keepCA(pem, setup):
	"""Somebody else's authority: its certificate only, so no certificate is issued here."""
	pem = str(pem or '').strip()
	if 'BEGIN CERTIFICATE' not in pem:
		raise AccessError("the authority's certificate, PEM: -----BEGIN CERTIFICATE-----...")
	openssl('x509', '-noout', '-subject', stdin=pem.encode())
	where = caDir(setup)
	os.makedirs(where, mode=0o700, exist_ok=True)
	if os.path.exists(os.path.join(where, 'ca.key')):
		os.remove(os.path.join(where, 'ca.key'))
	with open(os.path.join(where, 'ca.crt'), 'w') as handle:
		handle.write(pem + '\n')


def certName(text):
	"""A name as a certificate carries it: letters, digits, space . @ - and no more, 60 at most."""
	return ' '.join(re.sub(r'[^\w .@-]', '', str(text or '')).split())[:60]


def authorityHeld(setup):
	"""own: DATA_DIR/ca has the key too; external: only the certificate; None: no authority."""
	where = caDir(setup)
	if os.path.exists(os.path.join(where, 'ca.key')):
		return 'own'
	return 'external' if os.path.exists(os.path.join(where, 'ca.crt')) else None


def authorityShown(setup):
	"""The authority's name and the day it expires, read from its certificate; None without one."""
	path = os.path.join(caDir(setup), 'ca.crt')
	if not os.path.exists(path):
		return None
	try:
		text = openssl('x509', '-in', path, '-noout', '-subject', '-enddate', '-nameopt', 'RFC2253').decode()
	except AccessError:
		return {'name': '?', 'until': None}
	found = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
	try:
		until = time.strftime('%Y-%m-%d', time.strptime(found.get('notAfter', '').strip(), '%b %d %H:%M:%S %Y %Z'))
	except ValueError:
		until = None
	return {'name': subjectName(found.get('subject', '')), 'until': until}


def issuedHere(setup):
	"""The certificates this server's authority issued, the newest first (DATA_DIR/ca/issued.log)."""
	try:
		with open(os.path.join(caDir(setup), 'issued.log')) as handle:
			lines = handle.read().splitlines()
	except OSError:
		return []
	out = []
	for line in reversed(lines[-200:]):
		words = line.split(' ', 3)
		if len(words) == 4:
			out.append({'when': words[0] + ' ' + words[1], 'serial': words[2], 'name': words[3]})
	return out


def issue(name, setup):
	"""A client certificate for a person, signed here: (the .p12's bytes, its password)."""
	name = certName(name)
	where = caDir(setup)
	if not name:
		raise AccessError("whose certificate: a name")
	if not os.path.exists(os.path.join(where, 'ca.key')):
		raise AccessError("this server keeps no authority's key: issue the certificate where the authority is")
	scratch = tempfile.mkdtemp(dir=where)
	try:
		key, csr, crt, p12, ext = (os.path.join(scratch, n) for n in ('k', 'csr', 'crt', 'p12', 'ext'))
		with open(ext, 'w') as handle:
			handle.write('keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=clientAuth\n')
		openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', csr, '-utf8',
				'-subj', '/O=parity-deriva/CN=%s' % name)
		serial = '0x' + secrets.token_hex(16)
		openssl('x509', '-req', '-in', csr, '-CA', os.path.join(where, 'ca.crt'),
				'-CAkey', os.path.join(where, 'ca.key'), '-set_serial', serial, '-out', crt,
				'-days', '825', '-sha256', '-extfile', ext)
		password = secrets.token_urlsafe(9)
		openssl('pkcs12', '-export', '-out', p12, '-inkey', key, '-in', crt,
				'-certfile', os.path.join(where, 'ca.crt'), '-name', '%s (parity-deriva)' % name,
				'-passout', 'env:PD_P12', env={'PD_P12': password})
		with open(p12, 'rb') as handle:
			blob = handle.read()
	finally:
		shutil.rmtree(scratch, ignore_errors=True)
	# ponytail: no revocation; a CRL in the Caddyfile when one is needed
	with open(os.path.join(where, 'issued.log'), 'a') as handle:
		handle.write('%s %s %s\n' % (time.strftime('%Y-%m-%d %H:%M'), serial, name))
	return blob, password


# ------------------------------------------------------------------ caddy

def caddyfile(setup):
	"""
	DATA_DIR/Caddyfile for the caddy container, which reloads it when it
	changes: the domain of PARITY_DERIVA_DOMAIN, and the client certificate
	asked for (not required) once there is an authority. Nothing without the domain.
	"""
	domain = os.environ.get('PARITY_DERIVA_DOMAIN', '').strip()
	if not domain:
		return None
	if not re.fullmatch(r'[A-Za-z0-9.-]+', domain):
		logger.error("PARITY_DERIVA_DOMAIN %r is not a domain: no Caddyfile", domain)
		return None
	with open(os.path.join(HOME, 'docker', 'Caddyfile.template')) as handle:
		text = handle.read()
	# whenever there is an authority, whatever the mode: a certificate is seen,
	# and so tried, before a mode that wants one is chosen
	certs = authorityHeld(setup) is not None
	text = text.replace('@@DOMAIN@@', domain).replace('@@CLIENT_AUTH@@', CLIENT_AUTH if certs else '')
	path = os.path.join(dataDir(setup), 'Caddyfile')
	try:
		with open(path) as handle:
			if handle.read() == text:
				return path
	except OSError:
		pass
	with open(path + '.part', 'w') as handle:
		handle.write(text)
	os.replace(path + '.part', path)
	return path


# ------------------------------------------------------------------ setup

def begin(setup):
	"""At the service's start: the Caddyfile, and in setup mode the code, in the log."""
	try:
		caddyfile(setup)
	except OSError as exc:
		logger.error("no Caddyfile: %s", exc)
	if mode(setup) == 'setup':
		_setup['code'] = '-'.join(''.join(secrets.choice(CODE_LETTERS) for _ in range(4)) for _ in range(2))
		# the log and the console both: `docker compose logs parity` shows the console
		logger.warning("parity-deriva setup code: %s", _setup['code'])
		print("parity-deriva setup code: %s  (open /setup)" % _setup['code'], flush=True)


def profiles():
	"""The example profiles of etc/profiles."""
	out = []
	for path in sorted(glob.glob(os.path.join(HOME, 'etc', 'profiles', '*.json'))):
		try:
			with open(path) as handle:
				out.append({'file': os.path.basename(path), 'profile': json.load(handle)})
		except (OSError, ValueError):
			continue
	return out


def state(handler):
	here = base(handler)
	return {'profiles': profiles(), 'base': here, 'proxied': bool(handler.headers.get('X-Forwarded-For')),
			'domain': os.environ.get('PARITY_DERIVA_DOMAIN') or None,
			'callbacks': dict((n, redirectUri(handler, n)) for n in PROVIDERS),
			'roles': list(servers.ROLES), 'openssl': shutil.which('openssl') is not None}


def checkArchive(asked):
	"""The archive's market_status with this token: whether they answer."""
	url, token = str(asked.get('url') or '').strip(), str(asked.get('token') or '').strip()
	if not url.startswith(('https://', 'http://')) or not token:
		raise AccessError("the archive's MCP address, https://.../mcp, and a token made on it")
	try:
		found = sources.rpc({'url': url, 'token': token}, 'market_status', {}, timeout=30)
	except sources.SourceError as exc:
		raise AccessError(str(exc))
	return {'ok': True, 'instruments': len(found.get('instruments') or [])}


def cleanAllow(allow, empty=False):
	"""The list as server.json keeps it, each entry checked: [{'who', 'can'}], sorted."""
	if not isinstance(allow, list):
		raise AccessError("who may enter: a list")
	out = {}
	for row in allow:
		row = {'who': row} if isinstance(row, str) else row
		if not isinstance(row, dict):
			raise AccessError("who may enter: {who, can} each")
		name = ' '.join(str(row.get('who') or '').split())
		if not name:
			continue
		if name.lower().startswith('cert:'):
			name = 'cert:' + certName(name[5:])
			if name == 'cert:':
				raise AccessError("cert:<the name on the certificate>")
		else:
			name = name.lower()
			if not (name.startswith('github:') and len(name) > 7) and not re.fullmatch(r'[^@\s]*@[^@\s]+\.[^@\s]+', name):
				raise AccessError("%r: an address, @domain, github:<login> or cert:<name>" % name)
		level = row.get('can') or 'write'
		if level not in CAN:
			raise AccessError("%s: authorizing (write) or read only (read)" % name)
		out[name.lower()] = {'who': name, 'can': level}
	if not out and not empty:
		raise AccessError("who may enter: at least one address, @domain, github:<login> or cert:<name>")
	return sorted(out.values(), key=lambda r: r['who'].lower())


def publicUrl(text):
	text = str(text or '').strip().rstrip('/')
	host = urllib.parse.urlsplit(text).hostname
	if not (text.startswith('https://') or (text.startswith('http://') and host in ('localhost', '127.0.0.1'))):
		raise AccessError("the address this server is reached at, https://...: the providers send back there")
	return text


def cleanProviders(asked, kept, setup):
	"""
	The providers asked for, as server.json keeps them, and the secrets for
	DATA_DIR/.env: a secret left empty keeps the one there is, an id left
	empty drops the provider.
	"""
	if not isinstance(asked, dict):
		raise AccessError("the providers: google, microsoft, github")
	out, env = dict(kept or {}), {}
	for name, row in asked.items():
		if name not in PROVIDERS or not isinstance(row, dict):
			raise AccessError("%r: a provider among %s" % (name, ', '.join(PROVIDERS)))
		ident = str(row.get('id') or '').strip()
		if not ident:
			out.pop(name, None)
			env[SECRET % name.upper()] = None
			continue
		out[name] = {'id': ident}
		if name == 'microsoft':
			tenant = str(row.get('tenant') or 'common').strip()
			if not re.fullmatch(r'[A-Za-z0-9.-]+', tenant):
				raise AccessError("microsoft: the tenant is common, organizations, a domain or the tenant's id")
			out[name]['tenant'] = tenant
		if str(row.get('secret') or '').strip():
			env[SECRET % name.upper()] = str(row['secret']).strip()
		elif not secret(SECRET % name.upper(), setup):
			raise AccessError("%s: the client secret" % name)
	return out, env


def finish(handler, answer):
	"""
	Everything the setup asked, written: the authority, the market, the roles,
	DATA_DIR/.env and last server.json's "auth", which ends the setup. Then the
	process goes, and Docker's restart policy starts it again on the new .env.
	A step refused leaves the server in setup, to answer again.
	"""
	setup = handler.service.setup
	if not isinstance(answer, dict):
		raise AccessError("the setup's answers, as JSON")
	auth = answer.get('auth') if isinstance(answer.get('auth'), dict) else {}
	chosen = auth.get('mode')
	roles = answer.get('roles')
	if chosen not in MODES:
		raise AccessError("how one gets in: cert, oauth, both or none")
	if not isinstance(roles, list) or not roles or any(r not in servers.ROLES for r in roles):
		raise AccessError("the roles: one or more of %s" % ', '.join(servers.ROLES))
	accounts = answer.get('accounts') or 'demo'
	if accounts not in ('demo', 'real'):
		raise AccessError("the accounts: demo or real")
	archive = answer.get('archive') if isinstance(answer.get('archive'), dict) else {}
	url, token = str(archive.get('url') or '').strip(), str(archive.get('token') or '').strip()
	if 'archive' not in roles and (not url.startswith(('https://', 'http://')) or not token):
		raise AccessError("a server that is not the archive: the archive's MCP address and a token made on it")
	data = answer.get('market') if isinstance(answer.get('market'), dict) else {}
	candles, calendar = data.get('candles') or 'manual', data.get('calendar') or 'manual'
	if candles not in market.KINDS['candles'] or calendar not in market.KINDS['calendar']:
		raise AccessError("the market data: candles from %s, the calendar from %s" % (
			', '.join(market.KINDS['candles']), ', '.join(market.KINDS['calendar'])))
	if 'upstream' in (candles, calendar) and not url:
		raise AccessError("the market data from the archive: the archive's address and token")

	kept, env = {'mode': chosen}, {}
	out = {'ok': True, 'p12': None, 'password': None, 'promote': None, 'mcp': base(handler) + '/mcp',
		   'restart': True}
	if chosen == 'none' and handler.headers.get('X-Forwarded-For'):
		raise AccessError("no access control is for the PC this runs on, and you came through a proxy")
	if chosen in ('oauth', 'both'):
		kept['allow'] = cleanAllow(auth.get('allow'))
		kept['providers'], found = cleanProviders(auth.get('providers') or {}, {}, setup)
		if not kept['providers']:
			raise AccessError("at least one of google, microsoft, github, with its client id and secret")
		env.update(found)
		env['PARITY_DERIVA_PUBLIC_URL'] = publicUrl(auth.get('public_url'))
	if chosen in ('cert', 'both'):
		# nothing would check the certificate, and every page would be refused
		if not (os.environ.get('PARITY_DERIVA_DOMAIN') or handler.headers.get('X-Forwarded-For')):
			raise AccessError("a certificate is checked by the proxy in front, and there is none: set "
							  "PARITY_DOMAIN and start with --profile https, or put your own proxy in front")
		ca = answer.get('ca') if isinstance(answer.get('ca'), dict) else {}
		if ca.get('pem'):
			keepCA(ca['pem'], setup)
			kept['ca'] = 'external'
		else:
			if not str(ca.get('name') or '').strip():
				raise AccessError("the first certificate: whose, a name")
			makeCA(setup)
			kept['ca'] = 'own'
			blob, out['password'] = issue(ca['name'], setup)
			out['p12'] = base64.b64encode(blob).decode()

	changes = {'writer': True, 'candles': {'source': candles, 'every': 60},
			   'calendar': {'source': calendar, 'every': 60}}
	if url:
		changes['upstream'] = {'url': url, 'token': token}
	if data.get('provider'):
		changes['provider'] = data['provider']
	try:
		market.save(changes, setup)
	except market.MarketError as exc:
		raise AccessError(str(exc))
	servers.saveRoles(roles, setup)
	if 'trade' in roles:
		env['PARITY_DERIVA_ACCOUNTS'] = accounts
		# for the archive's settings, trade servers: it pushes and reads with it
		out['promote'] = handler.service.oauth.newKey('archive', 'promote')
	if url:
		env['PARITY_DERIVA_ARCHIVE_URL'] = url
		if 'test' in roles:
			env['PARITY_DERIVA_SYNC_TOKEN'] = token
	envKeep(env, setup)
	name = ' '.join(str(answer.get('name') or '').split())[:60]
	if name:
		servers.keep('name', name, setup)
	servers.keep('auth', kept, setup)
	caddyfile(setup)
	_setup['done'] = True
	logger.warning("setup done: %s, roles %s; restarting", chosen, ', '.join(roles))
	# after the answer has gone; os._exit, as SIGHUP's exec would keep the old
	# environment and the Docker image sources DATA_DIR/.env only when it starts
	threading.Timer(1.5, os._exit, (0,)).start()
	return out


# --------------------------------------------------------- settings page

def view(handler):
	"""The Access tab's settings: no secret, only whether one is there; and what this browser brings."""
	setup = handler.service.setup
	kept = stored(setup)
	shown = {}
	for name, row in (kept.get('providers') or {}).items():
		shown[name] = dict(row, secret=bool(secret(SECRET % name.upper(), setup)))
	found = who(handler)
	me = signed(handler, kept.get('allow'))
	current = mode(setup)
	return {'mode': current if current in MODES else None, 'allow': cleanAllow(kept.get('allow') or [], empty=True),
			'providers': shown,
			'public_url': secret('PARITY_DERIVA_PUBLIC_URL', setup) or getattr(setup, 'PUBLIC_URL', None) or '',
			'callbacks': dict((n, redirectUri(handler, n)) for n in PROVIDERS), 'ca': authorityHeld(setup),
			'authority': authorityShown(setup), 'issued': issuedHere(setup),
			'user': found[0] if found else None, 'can': can(handler, found),
			'signed': me[0] if me else None, 'certificate': certificate(handler),
			'proxied': bool(handler.headers.get('X-Forwarded-For')),
			'domain': os.environ.get('PARITY_DERIVA_DOMAIN') or None}


def can(handler, found):
	"""write or read for whoever this is; write with nothing checked here (none, or the proxy's)."""
	if found:
		return found[1]
	return 'write' if mode(handler.service.setup) in ('none', None) else None


def locked(handler, new, env, switching=True):
	"""
	Refuse a setting that would shut out the one saving it, or leave them read
	only, saying why: what the mode needs must be there, and this very request
	must already pass it.
	"""
	setup = handler.service.setup
	target, allow = new.get('mode'), new.get('allow') or []
	if target == 'none':
		if handler.headers.get('X-Forwarded-For'):
			raise AccessError("nothing checked lets in the PC this runs on only, and you came through a proxy")
		return
	if target in ('cert', 'both'):
		if not authorityHeld(setup):
			raise AccessError("a certificate needs an authority first: make one here or load yours")
		name = certificate(handler)
		if not name:
			raise AccessError("this browser brought no client certificate: import yours, reload the page, then change")
		if target == 'cert':
			held = certCan(name, allow)
			if held != 'write':
				raise AccessError("the list %s your certificate (%s)" % (
					'lets only look' if held else 'leaves out', name))
			return
	pending = lambda key: env[key] if key in env else secret(key, setup)
	if not [n for n, r in (new.get('providers') or {}).items() if r.get('id') and pending(SECRET % n.upper())]:
		raise AccessError("an account needs a provider first: its client id and secret")
	# on a switch only: a server on an account already signs in at the address it has
	if switching and not (pending('PARITY_DERIVA_PUBLIC_URL') or getattr(setup, 'PUBLIC_URL', None)):
		raise AccessError("an account needs this server's public address: the providers send back there")
	me = signed(handler, allow)
	if not me:
		raise AccessError("sign in first with a provider (the links under Accounts), with an account the list lets in")
	if me[1] != 'write':
		raise AccessError("the list lets you (%s) only look: keep yourself authorizing" % me[0])


def change(handler, asked):
	"""The Access tab's save: the mode, who may enter and what they may do, the providers, the public address."""
	setup = handler.service.setup
	if not isinstance(asked, dict):
		raise AccessError("the changes, as JSON")
	kept = stored(setup)
	new = dict(kept)
	if 'mode' in asked:
		if asked['mode'] not in MODES:
			raise AccessError("how one gets in: cert, oauth, both or none")
		new['mode'] = asked['mode']
	env = {}
	if 'allow' in asked:
		new['allow'] = cleanAllow(asked['allow'], empty=True)
	if 'providers' in asked:
		new['providers'], found = cleanProviders(asked['providers'], kept.get('providers'), setup)
		env.update(found)
	if 'public_url' in asked:
		env['PARITY_DERIVA_PUBLIC_URL'] = publicUrl(asked['public_url'])
	# no mode yet - the proxy's, as always - checks nothing: the providers kept
	# to sign in with and try, before the switch to them
	if new.get('mode') in MODES:
		locked(handler, new, env, switching=new['mode'] != kept.get('mode'))
	if env:
		envKeep(env, setup)
	servers.keep('auth', new, setup)
	if new.get('mode') != kept.get('mode'):
		logger.warning("access: %s -> %s, by %s", kept.get('mode') or 'the proxy', new['mode'],
					   certificate(handler) or (signed(handler, new.get('allow')) or ['this PC'])[0])
	caddyfile(setup)
	return view(handler)


def authority(handler, asked):
	"""The Access tab's authority: one made here, or somebody else's certificate. Not under a mode that uses it."""
	setup = handler.service.setup
	kept = stored(setup)
	if kept.get('mode') in ('cert', 'both'):
		raise AccessError("the certificates in use would stop working: change how people get in first")
	if isinstance(asked, dict) and asked.get('pem'):
		keepCA(asked['pem'], setup)
		kept['ca'] = 'external'
	elif isinstance(asked, dict) and asked.get('make'):
		makeCA(setup)
		kept['ca'] = 'own'
	else:
		raise AccessError("make: true, or pem: the authority's certificate")
	servers.keep('auth', kept, setup)
	caddyfile(setup)
	return view(handler)


def newCertificate(handler, asked):
	"""A certificate issued from the Access tab, and its name on the list with what it may do."""
	setup = handler.service.setup
	asked = asked if isinstance(asked, dict) else {}
	name, allowed_ = certName(asked.get('name')), asked.get('can') or 'write'
	if allowed_ not in CAN:
		raise AccessError("authorizing (write) or read only (read)")
	blob, password = issue(name, setup)
	kept = stored(setup)
	rows = list(kept.get('allow') or [])
	mine = certificate(handler)
	# the first certificate named would shut out every other one, the one of
	# whoever is issuing it too: that one goes on the list first
	if kept.get('mode') == 'cert' and mine and not any(r['who'].startswith('cert:') for r in entries(rows)):
		rows.append({'who': 'cert:' + certName(mine), 'can': 'write'})
	rows.append({'who': 'cert:' + name, 'can': allowed_})
	kept['allow'] = cleanAllow(rows, empty=True)
	servers.keep('auth', kept, setup)
	logger.warning("client certificate issued for %s (%s) by %s", name, allowed_, user(handler) or 'this PC')
	return {'p12': base64.b64encode(blob).decode(), 'password': password, 'settings': view(handler)}


def profile(setup):
	"""This server as a profile another one can start from: no secret, no authority."""
	kept = config(setup) or {}
	auth = {'mode': kept.get('mode')}
	if kept.get('allow'):
		auth['allow'] = cleanAllow(kept['allow'], empty=True)
	if kept.get('mode') in ('oauth', 'both'):
		auth.update(providers=dict(
			(n, dict((k, v) for k, v in r.items() if k in ('id', 'tenant'))) for n, r in (kept.get('providers') or {}).items()),
			public_url=secret('PARITY_DERIVA_PUBLIC_URL', setup))
	held = servers.roles(setup)
	found = market.config(setup)
	out = {'profile': 'parity-deriva', 'version': 1,
		   'name': servers.read(setup).get('name') or 'parity-deriva on %s' % socket.gethostname(),
		   'auth': auth, 'roles': held, 'accounts': getattr(setup, 'ACCOUNTS', 'demo'),
		   'market': {'candles': found['candles']['source'], 'calendar': found['calendar']['source']}}
	url = secret('PARITY_DERIVA_ARCHIVE_URL', setup) or found['upstream']['url']
	if 'archive' not in held and url:
		out['archive'] = {'url': url}
	return out


# ----------------------------------------------------------------- routes

def asked(handler):
	try:
		found = json.loads(mcp.body(handler) or b'{}')
	except ValueError:
		raise AccessError("not JSON")
	return found


def route(handler, method, path, query):
	"""The sign-in, the setup and the Access tab's routes; False for any other."""
	setup = handler.service.setup
	current = mode(setup)
	try:
		if method == 'GET' and path == '/login':
			# sendFile answers None: True here, or the routes after it answer again
			handler.sendFile('login.html')
			return True
		if method == 'GET' and path == '/api/access':
			found = who(handler)
			return mcp.reply(handler, 200, {'mode': current if current in MODES else None,
											'setup': current == 'setup', 'user': found[0] if found else None,
											'can': can(handler, found),
											'providers': [] if current == 'setup' else providers(stored(setup), setup)})
		if method == 'GET' and path.startswith('/auth/start/'):
			return start(handler, path[len('/auth/start/'):], query)
		if method == 'GET' and path.startswith('/auth/callback/'):
			return callback(handler, path[len('/auth/callback/'):], query)
		if method == 'GET' and path == '/logout':
			return mcp.reply(handler, 302, b'', headers=[('Location', base(handler) + '/login'),
														 ('Set-Cookie', setCookie(handler, COOKIE, '', 0))])
		if path == '/setup' or path.startswith('/api/setup/'):
			return setupRoute(handler, method, path)
		if path.startswith('/api/access/'):
			if method == 'GET' and path == '/api/access/settings':
				return mcp.reply(handler, 200, view(handler))
			if method == 'GET' and path == '/api/access/profile':
				return mcp.reply(handler, 200, json.dumps(profile(setup), indent=1).encode(), 'application/json',
								 headers=[('Content-Disposition', 'attachment; filename="parity-deriva-profile.json"')])
			if method != 'POST':
				return False
			if handler.headers.get('X-Parity-Deriva') != '1':
				return mcp.reply(handler, 403, {'error': "missing X-Parity-Deriva header"})
			if path == '/api/access/settings':
				return mcp.reply(handler, 200, change(handler, asked(handler)))
			if path == '/api/access/cert':
				return mcp.reply(handler, 200, newCertificate(handler, asked(handler)))
			if path == '/api/access/ca':
				return mcp.reply(handler, 200, authority(handler, asked(handler)))
	except (AccessError, ValueError) as exc:
		return mcp.reply(handler, 400, {'error': str(exc)})
	return False


def setupRoute(handler, method, route):
	if mode(handler.service.setup) != 'setup':
		if route == '/setup':
			return mcp.reply(handler, 302, b'', headers=[('Location', base(handler) + '/')])
		return mcp.reply(handler, 404, {'error': "this server is set up already"})
	if method == 'GET' and route == '/setup':
		handler.sendFile('setup.html')
		return True
	if _setup['done']:
		return mcp.reply(handler, 409, {'error': "the setup is done: the server is starting again"})
	if method == 'POST' and handler.headers.get('X-Parity-Deriva') != '1':
		return mcp.reply(handler, 403, {'error': "missing X-Parity-Deriva header"})
	if method == 'POST' and route == '/api/setup/code':
		typed = re.sub(r'[^A-Z0-9]', '', str(asked(handler).get('code') or '').upper())
		if not _setup['code'] or not hmac.compare_digest(typed, _setup['code'].replace('-', '')):
			# a second a wrong guess: eight letters of 32 are not guessed at that pace
			time.sleep(1)
			return mcp.reply(handler, 403, {'error': "not the code in the log"})
		value = sign(_setup['key'], {'until': int(time.time()) + 6 * 3600})
		return mcp.reply(handler, 200, {'ok': True},
						 headers=[('Set-Cookie', setCookie(handler, SETUP_COOKIE, value, 6 * 3600))])
	if not setupDone(handler):
		return mcp.reply(handler, 403, {'error': "the setup code first"})
	if method == 'GET' and route == '/api/setup/state':
		return mcp.reply(handler, 200, state(handler))
	if method == 'POST' and route == '/api/setup/archive':
		return mcp.reply(handler, 200, checkArchive(asked(handler)))
	if method == 'POST' and route == '/api/setup/finish':
		return mcp.reply(handler, 200, finish(handler, asked(handler)))
	return mcp.reply(handler, 404, {'error': "no route %s" % route})
