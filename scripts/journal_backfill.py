"""
The journals a server would have kept had it had them from the start (web/journal.py):
its sets, saved runs, favourites, mixes and live sessions, each at its own date.

    python scripts/journal_backfill.py
    python scripts/journal_backfill.py --dry-run

Once, on a server that simulated and traded before the journals. An entry's
id comes from what it is made from (sha1 of its kind and its id), so running it
again adds only what is new; the entries written since the journals are left
as they are.
"""

import argparse
import hashlib

from parity_deriva.etc import settings


def ident(*parts):
	return hashlib.sha1('\0'.join(str(p) for p in parts).encode()).hexdigest()


def found(service):
	"""(strategy, kind, level, data, fields, link, at, id) of everything on disk, oldest first."""
	out = []
	for meta in service.sweeps():
		out.append((meta.get('strategy'), 'sweep', 'experiment',
					{'id': meta['id'], 'name': meta.get('name'), 'runs': meta.get('runs'), 'total': meta.get('total'),
					 'stopped': meta.get('stopped'), 'varied': meta.get('varied'), 'bestScore': meta.get('bestScore'),
					 'bestParams': meta.get('bestParams')},
					meta, {'kind': 'sweep', 'id': meta['id']}, meta.get('saved'), ident('sweep', meta['id'])))
	for meta in service.runs():
		fields = meta.get('fields') or {}
		out.append((fields.get('strategy'), 'run', 'experiment',
					{'trades': meta.get('trades'), 'from': meta.get('from'), 'to': meta.get('to')},
					fields, {'kind': 'run', 'fields': fields}, meta.get('saved'), ident('run', meta['id'])))
	for row in service.favourites():
		summary = row.get('summary') or {}
		out.append((row['fields'].get('strategy'), 'favourite', 'experiment',
					{'trades': summary.get('trades'), 'net': summary.get('net'), 'pf': summary.get('profitFactor'),
					 'note': row.get('note')},
					row['fields'], service.favouriteLink(row), row.get('added'), ident('favourite', row['id'])))
	for mix in service.mixes():
		for (strategy, instrument, granularity), fields in service.mixForms(mix).items():
			out.append((strategy, 'mix', 'milestone',
						{'id': mix['id'], 'name': mix.get('name'), 'runs': len(mix.get('items') or [])},
						fields, {'kind': 'mix', 'id': mix['id']}, mix.get('saved'),
						ident('mix', mix['id'], strategy, instrument, granularity)))
	for session in service.live.ids():
		try:
			meta = service.live.meta(session)
		except Exception:
			continue
		fields = meta.get('fields') or {}
		data = {'id': session, 'provider': meta.get('provider'), 'account': meta.get('account'),
				'demo': meta.get('demo'), 'capital': fields.get('capital')}
		link = {'kind': 'session', 'id': session}
		out.append((fields.get('strategy'), 'session-start', 'milestone', data, fields, link,
					meta.get('started'), ident('session-start', session)))
		if meta.get('stopped'):
			out.append((fields.get('strategy'), 'session-stop', 'milestone', data, fields, link,
						meta['stopped'], ident('session-stop', session)))
	return sorted((row for row in out if row[0] and row[6]), key=lambda row: row[6])


def main(argv=None, report=print, setup=None):
	parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
									 formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument('--dry-run', action='store_true', help='say what would be written, write nothing')
	args = parser.parse_args(argv)
	from parity_deriva.web import journal
	from parity_deriva.web.service import Service
	setup = setup or settings
	service = Service(setup=setup)
	held = {}
	added = 0
	for strategy, kind, level, data, fields, link, at, key in found(service):
		book = journal.family(strategy)
		if book not in held:
			held[book] = set(e['id'] for e in journal.read(setup, strategy))
		if key in held[book]:
			continue
		held[book].add(key)
		added += 1
		if not args.dry_run:
			journal.add(setup, strategy, kind, level, data, fields, link=link, at=at, ident=key)
	report("%s %d entries in %d journals" % ('would write' if args.dry_run else 'wrote', added,
											 len([s for s in held if held[s]])))
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
