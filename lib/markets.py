"""
When the main stock exchanges open, for a strategy that trades around it.

A rule such as "the first two candles after New York opens" needs the open in
UTC, which is what every time in this project is - and the open in UTC moves.
New York opens at 09:30 on its own clock all year, which is 13:30 UTC in the
summer and 14:30 in the winter; and the United States and Europe change their
clocks on different Sundays, so for two or three weeks a year New York opens
an hour nearer to London than usual. So the open is kept here as it is known -
a local time in a zone - and turned into UTC through the zone's own rules
(zoneinfo, the standard library's), day by day, never by a fixed offset.

	from parity_deriva.lib import markets
	markets.opening(markets.NEW_YORK, datetime.date(2026, 7, 1))
	# datetime.datetime(2026, 7, 1, 13, 30)
	if markets.barAfterOpen(markets.NEW_YORK, candle.time, 'M15') in (1, 2):
		...                                  # one of the first two candles

`python -m parity_deriva.lib.markets [YYYY-MM-DD]` prints the day's opens.

The hours are the regular cash sessions, not the pre-market. A forex trader's
"New York open" is sometimes 08:00 there rather than the stock exchange's
09:30: that is a different moment, and would be an entry of its own here.
"""

import datetime
import math
import sys
from zoneinfo import ZoneInfo

from parity_deriva.lib.utils import granularityToTimedelta

NEW_YORK = 'NEW_YORK'
LONDON = 'LONDON'
FRANKFURT = 'FRANKFURT'
TOKYO = 'TOKYO'
HONG_KONG = 'HONG_KONG'
SYDNEY = 'SYDNEY'

#: each exchange's zone and the local time it opens at, Monday to Friday
EXCHANGES = {
	NEW_YORK: ('America/New_York', datetime.time(9, 30)),    # NYSE, Nasdaq
	LONDON: ('Europe/London', datetime.time(8, 0)),          # LSE
	FRANKFURT: ('Europe/Berlin', datetime.time(9, 0)),       # Xetra
	TOKYO: ('Asia/Tokyo', datetime.time(9, 0)),              # TSE
	HONG_KONG: ('Asia/Hong_Kong', datetime.time(9, 30)),     # HKEX
	SYDNEY: ('Australia/Sydney', datetime.time(10, 0)),      # ASX
}

UTC = datetime.timezone.utc


def opening(exchange, day):
	"""
	The instant `exchange` opens on `day`, a date on its own calendar: naive
	UTC, like every other time here. None on a Saturday or a Sunday.

	Sydney's and Tokyo's day starts on the evening before in UTC: Sydney's
	open of a Monday is the Sunday at 23:00 UTC in the southern summer.
	"""
	# ponytail: weekends only, no holidays - on the 4th of July this still
	# answers 13:30. The economic calendar's non-economic rows
	# (data/calendar.py) name the bank holidays, for a rule that must skip them.
	zone, at = EXCHANGES[exchange]
	if day.weekday() >= 5:
		return None
	local = datetime.datetime.combine(day, at, tzinfo=ZoneInfo(zone))
	return local.astimezone(UTC).replace(tzinfo=None)


def barAfterOpen(exchange, when, granularity):
	"""
	Which bar after the last open of `exchange` the bar opening at `when`
	(naive UTC) is: 1 for the bar the open falls inside, 2 for the next one,
	and on until the next open. `granularity` is 'M15', 'H1' and so on, or
	the bar's length as a timedelta.

	The bar the open falls inside, as portfolio/session.py counts the cut:
	half open, so an M15 bar at 13:30 is the first bar of a 13:30 open, and
	an H1 bar at 13:00 is the first of it too, since it holds the open.
	Before the day's open the count is still the previous day's: the Monday
	bars before New York opens are bars of Friday's session.
	"""
	span = granularity if isinstance(granularity, datetime.timedelta) \
		else granularityToTimedelta(granularity)
	end = when + span
	zone = ZoneInfo(EXCHANGES[exchange][0])
	# the latest open before the bar ends, on the exchange's own calendar:
	# the bar's own day, else back over a weekend
	day = (end - datetime.timedelta(microseconds=1)).replace(tzinfo=UTC).astimezone(zone).date()
	for back in range(4):
		start = opening(exchange, day - datetime.timedelta(days=back))
		if start is not None and start < end:
			return math.ceil((end - start) / span)
	return None


if __name__ == '__main__':
	on = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
	for name in EXCHANGES:
		start = opening(name, on)
		print("%-10s %s" % (name, start.strftime('%Y-%m-%d %H:%M UTC') if start else 'closed'))
