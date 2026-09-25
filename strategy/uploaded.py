"""
Strategies written somewhere else - by an assistant over MCP (web/mcp.py) -
and kept under DATA_DIR rather than in this tree.

Two states, and the directory a file sits in is the state:

	DATA_DIR/strategies/drafts/NAME@N.py   a draft. Backtested only in a child
		process with a ceiling on memory and time (web/sandbox.py); never
		imported by the service, never offered to a live session.
	DATA_DIR/strategies/NAME@N.py          enabled: somebody read it and pressed
		enable on the settings page. From then on it is a strategy like the
		ones beside this file - the page, the sweeps and scripts/live.py
		import it.

N is its version, and its code - the name the page, the runs and the ledger
know it by - is "NAME N". A version is never replaced: submitting NAME again
is N + 1, and the one somebody read and ran stays as it was. Each file ends
with the line stamp() writes, which says which version it is and the server
it was written on, so a copy of it carries both wherever it goes (the public
repository, web/mcp.py pullRequest).

check() is a seatbelt against mistakes, not a sandbox against malice. Whoever
runs this runs it on their own machine, with an assistant of their own
writing the code, so the danger is a strategy that is wrong - one that reads
a file, loops for ever or eats the memory - rather than one written to escape.
The imports are a short list of what a strategy needs and the calls that
reach outside Python are refused; the ceiling in the child process is what
stops the loop and the memory.
"""

import ast
import importlib.util
import logging
import os
import re
import sys

from parity_deriva.trading.handler import ExecutionHandler

#: upper case, digits and dashes, like the names beside this file. No
#: underscore, so turning the dashes into one for the module name is one to one
NAME = re.compile(r'[A-Z][A-Z0-9-]{1,39}')

#: a version's file: NAME@N.py. Its code has a space for the @
FILE = re.compile(r'(%s)@([1-9][0-9]{0,5})\.py' % NAME.pattern)

#: the module-level constant stamp() writes, and the comment over it
STAMP = 'PARITY_DERIVA'
STAMP_NOTE = '# written by parity-deriva on submit, rewritten on every one: leave it as it is'

#: the largest source accepted; the longest strategy here is 13 kB
MAX_SOURCE = 200 * 1024

#: what a strategy may import: the numbers, and the parts of this project a
#: strategy is made of. Not the brokers in lib/, not etc/settings.py
ALLOWED = frozenset((
	'math', 'statistics', 'collections', 'itertools', 'functools', 'operator',
	'datetime', 'bisect', 'heapq', 'copy', 'dataclasses', 'typing', 'enum',
	'decimal', 'fractions', 'numbers', 'numpy',
	'parity_deriva.lib.streaming', 'parity_deriva.lib.indicators',
	'parity_deriva.lib.utils', 'parity_deriva.event.event',
	'parity_deriva.trading.handler',
))

#: this directory's modules that are not strategies to build on
NOT_BASES = frozenset(('plugins', 'uploaded', 'private', 'strategy'))

#: the builtins that reach outside the strategy's own numbers, or around the
#: attribute check below by spelling a name as a string
FORBIDDEN = frozenset((
	'eval', 'exec', 'compile', 'open', '__import__', 'input', 'breakpoint',
	'globals', 'locals', 'vars', 'getattr', 'setattr', 'delattr', 'memoryview',
	'exit', 'quit',
))

#: the double-underscore names a strategy has a use for
DUNDERS = frozenset(('__init__', '__name__', '__class__'))

HERE = os.path.dirname(os.path.abspath(__file__))


class UploadError(Exception):
	"""A strategy that cannot be kept, enabled or loaded as asked."""


def root(dataDir):
	return os.path.join(dataDir, 'strategies')


def drafts(dataDir):
	"""name -> path of every draft."""
	return _listed(os.path.join(root(dataDir), 'drafts'))


def enabled(dataDir):
	"""name -> path of every enabled strategy."""
	return _listed(root(dataDir))


def code(name, version):
	"""The name a version is known by: "MY-EMA 3"."""
	return '%s %d' % (name, version)


def split(code):
	"""(name, version) of a code, or (code, None) for one without a version."""
	name, _, version = code.rpartition(' ')
	return (name, int(version)) if NAME.fullmatch(name) and version.isdigit() else (code, None)


def fileName(code):
	name, version = split(code)
	return '%s@%d.py' % (name, version)


def _listed(where):
	"""code -> path of every version in `where`, oldest version first."""
	try:
		names = os.listdir(where)
	except OSError:
		return {}
	found = [(m.group(1), int(m.group(2)), name) for m, name in
			 ((FILE.fullmatch(name), name) for name in names) if m]
	# a file from before versions is version 1, renamed the first time it is seen
	for name in names:
		if name.endswith('.py') and NAME.fullmatch(name[:-3]) \
				and not any(n == name[:-3] for n, _, _ in found):
			for a, b in ((name, name[:-3] + '@1.py'), (name[:-3] + '.json', name[:-3] + '@1.json')):
				if os.path.exists(os.path.join(where, a)):
					os.replace(os.path.join(where, a), os.path.join(where, b))
			found.append((name[:-3], 1, name[:-3] + '@1.py'))
	return dict((code(n, v), os.path.join(where, f)) for n, v, f in sorted(found))


def latest(dataDir, name):
	"""The code of a name's newest version, draft or enabled, or None."""
	versions = [split(c) for c in list(drafts(dataDir)) + list(enabled(dataDir))]
	mine = [v for n, v in versions if n == name]
	return code(name, max(mine)) if mine else None


def nextVersion(dataDir, name):
	found = latest(dataDir, name)
	return split(found)[1] + 1 if found else 1


def stamp(source, name, version, server):
	"""
	`source` with the line that says which version it is and the server it
	was written on at its end, in place of any it had: an assistant that
	edits the source it read back sends the old one along.
	"""
	try:
		tree = ast.parse(source)
	except SyntaxError:
		tree = None
	lines = source.splitlines()
	drop = set()
	for node in (tree.body if tree else ()):
		if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == STAMP
												for t in node.targets):
			drop.update(range(node.lineno - 1, node.end_lineno))
	kept = [l for i, l in enumerate(lines) if i not in drop and l.strip() != STAMP_NOTE]
	while kept and not kept[-1].strip():
		kept.pop()
	return '\n'.join(kept + ['', '', STAMP_NOTE, '%s = %r' % (
		STAMP, {'name': name, 'version': version, 'server': server})]) + '\n'


def allowedModule(module):
	"""May a strategy import `module`, and so may an assistant read it?"""
	if module in ALLOWED:
		return True
	prefix = 'parity_deriva.strategy.'
	if module.startswith(prefix):
		name = module[len(prefix):]
		return '.' not in name and name not in NOT_BASES \
			and os.path.isfile(os.path.join(HERE, name + '.py'))
	return False


def check(source):
	"""The problems with a strategy's source, one line each; [] is none."""
	if len(source.encode('utf-8')) > MAX_SOURCE:
		return ["the source is over %d kB" % (MAX_SOURCE // 1024)]
	try:
		tree = ast.parse(source)
	except SyntaxError as exc:
		return ["line %s: %s" % (exc.lineno, exc.msg)]
	problems = []
	for node in ast.walk(tree):
		line = getattr(node, 'lineno', '?')
		if isinstance(node, ast.Import):
			modules = [alias.name for alias in node.names]
		elif isinstance(node, ast.ImportFrom):
			modules = ['.' * node.level + (node.module or '')]
		else:
			modules = ()
		for module in modules:
			if not allowedModule(module):
				problems.append("line %s: import of %s; allowed are %s and "
								"parity_deriva.strategy.<a strategy module>"
								% (line, module, ', '.join(sorted(ALLOWED))))
		if isinstance(node, ast.Name) and (node.id in FORBIDDEN or (
				node.id.startswith('__') and node.id not in DUNDERS)):
			problems.append("line %s: %s is not allowed in a strategy" % (line, node.id))
		if isinstance(node, ast.Attribute) and node.attr.startswith('__') \
				and node.attr not in DUNDERS:
			problems.append("line %s: .%s is not allowed in a strategy" % (line, node.attr))
		if isinstance(node, (ast.Global, ast.Nonlocal)):
			problems.append("line %s: global state is not allowed in a strategy" % line)
	return problems


def moduleName(name):
	# under parity_deriva.strategy, which is what web/service.handlerFields
	# reads a strategy's parameters from. A code's space is _v: a name has no
	# lower case, so it stays one to one
	return 'parity_deriva.strategy.uploaded_' + name.replace('-', '_').replace(' ', '_v')


def load(name, path):
	"""
	Import the strategy in `path` as `name`: (module, class), the entry
	backtest/ledger.STRATEGIES keeps. Its TAG becomes its name, so its
	orders cannot pass for another strategy's; its VERSION and SERVER_VERSION
	are its stamp's (None where it has none).
	"""
	module = moduleName(name)
	spec = importlib.util.spec_from_file_location(module, path)
	loaded = importlib.util.module_from_spec(spec)
	sys.modules[module] = loaded
	try:
		spec.loader.exec_module(loaded)
		found = [value for value in vars(loaded).values()
				 if isinstance(value, type) and value.__module__ == module
				 and issubclass(value, ExecutionHandler)]
		if len(found) != 1:
			raise UploadError("%s defines %d strategy classes; it needs exactly one "
							  "subclass of H4 (or another ExecutionHandler)"
							  % (name, len(found)))
	except BaseException:
		sys.modules.pop(module, None)
		raise
	stamped = vars(loaded).get(STAMP)
	stamped = stamped if isinstance(stamped, dict) else {}
	found[0].TAG = name
	found[0].VERSION = stamped.get('version') or split(name)[1]
	found[0].SERVER_VERSION = stamped.get('server')
	return module, found[0].__name__


def backtest(dataDir):
	"""name -> (module, class) of every enabled strategy that imports."""
	out = {}
	for name, path in enabled(dataDir).items():
		try:
			out[name] = load(name, path)
		except (Exception, SystemExit) as exc:
			# enabled and broken: said, and left out, rather than taking the
			# service or the live session down with it
			logging.getLogger('parity_deriva.trading.trading').warning(
				"uploaded strategy %s does not load: %s", name, exc)
	return out


def live(dataDir):
	"""The same, in scripts/live.py's shape: a handler on bid/ask candles."""
	return dict((name, entry + (('bid_ask_candles',), 'pairs'))
				for name, entry in backtest(dataDir).items())
