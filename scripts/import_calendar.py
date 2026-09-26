"""
Import the ForexFactory economic calendar into the file data/calendar.py reads.

    python scripts/import_calendar.py                     # this week
    python scripts/import_calendar.py --from 2015-01-01   # to today
    python scripts/import_calendar.py --from 2015-01-01 --to 2016-01-01

One request a week of history, parsed out of the page's own state:
forexfactory.com/calendar?week=jan5.2015 carries the week as JSON in a
<script> - `window.calendarComponentStates[1] = {days: [...]}` - so nothing
here reads the HTML table, and a layout change does not silently produce an
empty week. Each event brings `dateline`, which is epoch seconds and so is
the same instant whatever timezone the site decided to print it in; the
labels beside it are not read, precisely because they are in that timezone.

Politeness, and it is not decoration: it is one request a week, in order,
with a pause between them, and a week already in the file is skipped. Eleven
years is six hundred requests; without --force, running it twice makes none.

Impact is kept as ForexFactory's own word - high, medium, low, non-economic -
because it is their judgement about the event and relabelling it as a number
of ours would hide whose judgement a rule is acting on.

The result is a CSV next to the stores, merged rather than overwritten, so an
interrupted import is a shorter import and not a lost one.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

from parity_deriva.data import calendar, market
from parity_deriva.etc import settings

URL = 'https://www.forexfactory.com/calendar'

#: the same calendar as a feed, published for the week in progress. It is the
#: way to keep the file current - one request, no page to parse, and the host
#: that serves it does not mind being asked - but it is only ever this week,
#: so the history still comes from the pages above.
FEED = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'

#: the two readers spell the same judgement differently: the page says
#: 'non-economic' where the feed says 'Holiday'. Mapped here rather than at
#: the point of use, so a rule that asks for high impact gets the same rows
#: whichever reader imported them.
IMPACTS = {'high': calendar.HIGH, 'medium': calendar.MEDIUM,
           'low': calendar.LOW, 'non-economic': calendar.NON_ECONOMIC,
           'holiday': calendar.NON_ECONOMIC}

#: a browser's, because the site answers a bare one with a challenge page.
#: Not a disguise - the request is one a person could make by clicking - but
#: the site has to be willing to answer it for the import to exist at all.
AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

#: fetched with curl and not with requests, which is worth a line because it
#: looks like a preference and is not. The site sits behind a front end that
#: refuses python's TLS handshake outright: curl gets 200 and requests gets
#: 403 for the same URL, the same agent and the same headers. The choices are
#: a library that imitates a browser's handshake - a dependency of its own,
#: for a project whose dependency list is pandas, numpy and requests - or the
#: curl that is already on every machine this runs on. This is the second.
CURL = 'curl'

#: seconds between requests. Six hundred weeks at two seconds is twenty
#: minutes, which is a morning's import done once; faster than this is a
#: scrape somebody will be asked to stop.
DELAY = 2.0

TIMEOUT = 30

#: consecutive refusals before this stops asking. The site is behind a front
#: end that starts refusing an address that asks too often, and an importer
#: that kept knocking for another four hundred weeks would be both useless
#: and rude. Stopping leaves a file that is short rather than wrong, and the
#: next run picks up where this one gave up.
GIVE_UP = 10

WEEK = datetime.timedelta(days=7)

STATE = 'window.calendarComponentStates['


def week_name(day):
    """The week parameter as the site writes it: mmmD.YYYY, lower case."""
    return '%s%d.%d' % (day.strftime('%b').lower(), day.day, day.year)


def days_of(page):
    """
    The days array out of the page's own state.

    Found by balancing brackets rather than by a regular expression: the JSON
    holds event names with brackets in them, and a pattern that stopped at the
    first ] would import Monday and call it a week.
    """
    at = page.find(STATE)
    if at < 0:
        raise ValueError("no calendar state in the page")
    start = page.find('days: [', at)
    if start < 0:
        raise ValueError("no days in the calendar state")
    start += len('days: ')
    depth = 0
    for i in range(start, len(page)):
        if page[i] == '[':
            depth += 1
        elif page[i] == ']':
            depth -= 1
            if depth == 0:
                return json.loads(page[start:i + 1])
    raise ValueError("the days array never closes")


def rows_of(days):
    """(time, currency, impact, title) per event, in UTC."""
    out = []
    for day in days:
        for event in day.get('events', []):
            stamp = event.get('dateline')
            currency = (event.get('currency') or '').strip()
            if not stamp or not currency:
                # an event with no time is one the site lists as "tentative",
                # and an event with no currency is a site-wide note. Neither
                # can be the middle of a window, so neither is imported
                continue
            when = datetime.datetime.fromtimestamp(int(stamp),
                                                   datetime.timezone.utc)
            when = when.replace(tzinfo=None)
            impact = (event.get('impactName')
                      or event.get('impactTitle') or '').strip().lower()
            out.append((when.strftime('%Y-%m-%d %H:%M:%S'), currency,
                        IMPACTS.get(impact, impact or 'unknown'),
                        (event.get('name') or '').strip()))
    return out


def rows_of_feed(events):
    """
    The same four columns out of the weekly JSON feed.

    Its dates carry an offset - '2026-09-20T19:00:00-04:00' - so they are read
    as the instants they are and written as UTC, like everything else here.
    """
    out = []
    for event in events:
        when = (event.get('date') or '').strip()
        currency = (event.get('country') or '').strip()
        if not when or not currency:
            continue
        stamp = datetime.datetime.fromisoformat(when)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        impact = (event.get('impact') or '').strip().lower()
        out.append((stamp.strftime('%Y-%m-%d %H:%M:%S'), currency,
                    IMPACTS.get(impact, impact or 'unknown'),
                    (event.get('title') or '').strip()))
    return out


def read_feed(url=FEED, agent=AGENT, timeout=TIMEOUT, curl=CURL):
    done = subprocess.run(
        [curl, '-sS', '--compressed', '-A', agent, '--max-time', str(timeout), url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if done.returncode != 0:
        raise IOError("curl failed: %s" % done.stderr.decode('utf-8', 'replace').strip())
    return rows_of_feed(json.loads(done.stdout.decode('utf-8', 'replace')))


def read_week(day, agent=AGENT, timeout=TIMEOUT, curl=CURL):
    """One week's page, as text. Raises on anything but a 200."""
    url = '%s?week=%s' % (URL, week_name(day))
    done = subprocess.run(
        [curl, '-sS', '--compressed', '-A', agent, '--max-time', str(timeout),
         '-w', '\n%{http_code}', url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if done.returncode != 0:
        raise IOError("curl failed: %s" % done.stderr.decode('utf-8', 'replace').strip())
    page = done.stdout.decode('utf-8', 'replace')
    page, _, status = page.rpartition('\n')
    if status.strip() != '200':
        raise IOError("%s answered %s" % (url, status.strip()))
    return page


def fetch(day, reader=None):
    reader = reader if reader is not None else read_week
    return rows_of(days_of(reader(day)))


def weeks(dtfrom, dtto):
    day = dtfrom
    while day <= dtto:
        yield day
        day = day + WEEK


def covered(frame, day):
    """Is this week already in the file?"""
    if not len(frame):
        return False
    inside = frame[(frame['time'] >= day) & (frame['time'] < day + WEEK)]
    return bool(len(inside))


def main(argv=None, report=print, progress=None, reader=None):
    """
    report(line) is told what happened to each week; progress(done, total,
    text), if given, how far along it is, in weeks. `reader` is there for
    tests, which have a page of their own and no business on the network.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    today = datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    parser.add_argument('--from', dest='dtfrom', default=None,
                        help='first week to import (YYYY-MM-DD, default: this week)')
    parser.add_argument('--to', dest='dtto', default=None,
                        help='last week to import (default: today)')
    parser.add_argument('--data-dir', default=market.directory(),
                        help='where calendar.csv goes (default: %(default)s)')
    parser.add_argument('--out', default=None,
                        help='the file itself, instead of <data-dir>/calendar.csv')
    parser.add_argument('--delay', type=float, default=DELAY,
                        help='seconds between requests (default: %(default)s)')
    parser.add_argument('--feed', action='store_true',
                        help='take the week in progress from the JSON feed '
                             'instead of walking the pages; this is what a '
                             'weekly cron runs')
    parser.add_argument('--give-up', type=int, default=GIVE_UP,
                        help='stop after this many weeks in a row are '
                             'refused (default: %(default)s)')
    parser.add_argument('--force', action='store_true',
                        help='fetch weeks the file already holds')
    parser.add_argument('--dry-run', action='store_true',
                        help='fetch and report, write nothing')
    args = parser.parse_args(argv)

    dtfrom = _day(args.dtfrom) if args.dtfrom else today
    dtto = _day(args.dtto) if args.dtto else today
    if dtto < dtfrom:
        report("the last week is before the first one")
        return 2

    where = args.out or os.path.join(args.data_dir, calendar.FILENAME)
    frame = calendar.load(where)
    before = len(frame)
    wanted = list(weeks(dtfrom, dtto))

    if args.feed:
        rows = read_feed() if reader is None else reader(None)
        frame = calendar.merge(frame, rows)
        if not args.dry_run:
            calendar.save(frame, where)
        report("feed: %d events, %d added, %d in %s"
               % (len(rows), len(frame) - before, len(frame), where))
        return 0

    read = 0
    refused = 0
    for i, day in enumerate(wanted):
        if progress is not None:
            progress(i, len(wanted), week_name(day))
        if not args.force and covered(frame, day):
            continue
        try:
            rows = fetch(day, reader)
        except Exception as exc:
            refused += 1
            report("%s: %s: %s" % (week_name(day), type(exc).__name__, exc))
            if args.give_up and refused >= args.give_up:
                report("%d weeks refused in a row; stopping. The file keeps "
                       "what was read, and another run carries on from here."
                       % refused)
                break
            if args.delay:
                time.sleep(args.delay)
            continue
        refused = 0
        frame = calendar.merge(frame, rows)
        read += 1
        report("%s  %d events, %d in the file" % (week_name(day), len(rows),
                                                  len(frame)))
        if not args.dry_run:
            # written as it goes: an import interrupted after four hundred
            # weeks is a shorter import next time and not a lost one
            calendar.save(frame, where)
        if args.delay and i + 1 < len(wanted):
            time.sleep(args.delay)

    if progress is not None:
        progress(len(wanted), len(wanted), 'done')
    report("%d weeks fetched, %d events added, %d in %s"
           % (read, len(frame) - before, len(frame), where))
    return 0


def _day(text):
    return datetime.datetime.strptime(text, '%Y-%m-%d')


if __name__ == '__main__':
    sys.exit(main())
