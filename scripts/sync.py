"""
Push this server's mixes to another one - the PC's to the cloud - so what
trades there is what was simulated here, and is checked again on its code
and candles (verify, on the cloud's mix page).

    python scripts/sync.py push --to https://host/parity/mcp --token <a pc token>
    python scripts/sync.py push --to ... --mix 0123456789abcdef --dry-run

A mix goes with what it needs: the uploaded strategies its sets trade
(submit_strategy: a draft there, enabled by hand), its sets and the runs in
it. What was sent is kept in DATA_DIR/sync.json, and a file that has not
changed since is not sent again. The token is a "pc" one, made on the other
server's settings page; it may also be in PARITY_DERIVA_SYNC_TOKEN. The
market data comes the other way, as that server's upstream: never from here.
Run it by hand, or from the Windows Task Scheduler.
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


def submitted(upstream, code, dataDir, report):
    """The other server's code for an uploaded strategy of this one, submitting it if need be."""
    path = dict(uploaded.drafts(dataDir), **uploaded.enabled(dataDir)).get(code)
    if path is None:
        return code    # a built-in one: the same code there, through git
    family, _ = uploaded.split(code)
    with open(path) as handle:
        source = handle.read()
    try:
        there = rpc(upstream, 'submit_strategy', {'name': family, 'source': source})['name']
        report("%s: submitted, a draft there as %s - enable it on its settings page" % (code, there))
    except SourceError as exc:
        same = re.search(r'the same code as (\S+ \d+)', str(exc))
        if not same:
            raise
        there = same.group(1)
    return there


def main(argv=None, report=print):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['push'])
    parser.add_argument('--to', required=True, help="the other server's MCP address, https://.../mcp")
    parser.add_argument('--token', default=os.environ.get('PARITY_DERIVA_SYNC_TOKEN', ''),
                        help='a pc token of that server (default: PARITY_DERIVA_SYNC_TOKEN)')
    parser.add_argument('--mix', action='append', help='only this mix (its id); again for more')
    parser.add_argument('--dry-run', action='store_true', help='say what would go, send nothing')
    args = parser.parse_args(argv)
    if not args.token:
        report("no token: --token, or PARITY_DERIVA_SYNC_TOKEN")
        return 2
    from parity_deriva.web.service import Service
    service = Service(setup=settings)
    upstream = {'url': args.to, 'token': args.token}
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
        files = []
        for sweep in sorted(set(i['sweep'] for i in mix['items'])):
            path = service.sweepPath(sweep, '.json.gz')
            with gzip.open(path, 'rb') as handle:
                job = json.loads(handle.read())
            code = (job.get('fields') or {}).get('strategy')
            if code and code not in codes:
                codes[code] = code if args.dry_run else submitted(upstream, code, settings.DATA_DIR, report)
            if code and codes[code] != code:
                job['fields']['strategy'] = codes[code]
                blob = gzip.compress(json.dumps(job).encode())
            else:
                with open(path, 'rb') as handle:
                    blob = handle.read()
            files.append((path, blob, {'sweep': sweep, 'commit': here}))
            for item in mix['items']:
                if item['sweep'] == sweep:
                    run = service.sweepRunPath(sweep, item['n'])
                    with open(run, 'rb') as handle:
                        files.append((run, handle.read(), {'sweep': sweep, 'n': item['n']}))
        for path, blob, what in files:
            stamp = hashlib.sha1(blob).hexdigest()
            if sent.get(path) == stamp:
                continue
            if args.dry_run:
                report("would push %s (%d KB)" % (os.path.basename(path), len(blob) >> 10))
                continue
            pushFile(upstream, blob, what)
            sent[path] = stamp
            report("pushed %s%s (%d KB)" % (what['sweep'], '/%s' % what['n'] if 'n' in what else '',
                                           len(blob) >> 10))
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
