"""
The sets simulated a while ago, their runs into the bucket (web/service.py
freeze): the rule the button on the simulate page is the hand of, for cron.

    python scripts/cold.py --older-than 30
    python scripts/cold.py --older-than 30 --dry-run

A set goes once it was saved more than that many days ago, unless it is in a
mix or one of its runs is starred: those are the ones opened again. Its table
stays here - the lists, the mixes and the favourites read it - and its runs'
files go, each coming back by itself the first time it is opened. The bucket
is PARITY_DERIVA_S3_* in parity_deriva/.env.
"""

import argparse
import os
import sys
import time

from parity_deriva.etc import settings


def main(argv=None, report=print):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--older-than', type=float, required=True, metavar='DAYS',
                        help='send the sets saved more than this many days ago')
    parser.add_argument('--dry-run', action='store_true', help='say what would go, send nothing')
    args = parser.parse_args(argv)
    from parity_deriva.web.service import Service, ServiceError
    service = Service(setup=settings)
    try:
        service.bucket()
    except ServiceError as exc:
        report(str(exc))
        return 2
    used = set(i['sweep'] for m in service.mixes() for i in m.get('items') or []) | set(
        f['source']['id'] for f in service.favourites() if (f.get('source') or {}).get('kind') == 'sweep')
    before = (time.time() - args.older_than * 86400) * 1000
    freed, status = 0, 0
    for row in service.sweeps():
        folder = service.sweepPath(row['id'], '')
        if row.get('saved', 0) > before or row['id'] in used or not os.path.isdir(folder) \
                or not os.listdir(folder):
            continue
        if args.dry_run:
            report("would send set %s %s (%.1f MB)" % (row['id'], row.get('name') or '', sum(
                os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder)) / 1e6))
            continue
        try:
            told = service.freeze(row['id'])
        except ServiceError as exc:
            report("set %s: %s" % (row['id'], exc))
            status = 1
            continue
        freed += told['freed']
        report("set %s %s: %d files in the bucket, %.1f MB freed" % (
            row['id'], row.get('name') or '', told['files'], told['freed'] / 1e6))
    if not args.dry_run:
        report("%.1f MB freed" % (freed / 1e6))
    return status


if __name__ == '__main__':
    sys.exit(main())
