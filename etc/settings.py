from decimal import Decimal
import os


ENVIRONMENTS = { 
    "streaming": {
        "real": "stream-fxtrade.oanda.com",
        "practice": "stream-fxpractice.oanda.com",
        "sandbox": "stream-sandbox.oanda.com"
    },
    "api": {
        "real": "api-fxtrade.oanda.com",
        "practice": "api-fxpractice.oanda.com",
        "sandbox": "api-sandbox.oanda.com"
    }
}

# --------------------------------------------------------------------------
# Which broker the stack talks to
#
# 'oanda' or 'etoro'. The event bus is the same either way - strategies, the
# money manager, the simulator and the parity monitor do not know which is
# behind them - but the two brokers are not the same shape, and what each can
# do is declared in trading/providers.py rather than discovered. A wiring
# asks for what it needs with providers.require() and fails at startup if the
# chosen provider does not have it.
#
# DOMAIN below still decides practice against real, for both.
PROVIDER = os.environ.get('PARITY_DERIVA_PROVIDER', 'oanda')

CSV_DATA_DIR = os.environ.get('PARITY_DERIVA_CSV_DATA_DIR', ".")
OUTPUT_RESULTS_DIR = os.environ.get('OUTPUT_RESULTS_DIR', ".")

DOMAIN = "practice"
STREAM_DOMAIN = ENVIRONMENTS["streaming"][DOMAIN]
API_DOMAIN = ENVIRONMENTS["api"][DOMAIN]
# Credentials come from the environment only - never commit them. Set:
#   export OANDA_API_ACCESS_TOKEN=...
#   export OANDA_API_ACCOUNT_ID=...
# An empty token makes OANDA answer 401, which the data handlers report as a
# StatusEvent('ERROR') rather than crashing.
ACCESS_TOKEN = os.environ.get('OANDA_API_ACCESS_TOKEN', '')
ACCOUNT_ID = os.environ.get('OANDA_API_ACCOUNT_ID', '')
API_VERSION = '3'

DATA_DIR = os.environ.get('PARITY_DERIVA_DATA_DIR', "/home/rrambaldi/DATA")
LOG_DIR = os.environ.get('PARITY_DERIVA_LOG_DIR', "/home/rrambaldi/DATA")
BASE_CURRENCY = "EUR"
EQUITY = Decimal("100000.00")

DEF_CONFIG = 'logging.conf'
DEF_PAIRS  = [ 'EUR_USD' ]
DEF_GRANULARITY = 'M1'

# Decimal places OANDA accepts for an order price, per instrument - its
# "displayPrecision". Sending more is rejected outright
# (PRICE_PRECISION_EXCEEDED); rounding to fewer silently moves the level,
# which is worse, so a strategy must never guess.
#
# The authoritative list is GET /v3/accounts/{accountID}/instruments, field
# displayPrecision. The two entries below are the instruments this project has
# actually traded, and both are evidenced rather than assumed:
#   EUR_USD  5 - every price in the M1 history carries five decimals
#   DE30_EUR 1 - every accepted price in the captured OANDA responses in
#                execution/execution.py has one decimal, and the single
#                two-decimal one there is the order OANDA rejected with
#                TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED
INSTRUMENT_PRECISION = {
    'EUR_USD': 5,
    'DE30_EUR': 1,
}

# --------------------------------------------------------------------------
# Parity alarm
#
# trading/parity.py joins each live fill to its simulated counterpart on the
# signal key and counts the disagreements. These settings decide when that
# becomes an alarm, and whether an alarm stops trading. They are yours to set:
# the numbers below are defaults, not measurements.
#
# What IS measured is the floor. A simulator reads bars while an account
# trades ticks, so when one bar holds both the stop and the target of an open
# trade it cannot say which came first and has to guess. Run
# scripts/divergence_band.py to size that guess on your own data and
# granularity; a threshold below the figure it reports will fire on the width
# of the bars rather than on the market.
#
# On EUR_USD, AG01 on H1 over two months of M1 history, it reported:
#     fed H1 bars, the simulator's outcome is a coin flip on 1.23% of trades
#     fed M1 bars,                                              0.09%
# so max_outcome_mismatch below is set well clear of the first of those. Your
# instrument, granularity and period will give different numbers - measure,
# do not inherit these.
PARITY_ALARM = {
    # trades the rate is measured over, and the sample below which the
    # monitor declines to judge at all: with a 1% floor, one disagreement in
    # the first three trades means nothing
    'window': 200,
    'min_sample': 50,

    # fraction of reconciled trades whose outcome the two sides disagree on
    'max_outcome_mismatch': 0.05,

    # per-trade fill price difference, in the instrument's own units.
    # None disables the check rather than defaulting to something invented
    'max_slippage': None,

    # trades where only one side ever reported, counted in the window. This
    # is the serious one - the simulator filled and the account did not, or
    # the reverse - so it is deliberately tighter than the rate above
    'max_unpaired': 5,

    # trades the two sides closed but whose outcome could not be judged at
    # all. On OANDA this stays zero: the broker states which leg closed a
    # trade. On eToro it does not, so data/etoro.closeReason() infers the leg
    # from the closing rate and reports UNKNOWN where the rate belongs to
    # neither leg clearly - and a monitor that is blind to the outcome is
    # worth knowing about even though it is not itself a divergence.
    # None disables the check rather than defaulting to something invented
    'max_undecided': None,

    # 'warn' logs and keeps going; 'halt' also publishes StatusEvent('HALT'),
    # which MoneyManager honours by refusing further signals until a
    # StatusEvent('RESUME')
    'action': 'warn',
}

# Per-instrument overrides, merged over PARITY_ALARM. Volatility and spread
# differ enough between an index and a currency pair that one band rarely
# fits both.
PARITY_ALARM_BY_INSTRUMENT = {
    # 'DE30_EUR': {'max_outcome_mismatch': 0.10, 'action': 'halt'},
}

# Used when an instrument is not in the table above. Five decimals is the FX
# convention and, more importantly, it errs towards *too many* decimals: the
# broker then rejects the order loudly instead of accepting a level that has
# been silently rounded away.
DEFAULT_PRICE_PRECISION = 5

# --------------------------------------------------------------------------
# eToro
#
# Credentials come from the environment, like OANDA's. Use EITHER a bearer
# token OR the user-key/api-key pair - a request carrying both is rejected
# with 422, so setting both raises here instead.
#
#   export ETORO_API_DOMAIN=...        # the public API host
#   export ETORO_ACCESS_TOKEN=...      # OAuth bearer token
#   # or
#   export ETORO_USER_KEY=...
#   export ETORO_API_KEY=...
#
# The host below is evidenced rather than assumed, which is the only reason
# it has a default at all:
#   - it holds a Google Trust Services certificate issued to
#     public-api.etoro.com, so it is eToro's and not a lookalike
#   - unauthenticated it answers {"errorCode":"Unauthorized",...}, which is
#     the gatewayErrorEnvelope the published OpenAPI documents for exactly
#     that case - a different service would not answer in that shape
#   - GET /api/v1/me on it returns this account's profile and scopes
# It is still an environment variable because the tooling describes the host
# as a property of the deployment, so a partner application may be given a
# different one. Set it to empty and lib/etoro.EToroAPI refuses to be built
# rather than reaching somewhere unintended.
ETORO_API_DOMAIN = os.environ.get('ETORO_API_DOMAIN', 'public-api.etoro.com')
ETORO_ACCESS_TOKEN = os.environ.get('ETORO_ACCESS_TOKEN', '')
ETORO_USER_KEY = os.environ.get('ETORO_USER_KEY', '')
ETORO_API_KEY = os.environ.get('ETORO_API_KEY', '')

# This project names instruments the way OANDA does. eToro keys everything by
# a numeric instrumentId, and the mapping between the two is not derivable -
# 'DE30_EUR' and eToro's German index are the same market under two names and
# an id only eToro knows. So it is configuration, and an unmapped instrument
# raises rather than being resolved at runtime to whatever a search returns.
#
# Fill it in with:
#     python scripts/etoro_instruments.py EUR_USD DE30_EUR
# which prints what the API answers for a symbol, for you to paste here.
# 'precision' is optional and overrides INSTRUMENT_PRECISION for eToro only.
# The two below were resolved against the live API and are evidenced, not
# guessed: symbols=EURUSD returns instrumentId 1, "EUR/USD", type Forex, and
# symbols=GER40 returns instrumentId 32, "GER40 Index (Non Expiry)", type
# Indices. eToro has no GER30 and no DE30 - both 404 - so the counterpart of
# OANDA's DE30_EUR is GER40, a 40-constituent index where DE30_EUR tracks 30.
# They are not the same basket, and a strategy calibrated on one is not
# calibrated on the other.
ETORO_INSTRUMENTS = {
    'EUR_USD': {'symbol': 'EURUSD', 'instrumentId': 1, 'precision': 5},
    'DE30_EUR': {'symbol': 'GER40', 'instrumentId': 32, 'precision': 1},
}

# eToro serves ONE OHLC per candle. OANDA serves three, and the strategies
# read them: AG01 buys the high of the ask and stops out at the low of the
# bid. Those two prices do not exist in eToro's candle data, and no
# measurement recovers them, so bid and ask are a model or they are nothing.
#
# Unset (the default), candles carry mid only, the provider declines
# bid_ask_candles, and a wiring that needs them refuses to start and says
# why. That is the same rule PARITY_ALARM follows: a value nobody measured is
# off, not filled in with something plausible.
#
# Set it to a spread in the instrument's own units, or per instrument:
#     ETORO_SPREAD = 0.00001
#     ETORO_SPREAD = {'EUR_USD': 0.00001, 'DE30_EUR': 2.2}
# Half of it is applied either side of the served price.
#
# What one demo round trip actually measured, on 2026-09-10 in a quiet market
# (EUR_USD, 900 units, market in and market out about thirty seconds apart):
#
#   the rates route, twelve samples, constant to the last digit
#                                        0.00001 on EUR_USD, 2.20 on GER40
#   the fill's openingData               marketSpread 0.01, markup 0.02
#   the realised result                  netProfit -0.04 USD, fees 0.00,
#                                        open 1.16124, close 1.16120
#
# The last line is the only one in units anybody can check: 0.4 pips of
# adverse movement over the whole round trip on a 900-unit position. That is
# consistent with the feed's 0.00001 and rules out a spread of pips being
# charged on top of it.
#
# marketSpread and markup are therefore NOT price differences - 0.01 on a
# 1.16 instrument would be a hundred pips, which the P&L flatly contradicts.
# Read as percentages they come to roughly one and two pips, the right order
# of magnitude for eToro's published forex spread, but the API does not state
# their unit and this has not been established. Do not compute a level from
# them.
#
# So the feed is a defensible starting point after all, at least on the demo
# account. The real account may price differently, and the figure to trust
# there is the one this table's last line is made of: the P&L of a round trip
# you actually did.
ETORO_SPREAD = None

# Leverage on every order. Anything above 1 makes eToro require a stopLossRate,
# which execution/etoro.py then refuses to send an order without.
ETORO_LEVERAGE = 1

# Settlement type: cfd, real, realFutures or marginTrade. Optional on the v2
# create route and left unset here, because the eligible values differ per
# instrument, direction and leverage - POST /api/v2/trading/info/eligibility
# lists the valid combinations for your account.
ETORO_SETTLEMENT_TYPE = None

# eToro does not expire orders. This project's strategies place brackets that
# are meant to die at the end of the day, and a bracket still resting a week
# later is not that trade any more, so data/etoro.EToroTransactions cancels a
# resting order once the gtdTime it was issued with has passed. That is our
# action, not the broker's, and it is logged each time. Set False and a
# resting order rests until it triggers.
ETORO_ENFORCE_EXPIRY = True

# Seconds between polls. Nothing in the eToro public API is pushed, so this
# is the resolution at which candles, prices, fills and closes arrive. The
# published quotas are shared per group - 120/60s across eleven market-data
# endpoints, 20/60s across every execution endpoint, 60/60s for order info -
# and lib/etoro.RateLimiter paces against those, but a poll interval short
# enough to fight the limiter just adds latency.
ETORO_POLL_SECONDS = 5
