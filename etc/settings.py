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

