"""
A Test server and its archive - the PC and the cloud: push this server's
mixes there, so what trades is what was simulated here, and is checked again
on the archive's code and candles (verify, on its mix page); pull from there
the strategies and indicators enabled on it, to simulate them here.

    python scripts/sync.py push --to https://host/parity/mcp --token <a pc token>
    python scripts/sync.py push --to ... --mix 0123456789abcdef --dry-run
    python scripts/sync.py pull --from https://host/parity/mcp --token <a pc token>
    python scripts/sync.py pull     # PARITY_DERIVA_ARCHIVE_URL and PARITY_DERIVA_SYNC_TOKEN

A mix goes with what it needs: the uploaded strategies its sets trade and the
indicators those take (submit_strategy, submit_indicator: drafts there,
enabled by hand), its sets and the runs in it. What was sent is kept in
DATA_DIR/sync.json, and a file that has not changed since is not sent again.
A pull brings each enabled version this server does not hold as a draft here,
enabled on this server's settings page like any other. The token is a "pc"
one, made on the archive's settings page; it may also be in
PARITY_DERIVA_SYNC_TOKEN. The market data comes as this server's upstream,
never from here. Run it by hand, or from the Windows Task Scheduler.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from parity_deriva.data.sources import SourceError, rpc
from parity_deriva.etc import settings
from parity_deriva.strategy import uploaded
from parity_deriva.web.mcp import PUSH_CHUNK


def commit():
    """This checkout's commit, sent with each set: the code it was simulated on."""
    try:
        return subprocess.run(['git', '-C', os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ''


def pushFile(upstream, blob, args):
    """A gzipped file through push_sweep, a chunk a call."""
    parts = max(1, -(-len(blob) // PUSH_CHUNK))
    for part in range(parts):
        got = rpc(upstream, 'push_sweep', dict(
            args, part=part, parts=parts,
            data=base64.b64encode(blob[part * PUSH_CHUNK:(part + 1) * PUSH_CHUNK]).decode()))
    return got


def retake(source, code, there):
    """`source` taking the indicator `there` where it took `code`: the other server's version of it."""
    return re.sub(r"""(['"])%s\1""" % re.escape(code), lambda m: m.group(1) + there + m.group(1), source)


def submitted(upstream, code, dataDir, report, kind='strategies'):
    """
    The other server's code for an uploaded strategy or indicator of this
    one, submitting it if need be - a strategy after the indicators it takes,
    under the codes the other server gave them.
    """
    path = dict(uploaded.drafts(dataDir, kind), **uploaded.enabled(dataDir, kind)).get(code)
    if path is None:
        return code    # a built-in one: the same code there, through git
    family, _ = uploaded.split(code)
    with open(path) as handle:
        source = handle.read()
    if kind == 'strategies':
        for taken in uploaded.used(source):
            source = retake(source, taken, submitted(upstream, taken, dataDir, report, 'indicators'))
    try:
        there = rpc(upstream, 'submit_strategy' if kind == 'strategies' else 'submit_indicator',
                    {'name': family, 'source': source})['name']
        report("%s: submitted, a draft there as %s - enable it on its settings page" % (code, there))
    except SourceError as exc:
        same = re.search(r'the same code as (\S+ \d+)', str(exc))
        if not same:
            raise
        there = same.group(1)
    return there


def pushRuns(upstream, service, items, codes, sent, report, dry_run=False, here=''):
    """
    The sets of `items` ({sweep, n}) and those runs of theirs, the uploaded
    strategies they trade submitted first (`codes` maps this server's code
    to the other's). A file whose sha1 is in `sent` already went.
    """
    files = []
    for sweep in sorted(set(i['sweep'] for i in items)):
        path = service.sweepPath(sweep, '.json.gz')
        with gzip.open(path, 'rb') as handle:
            job = json.loads(handle.read())
        code = (job.get('fields') or {}).get('strategy')
        if code and code not in codes:
            codes[code] = code if dry_run else submitted(upstream, code, service.setup.DATA_DIR, report)
        if code and codes[code] != code:
            job['fields']['strategy'] = codes[code]
            blob = gzip.compress(json.dumps(job).encode())
        else:
            with open(path, 'rb') as handle:
                blob = handle.read()
        files.append((path, blob, {'sweep': sweep, 'commit': here}))
        for item in items:
            if item['sweep'] == sweep:
                run = service.warmFile(sweep, '%d.json.gz' % int(item['n'])) \
                    or service.sweepRunPath(sweep, item['n'])
                with open(run, 'rb') as handle:
                    files.append((run, handle.read(), {'sweep': sweep, 'n': item['n']}))
    for path, blob, what in files:
        stamp = hashlib.sha1(blob).hexdigest()
        if sent.get(path) == stamp:
            continue
        if dry_run:
            report("would push %s (%d KB)" % (os.path.basename(path), len(blob) >> 10))
            continue
        pushFile(upstream, blob, what)
        sent[path] = stamp
        report("pushed %s%s (%d KB)" % (what['sweep'], '/%s' % what['n'] if 'n' in what else '',
                                       len(blob) >> 10))


def pull(upstream, dataDir, report):
    """
    The strategies and indicators enabled on the archive, each version this
    server does not hold written here as a draft, with what the archive
    keeps beside it. One this server holds is left as it is, and said when
    its source is not the archive's; one the checks refuse is not written.
    """
    got = rpc(upstream, 'pull_code', {})
    taken = 0
    for kind in ('indicators', 'strategies'):
        other = [k for k in uploaded.KINDS if k != kind][0]
        held = dict(uploaded.drafts(dataDir, kind), **uploaded.enabled(dataDir, kind))
        for item in got.get(kind) or []:
            code, source = str(item.get('code') or ''), item.get('source')
            name, version = uploaded.split(code)
            if not version or not isinstance(source, str):
                report("%s: not a version's code, left out" % code)
                continue
            if code in held:
                with open(held[code]) as handle:
                    if handle.read() != source:
                        report("%s: this server has another %s already, left as it is" % (code, code))
                continue
            if uploaded.latest(dataDir, name, other):
                report("%s: %s is one of the %s here, left out" % (code, name, other))
                continue
            problems = uploaded.check(source)
            if problems:
                report("%s: refused by the checks - %s" % (code, '; '.join(problems)))
                continue
            where = os.path.join(uploaded.root(dataDir, kind), 'drafts')
            os.makedirs(where, exist_ok=True)
            path = os.path.join(where, uploaded.fileName(code))
            kept = dict((k, v) for k, v in (item.get('meta') or {}).items() if k not in ('family', 'version'))
            kept['pulled'] = {'from': upstream['url'], 'at': int(time.time() * 1000)}
            for target, body in ((path, source), (path[:-3] + '.json', json.dumps(kept))):
                with open(target + '.part', 'w') as handle:
                    handle.write(body)
                os.replace(target + '.part', target)
            report("%s: pulled, a draft here - enable it on the settings page" % code)
            taken += 1
    report("%d pulled" % taken)
    return taken


def main(argv=None, report=print):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['push', 'pull'])
    parser.add_argument('--to', '--from', dest='to', default=os.environ.get('PARITY_DERIVA_ARCHIVE_URL', ''),
                        help="the archive's MCP address, https://.../mcp (default: PARITY_DERIVA_ARCHIVE_URL)")
    parser.add_argument('--token', default=os.environ.get('PARITY_DERIVA_SYNC_TOKEN', ''),
                        help='a pc token of that server (default: PARITY_DERIVA_SYNC_TOKEN)')
    parser.add_argument('--mix', action='append', help='only this mix (its id); again for more')
    parser.add_argument('--dry-run', action='store_true', help='say what would go, send nothing')
    args = parser.parse_args(argv)
    if not args.token or not args.to:
        report("no %s: %s" % (('token', '--token, or PARITY_DERIVA_SYNC_TOKEN') if not args.token else
                              ('address', '--to, or PARITY_DERIVA_ARCHIVE_URL')))
        return 2
    upstream = {'url': args.to, 'token': args.token}
    if args.action == 'pull':
        try:
            pull(upstream, settings.DATA_DIR, report)
        except SourceError as exc:
            report(str(exc))
            return 1
        return 0
    from parity_deriva.web.service import Service
    service = Service(setup=settings)
    kept = os.path.join(settings.DATA_DIR, 'sync.json')
    try:
        with open(kept) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    sent = state.setdefault(args.to, {})
    here = commit()
    codes = {}
    status = 0
    for mix in service.mixes():
        if args.mix and mix['id'] not in args.mix:
            continue
        label = "mix %s" % (mix.get('name') or mix['id'])
        missing = [i for i in mix['items'] if service.sweepPayload(i['sweep'], i['n']) is None]
        if missing:
            report("%s: runs %s have no saved trades - open them on the run page, or simulate "
                   "the mix together, then push again" % (label, ', '.join(
                       '%s/%s' % (i['sweep'], i['n']) for i in missing)))
            status = 1
            continue
        pushRuns(upstream, service, mix['items'], codes, sent, report, args.dry_run, here)
        if not args.dry_run:
            rpc(upstream, 'push_mix', {'mix': {'id': mix['id'], 'name': mix.get('name') or '',
                                               'leverage': mix.get('leverage'),
                                               'items': mix['items']}})
            report("%s: pushed with %d runs - verify it on the other server's mix page"
                   % (label, len(mix['items'])))
        with open(kept + '.part', 'w') as handle:
            json.dump(state, handle)
        os.replace(kept + '.part', kept)
    return status


if __name__ == '__main__':
    sys.exit(main())
