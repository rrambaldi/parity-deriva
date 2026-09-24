"""
The curves a chart draws with the candles: SMA, EMA, Bollinger bands, ATR.

Two rules, and everything here follows from them.

**Nothing reads a bar later than the one it reports on.** The value at t is a
function of 0..t, so a series computed once over the whole run agrees at every
point with one computed over the run cut at t. A chart that broke this would
show a line the strategy could not have seen, which is worse than showing no
line at all.

**A curve is warm or it is absent.** Until there are enough bars the value is
None, and the page leaves a gap rather than drawing a mean of three bars
labelled SMA(100). That gap is information: it is the stretch of the backtest
the indicator had nothing to say about.

The EMA is seeded with the simple average of its first `period` closes rather
than started from the first close. That is the textbook seeding, and more to
the point it is what the strategies here do, so the line on the chart is the
line the strategy read. Starting from the first close instead leaves the two
disagreeing for the first few dozen bars - which is exactly where a backtest's
early trades are.

Not every curve belongs on the price axis. An ATR of 0.004 next to a price of
1.2 does not draw as a line near the bottom of the chart - it drags the scale
down to zero and flattens every candle into a hair. So a curve says which axis
it is on (`panel`), and the page draws the ones that are not on the price axis
in a strip of their own, under the chart and on the same bars.

Which curves a strategy wants is declared by the strategy, not here and not by
the page: `INDICATORS` on the class, or a plugin's 'indicators'. A period
typed into the viewer is a period that drifts from the one being traded.
"""


def sma(values, period):
    """Simple moving average, None until there are `period` values."""
    if period < 1:
        raise ValueError("sma period must be at least 1, got %r" % (period,))
    out = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values, period):
    """
    Exponential moving average, seeded with the simple average of its first
    `period` closes and None before that. See the module docstring for why the
    seeding is not an implementation detail.
    """
    if period < 1:
        raise ValueError("ema period must be at least 1, got %r" % (period,))
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / float(period)
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def true_range(highs, lows, closes):
    """
    max(high - low, |high - prev close|, |low - prev close|), per bar.

    The first bar has no previous close, so its true range is its own range.
    That is Wilder's own handling, and it matters only for the first bar of a
    series - which the warm-up has already excluded from anything.
    """
    out = []
    for i in range(len(highs)):
        if i == 0:
            out.append(highs[i] - lows[i])
            continue
        previous = closes[i - 1]
        out.append(max(highs[i] - lows[i],
                       abs(highs[i] - previous),
                       abs(lows[i] - previous)))
    return out


def atr(highs, lows, closes, period):
    """
    Wilder's ATR, which is what "ATR(14)" means on a chart.

    Wilder's smoothing and not a plain mean of the true ranges: the two differ
    by enough to move a 1.5 x ATR threshold across a pivot, and this is the one
    every broker platform draws. It is also the one ftw_ab/indicators.py
    computes, and that is the point of the choice rather than a coincidence -
    the line on the chart is the line the rule read, or it is decoration.

    Seeded with the mean of the first `period` true ranges and None before
    that, so the recursion starts where the data does.
    """
    if period < 1:
        raise ValueError("atr period must be at least 1, got %r" % (period,))
    ranges = true_range(highs, lows, closes)
    out = [None] * len(ranges)
    if len(ranges) < period:
        return out
    current = sum(ranges[:period]) / float(period)
    out[period - 1] = current
    for i in range(period, len(ranges)):
        # the recursion ewm(alpha=1/period, adjust=False) runs, written out
        current += (ranges[i] - current) / period
        out[i] = current
    return out


#: the slope measure's own periods. Not typed into the page and not into the
#: service: the same three numbers as ftw_ab/config.py's SMA_SLOW,
#: REGIME_SLOPE_BARS and ATR_PERIOD, so what the chart shades is what the
#: rule reads. A fourth number - the threshold the reading is cut at - is not
#: here, because nobody has chosen it yet: the page offers percentiles of the
#: measure instead, which is how it is meant to be chosen.
SLOPE_PERIOD = 100
SLOPE_WINDOW = 20
SLOPE_ATR = 14


def slope(closes, highs, lows, period=SLOPE_PERIOD, window=SLOPE_WINDOW,
          atrPeriod=SLOPE_ATR):
    """
    How fast the slow average is moving, per bar, in ATR.

        (SMA(t) - SMA(t-window)) / (window * ATR(t))

    Dividing by the window is what makes the number comparable: without it the
    same market reads 1.0 over ten bars and 2.0 over twenty, and a threshold
    on it means a different thing for every window. Dividing by the ATR is
    what makes it comparable across instruments and across years - the result
    is a fraction of a bar's own range and not a number of pips.

    None until both averages are warm, like every other curve here.
    """
    if window < 1:
        raise ValueError("slope window must be at least 1, got %r" % (window,))
    slow = sma(closes, period)
    width = atr(highs, lows, closes, atrPeriod)
    out = [None] * len(closes)
    for i in range(window, len(closes)):
        before, now, wide = slow[i - window], slow[i], width[i]
        if before is None or now is None or not wide:
            continue
        out[i] = (now - before) / (window * wide)
    return out


def percentile(values, percent):
    """
    The percentile of `values`, interpolated between the two neighbours.

    Its own four lines rather than numpy: this module has no dependencies and
    the page that reads it is served from a standard library HTTP server.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    place = (len(ordered) - 1) * percent / 100.0
    low = int(place)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (place - low)


def stdev(values, period):
    """
    Population standard deviation over a rolling window, None until warm.

    Population and not sample: the window is the whole of what the band is
    describing, not a draw from something larger, and it is what every charting
    package computes for Bollinger bands. The difference is small and it is not
    zero, so it is stated rather than left to the reader.
    """
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        out[i] = (sum((v - mean) ** 2 for v in window) / period) ** 0.5
    return out


def bollinger(values, period, deviations):
    """(middle, upper, lower). The middle band is the SMA."""
    middle = sma(values, period)
    spread = stdev(values, period)
    upper, lower = [None] * len(values), [None] * len(values)
    for i, mid in enumerate(middle):
        if mid is None or spread[i] is None:
            continue
        upper[i] = mid + deviations * spread[i]
        lower[i] = mid - deviations * spread[i]
    return middle, upper, lower


#: kind -> what it needs and what it is called, so an unknown kind is refused
#: by name instead of drawn as something else
KINDS = ('sma', 'ema', 'bollinger', 'atr')

#: the kinds that are not on the price axis. See the module docstring: this is
#: a fact about the indicator and not a preference, so the page is told rather
#: than left to guess from the size of the numbers.
PANEL = ('atr',)


def curve(spec, values, highs=None, lows=None):
    """
    One declared curve, computed. Returns the dictionary the page draws, or
    raises ValueError for a declaration it does not understand - a strategy
    asking for an indicator nobody wrote is a mistake worth hearing about
    rather than a curve quietly missing from a chart.

    `values` are the closes. An indicator that reads the whole bar wants the
    highs and lows as well, and refuses rather than falling back to the closes:
    an ATR of the closes is a different number with the same name.
    """
    kind = spec.get('kind')
    if kind not in KINDS:
        raise ValueError("unknown indicator %r; this draws %s"
                         % (kind, ", ".join(KINDS)))
    period = int(spec.get('period', 20))
    drawn = {'kind': kind, 'panel': kind in PANEL,
             'label': spec.get('label') or "%s %d" % (kind.upper(), period)}
    if kind == 'bollinger':
        deviations = float(spec.get('deviations', 2.0))
        middle, upper, lower = bollinger(values, period, deviations)
        drawn['label'] = spec.get('label') \
            or "Bollinger %d / %g" % (period, deviations)
        drawn.update({'middle': middle, 'upper': upper, 'lower': lower})
        return drawn
    if kind == 'atr':
        if highs is None or lows is None:
            raise ValueError("atr needs the highs and the lows, not only the "
                             "closes")
        drawn['values'] = atr(highs, lows, values, period)
        return drawn
    drawn['values'] = sma(values, period) if kind == 'sma' \
        else ema(values, period)
    return drawn


def curves(specs, values, highs=None, lows=None):
    """Every declared curve, in the order declared."""
    return [curve(spec, values, highs, lows) for spec in (specs or [])]
