"""
The alerts of a demo or real money server (docs/PIANO-FASE1.md C8): what it
tells whoever runs it when a session needs looking at.

notify() is the one way in. Every alert goes to the service's log and to
DATA_DIR/alerts.json, which the live and logs pages show as a banner; an
urgent one goes out as well on each channel configured - email (SMTP_*),
Telegram (TELEGRAM_*) and the phones paired (web/phone.py).

An alert is open from notify() to resolve(), under its kind and key (a
session's id, a day): the minute's round (web/livesessions.py alerts) calls
notify() for as long as a session is down, and only the first call goes out.
resolve() says "resolved" once, on the same channels. The text carries the
form, the event and its numbers - never an account, a token or a key.
"""

import email.message
import json
import logging
import os
import smtplib
import threading
import time
import urllib.parse
import urllib.request

LEVELS = ('urgent', 'info')
#: the alerts alerts.json keeps, newest first, the resolved ones included
RECENT = 100
#: seconds a channel may take before it counts as failed
TIMEOUT = 15

logger = logging.getLogger('parity_deriva.web')
_lock = threading.Lock()


def path(setup):
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'alerts.json')


def read(setup):
	"""{'open': {id: alert}, 'recent': [alert, ...]}, empty when there is none."""
	try:
		with open(path(setup)) as handle:
			held = json.load(handle)
	except (OSError, ValueError):
		held = {}
	return {'open': held.get('open') or {}, 'recent': held.get('recent') or []}


def _write(setup, held):
	name = path(setup)
	os.makedirs(os.path.dirname(name) or '.', exist_ok=True)
	with open(name + '.part', 'w') as handle:
		json.dump(held, handle)
	os.replace(name + '.part', name)


def _keep(setup, change):
	"""Read, change and write alerts.json under the lock; what `change` returns."""
	with _lock:
		held = read(setup)
		out = change(held)
		if out is not None:
			held['recent'] = held['recent'][:RECENT]
			_write(setup, held)
		return out


def notify(setup, level, kind, key, text):
	"""Open an alert and send it; None when it is open already."""
	if level not in LEVELS:
		raise ValueError("an alert is %s" % ' or '.join(LEVELS))
	ident = '%s:%s' % (kind, key)

	def change(held):
		if ident in held['open']:
			return None
		alert = {'id': ident, 'level': level, 'kind': kind, 'key': key, 'text': text,
				 'at': int(time.time() * 1000)}
		held['open'][ident] = alert
		held['recent'].insert(0, alert)
		return alert
	alert = _keep(setup, change)
	if alert is not None:
		send(setup, alert)
	return alert


def resolve(setup, kind, key):
	"""Close an open alert, with a "resolved" on its channels; None when none is open."""
	ident = '%s:%s' % (kind, key)

	def change(held):
		alert = held['open'].pop(ident, None)
		if alert is None:
			return None
		done = dict(alert, resolved=True, text='resolved: ' + alert['text'], at=int(time.time() * 1000))
		held['recent'].insert(0, done)
		return done
	done = _keep(setup, change)
	if done is not None:
		send(setup, done)
	return done


def dismiss(setup, ident):
	"""The ✕ on a banner: the alert closed without a message."""
	return _keep(setup, lambda held: held['open'].pop(ident, None))


def test(setup):
	"""An urgent alert that opens nothing, from the settings page: {channel: outcome}."""
	alert = {'id': 'test:%d' % time.time(), 'level': 'urgent', 'kind': 'test', 'key': '',
			 'text': 'a test alert from the settings page', 'at': int(time.time() * 1000)}

	def change(held):
		held['recent'].insert(0, alert)
		return alert
	_keep(setup, change)
	return send(setup, alert)


def title(setup):
	"""The server in a line: 'parity demo', or 'parity REAL' for real money."""
	return 'parity REAL' if getattr(setup, 'ACCOUNTS', 'demo') == 'real' else 'parity demo'


def send(setup, alert):
	"""The log always, and an urgent alert on each channel: {channel: 'sent', 'off' or the error}."""
	(logger.warning if alert['level'] == 'urgent' else logger.info)(
		"ALERT %s: %s" % (alert['level'], alert['text']))
	if alert['level'] != 'urgent':
		return {}
	from parity_deriva.web import phone
	out = {}
	for name, channel in (('email', byEmail), ('telegram', byTelegram), ('phones', phone.push)):
		try:
			out[name] = channel(setup, alert)
		except Exception as exc:
			logger.warning("alert by %s: %s" % (name, exc))
			out[name] = str(exc) or type(exc).__name__
	return out


def channels(setup):
	"""Which channels are configured: {email, telegram}."""
	return {'email': bool(getattr(setup, 'SMTP_HOST', None) and getattr(setup, 'SMTP_TO', None)),
			'telegram': bool(getattr(setup, 'TELEGRAM_TOKEN', None) and getattr(setup, 'TELEGRAM_CHAT', None))}


def byEmail(setup, alert):
	if not channels(setup)['email']:
		return 'off'
	message = email.message.EmailMessage()
	message['Subject'] = '[%s] %s' % (title(setup), alert['text'])
	message['From'] = getattr(setup, 'SMTP_FROM', None) or getattr(setup, 'SMTP_USER', None) or setup.SMTP_TO
	message['To'] = setup.SMTP_TO
	message.set_content('%s\n\n%s\n' % (alert['text'], time.strftime(
		'%Y-%m-%d %H:%M UTC', time.gmtime(alert['at'] / 1000.0))))
	port = int(getattr(setup, 'SMTP_PORT', None) or 587)
	connect = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
	with connect(setup.SMTP_HOST, port, timeout=TIMEOUT) as smtp:
		smtp.ehlo()
		if port != 465 and smtp.has_extn('starttls'):
			smtp.starttls()
			smtp.ehlo()
		if getattr(setup, 'SMTP_USER', None):
			smtp.login(setup.SMTP_USER, getattr(setup, 'SMTP_PASSWORD', None) or '')
		smtp.send_message(message)
	return 'sent'


def byTelegram(setup, alert):
	"""A message from the bot (made with BotFather) to TELEGRAM_CHAT, by the Bot API."""
	if not channels(setup)['telegram']:
		return 'off'
	data = urllib.parse.urlencode({'chat_id': setup.TELEGRAM_CHAT,
								   'text': '%s · %s' % (title(setup), alert['text'])}).encode()
	urllib.request.urlopen('https://api.telegram.org/bot%s/sendMessage' % setup.TELEGRAM_TOKEN,
						   data, timeout=TIMEOUT).read()
	return 'sent'
