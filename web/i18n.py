"""
The pages' languages (web/static/i18n.js). English is the code's own, and a
catalogue is {"name": "italiano", "texts": {"English text": "its text"}},
{name} standing for a value the page puts in.

Built in: web/static/i18n/<code>.json. Uploaded from the settings page:
DATA_DIR/i18n/<code>.json, over the built-in one of its code, so a language
is added, or one corrected, with no release. What a language has to say is
the template: every text of the built-in catalogues, with nothing yet.
"""

import json
import os
import re

#: the built-in catalogues
BUILTIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'i18n')
#: a language's code: it, pt-BR
CODE = re.compile(r'[a-z]{2,3}(-[A-Z]{2})?')
#: a value a text puts in
PLACE = re.compile(r'\{\w+\}')
#: the most an uploaded catalogue weighs
MAX_UPLOAD = 2 << 20


class I18nError(Exception):
	"""A catalogue refused: the settings page shows why."""


def folder(setup):
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'i18n')


def _read(path):
	try:
		with open(path) as handle:
			found = json.load(handle)
	except (OSError, ValueError):
		return None
	return found if isinstance(found, dict) and isinstance(found.get('texts'), dict) else None


def _codes(where):
	if not os.path.isdir(where):
		return []
	return sorted(name[:-5] for name in os.listdir(where)
				  if name.endswith('.json') and CODE.fullmatch(name[:-5]))


def template():
	"""Every text there is to say: the keys of the built-in catalogues."""
	keys = set()
	for code in _codes(BUILTIN):
		keys.update((_read(os.path.join(BUILTIN, code + '.json')) or {}).get('texts', {}))
	return sorted(keys)


def catalogue(code, setup):
	"""A language's catalogue: the built-in one with the uploaded one over it."""
	if code == 'en':
		return {'code': 'en', 'name': 'English', 'texts': {}}
	if not CODE.fullmatch(code or ''):
		raise I18nError("%r is not a language's code, e.g. it or pt-BR" % code)
	held = [c for c in (_read(os.path.join(BUILTIN, code + '.json')),
						_read(os.path.join(folder(setup), code + '.json'))) if c]
	if not held:
		raise I18nError("no language %r" % code)
	texts = {}
	for part in held:
		texts.update((k, v) for k, v in part['texts'].items() if isinstance(v, str) and v)
	return {'code': code, 'name': held[-1].get('name') or code, 'texts': texts}


def languages(setup):
	"""Every language there is, with its share of the template said."""
	keys = template()
	out = [{'code': 'en', 'name': 'English', 'share': 1.0, 'builtin': True, 'uploaded': False}]
	mine = _codes(folder(setup))
	for code in sorted(set(_codes(BUILTIN)) | set(mine)):
		held = catalogue(code, setup)
		said = sum(1 for k in keys if held['texts'].get(k))
		out.append({'code': code, 'name': held['name'], 'uploaded': code in mine,
					'builtin': os.path.exists(os.path.join(BUILTIN, code + '.json')),
					'share': round(said / float(len(keys)), 3) if keys else 0.0})
	return out


def check(code, body):
	"""An uploaded catalogue, checked: a code, known texts, the same {values} as the English."""
	if not CODE.fullmatch(code or '') or code == 'en':
		raise I18nError("a language's code, e.g. it or pt-BR: English is the pages' own")
	if not isinstance(body, dict) or not isinstance(body.get('texts'), dict):
		raise I18nError('a catalogue is {"name": "...", "texts": {"English text": "its text"}}: '
						'download the template to start one')
	known = set(template())
	unknown = [k for k in body['texts'] if k not in known]
	if unknown:
		raise I18nError("%d texts the pages do not have, e.g. %s" % (
			len(unknown), ', '.join(repr(k) for k in unknown[:5])))
	texts = {}
	for key, value in body['texts'].items():
		if value in (None, ''):
			continue
		if not isinstance(value, str):
			raise I18nError("the text of %r is not a text" % key)
		if sorted(PLACE.findall(key)) != sorted(PLACE.findall(value)):
			raise I18nError("%r puts in %s, its text %s: the same ones, or the values are lost"
							% (key, ', '.join(PLACE.findall(key)) or 'nothing',
							   ', '.join(PLACE.findall(value)) or 'nothing'))
		texts[key] = value
	return {'name': ' '.join(str(body.get('name') or code).split())[:40], 'texts': texts}


def save(code, body, setup):
	kept = check(code, body)
	where = folder(setup)
	os.makedirs(where, exist_ok=True)
	path = os.path.join(where, code + '.json')
	with open(path + '.part', 'w') as handle:
		json.dump(kept, handle, ensure_ascii=False, indent=1, sort_keys=True)
	os.replace(path + '.part', path)
	return languages(setup)


def drop(code, setup):
	if CODE.fullmatch(code or ''):
		try:
			os.remove(os.path.join(folder(setup), code + '.json'))
		except FileNotFoundError:
			pass
	return languages(setup)


def download(code, setup):
	"""A catalogue as a file to edit: the language's, or with 'template' every text empty."""
	if code == 'template':
		return {'name': '', 'texts': dict((k, '') for k in template())}
	held = catalogue(code, setup)
	return {'name': held['name'], 'texts': dict((k, held['texts'].get(k, '')) for k in template())}


def route(handler, method, path, query):
	"""api/i18n, api/i18n/<code>, <code>/download; POST <code> to upload, <code>/delete. False for others."""
	if path != '/api/i18n' and not path.startswith('/api/i18n/'):
		return False
	from parity_deriva.web import mcp
	setup = handler.service.setup
	code, _, action = path[len('/api/i18n/'):].partition('/') if path != '/api/i18n' else ('', '', '')
	try:
		if method == 'GET':
			if not code:
				return mcp.reply(handler, 200, {'languages': languages(setup), 'texts': len(template())})
			if action == 'download':
				return mcp.reply(handler, 200, json.dumps(download(code, setup), ensure_ascii=False,
														  indent=1, sort_keys=True).encode(),
								 'application/json', headers=[(
									 'Content-Disposition', 'attachment; filename="%s.json"' % code)])
			if not action:
				return mcp.reply(handler, 200, catalogue(code, setup))
			return mcp.reply(handler, 404, {'error': "no route %s" % path})
		if handler.headers.get('X-Parity-Deriva') != '1':
			return mcp.reply(handler, 403, {'error': "missing X-Parity-Deriva header"})
		if action == 'delete':
			return mcp.reply(handler, 200, {'languages': drop(code, setup)})
		if action:
			return mcp.reply(handler, 404, {'error': "no route %s" % path})
		raw = mcp.body(handler)
		if len(raw) >= MAX_UPLOAD:
			raise I18nError("a catalogue is %d MB at most" % (MAX_UPLOAD >> 20))
		try:
			body = json.loads(raw or b'null')
		except ValueError:
			raise I18nError("the file is not JSON")
		return mcp.reply(handler, 200, {'languages': save(code, body, setup)})
	except I18nError as exc:
		return mcp.reply(handler, 400, {'error': str(exc)})
