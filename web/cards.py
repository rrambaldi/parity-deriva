"""
The card of a version (docs/PIANO-FASE1.md C2, D1): the state of a strategy's
form from the gate on, and the numbers it is judged by in demo and live.

A version is the code with its parameters frozen. The code is the strategy's
source, the modules of this tree it builds on (a base class, the indicators)
and the uploaded indicators it takes, each read as Python - its syntax tree,
docstrings left out - so that a comment or a blank line is no new version;
the parameters are the form's groupKey (web/livesessions.py), so the capital
and the window are not either. Its id is sha1(codeHash + groupKey)[:16], its
label "M1502 v3", counted by strategy, instrument and granularity.

One file a version, DATA_DIR/cards/<id>.json. move() is the one way its
state changes: along MOVES only, DEAD only by a person, and every change an
entry in the journal (web/journal.py), which is the card's history - the card
holds only where it is now.
"""

import ast
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time

from parity_deriva.web import journal, livesessions

STATES = ('SIM', 'DEMO', 'LIVE', 'SUSPENDED', 'DEAD')
#: where a state may go (docs/PROCESSO.md section 8)
MOVES = {'SIM': ('DEMO', 'DEAD'), 'DEMO': ('LIVE', 'SIM', 'DEAD'), 'LIVE': ('SUSPENDED', 'DEAD'),
		 'SUSPENDED': ('DEMO', 'SIM', 'DEAD'), 'DEAD': ()}
#: the modules a strategy's code is made of besides its own file: the base
#: classes beside it and the indicators, not the engine it runs in
PARTS = ('parity_deriva.strategy.', 'parity_deriva.lib.indicators', 'parity_deriva.lib.streaming')
CARD_ID = re.compile(r'[0-9a-f]{16}')

_lock = threading.Lock()


class CardError(Exception):
	"""A card that does not exist or a move that is not one: the page shows it."""


def folder(setup):
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'cards')


# ------------------------------------------------------------------ the code

def shape(source):
	"""The source as Python reads it: no comments, no blank lines, no docstrings."""
	tree = ast.parse(source)
	for node in ast.walk(tree):
		body = getattr(node, 'body', None)
		if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body \
				and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
				and isinstance(body[0].value.value, str):
			node.body = body[1:] or [ast.Pass()]
	return ast.dump(tree)


def moduleFile(module):
	held = sys.modules.get(module)
	if getattr(held, '__file__', None):
		return held.__file__
	try:
		spec = importlib.util.find_spec(module)
	except (ImportError, ValueError):
		return None
	return spec.origin if spec and spec.origin and spec.origin.endswith('.py') else None


def imported(tree):
	"""The modules a syntax tree imports."""
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				yield alias.name
		elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
			yield node.module


def code(strategy, setup=None):
	"""
	(codeHash, {part: source}) of a strategy the ledger runs, or None for one
	it does not (a viewer plugin): its file, the modules of PARTS it imports,
	theirs, and the uploaded indicators it takes.
	"""
	from parity_deriva.backtest import ledger
	from parity_deriva.strategy import uploaded
	if strategy not in ledger.STRATEGIES:
		return None
	start = moduleFile(ledger.STRATEGIES[strategy][0])
	if not start:
		return None
	indicators = uploaded.enabled(getattr(setup, 'DATA_DIR', '') or '.', 'indicators') if setup else {}
	sources, todo = {}, [(strategy, start)]
	while todo:
		part, name = todo.pop()
		if part in sources or not name:
			continue
		try:
			with open(name) as handle:
				text = handle.read()
			tree = ast.parse(text)
		except (OSError, SyntaxError, ValueError):
			continue
		sources[part] = text
		for module in imported(tree):
			if module.startswith(PARTS) and module != 'parity_deriva.strategy.uploaded':
				todo.append((module, moduleFile(module)))
		for used in uploaded.used(text):
			todo.append((used, indicators.get(used)))
	digest = hashlib.sha1()
	for part in sorted(sources):
		digest.update(part.encode() + b'\0' + shape(sources[part]).encode() + b'\0')
	return digest.hexdigest(), sources


def codeSeen(setup, fields):
	"""A "code changed" entry when the strategy's code is not the one its journal saw last."""
	strategy = (fields or {}).get('strategy')
	try:
		found = code(strategy, setup)
	except Exception:
		journal.logger.exception("the code of %s" % strategy)
		return None
	if not found:
		return None
	last = next((e for e in reversed(journal.read(setup, strategy)) if e['kind'] == 'code'), None)
	if last and last['data'].get('hash') == found[0]:
		return None
	return journal.record(setup, strategy, 'code', 'experiment', {'hash': found[0], 'source': found[1]},
						  fields)


# ----------------------------------------------------------------- the cards

def versionId(codeHash, fields):
	return hashlib.sha1((codeHash + livesessions.groupKey(fields)).encode()).hexdigest()[:16]


def cardPath(setup, ident):
	if not CARD_ID.fullmatch(str(ident or '')):
		raise CardError("no such card")
	return os.path.join(folder(setup), ident + '.json')


def get(setup, ident):
	try:
		with open(cardPath(setup, ident)) as handle:
			return json.load(handle)
	except (OSError, ValueError):
		raise CardError("no card %s" % ident) from None


def save(setup, card):
	name = cardPath(setup, card['id'])
	os.makedirs(folder(setup), exist_ok=True)
	with open(name + '.part', 'w') as handle:
		json.dump(card, handle)
	os.replace(name + '.part', name)
	return card


def cards(setup, strategy=None):
	"""Every card, or a strategy's - all the versions of an uploaded one - newest first."""
	out = []
	where = folder(setup)
	for name in os.listdir(where) if os.path.isdir(where) else ():
		if name.endswith('.json'):
			try:
				card = get(setup, name[:-5])
			except CardError:
				continue
			if strategy is None or journal.family(card.get('strategy') or '') == journal.family(strategy):
				out.append(card)
	return sorted(out, key=lambda c: -(c.get('made') or 0))


def make(setup, fields, source=None, by=None):
	"""
	The card of the form's version, made in SIM if there is none: the gate's
	(C3). The same code with the same parameters is the same card.
	"""
	fields = dict(fields or {})
	strategy = fields.get('strategy')
	found = code(strategy, setup)
	if not found:
		raise CardError("%s is not a strategy whose code this server reads" % strategy)
	ident = versionId(found[0], fields)
	with _lock:
		try:
			return get(setup, ident)
		except CardError:
			pass
		same = [c for c in cards(setup, strategy) if (c['fields'].get('instrument'), c['fields'].get('granularity'))
				== (fields.get('instrument'), fields.get('granularity'))]
		card = {'id': ident, 'label': '%s v%d' % (strategy, len(same) + 1), 'strategy': strategy,
				'codeHash': found[0], 'fields': fields, 'state': 'SIM', 'made': journal.now(),
				'source': source, 'reference': None, 'holdout': None}
		save(setup, card)
	journal.record(setup, strategy, 'version', 'milestone', {'label': card['label'], 'state': 'SIM'},
				   fields, version=ident, link={'kind': 'card', 'id': ident}, by=by)
	return card


def forFields(setup, fields):
	"""The card of the form as the code is now, or None."""
	found = code((fields or {}).get('strategy'), setup)
	if not found:
		return None
	try:
		return get(setup, versionId(found[0], fields))
	except CardError:
		return None


def move(setup, ident, to, why, by):
	"""
	A card to another state, along MOVES: DEAD only when a person asks
	(`by` not parity-deriva), never a check. Written in the journal.
	"""
	if to not in STATES:
		raise CardError("a state is %s" % ', '.join(STATES))
	with _lock:
		card = get(setup, ident)
		if to not in MOVES[card['state']]:
			raise CardError("%s is %s: it goes to %s, not to %s" % (
				card['label'], card['state'], ' or '.join(MOVES[card['state']]) or 'nowhere', to))
		if to == 'DEAD' and (not by or by == journal.MACHINE):
			raise CardError("only a person discards a version: DEAD is the user's call")
		was, card['state'], card['moved'] = card['state'], to, journal.now()
		save(setup, card)
	journal.record(setup, card['strategy'], 'state', 'milestone',
				   {'label': card['label'], 'from': was, 'to': to, 'why': why}, card['fields'],
				   version=ident, link={'kind': 'card', 'id': ident}, by=by)
	return card


def receive(setup, card):
	"""A card that came with a push (servers.push, livesessions.promote): this server's copy of it."""
	if not isinstance(card, dict) or not CARD_ID.fullmatch(str(card.get('id') or '')) \
			or card.get('state') not in STATES:
		raise CardError("a card is {id, label, state, fields, ...}, as cards.make writes it")
	with _lock:
		try:
			mine = get(setup, card['id'])
		except CardError:
			mine = None
		kept = dict(card, received=int(time.time() * 1000))
		if mine:
			kept['state'] = mine['state']
		return save(setup, kept)
