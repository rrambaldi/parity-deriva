import copy
from decimal import Decimal, getcontext
import logging
import queue
import threading
import time

from parity_deriva.execution.execution import OANDAExecutionHandler
from parity_deriva.portfolio.portfolio import Portfolio
from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.strategy.AG01 import AG01
from parity_deriva.data.candles import ForexCandles
from parity_deriva.data.transaction import StreamingForexTransactions
from parity_deriva.trading.engine import Engine

# Set up logging
logger = getLogger()

# Set the number of decimal places to 2
getcontext().prec = 2

e = Engine()
e.heartbeat = 0.5  # Time in seconds between polling
equity = settings.EQUITY

# Pairs to include in streaming data set
pairs = ["EUR_USD"]

# Create the strategy/signal generator, passing the
# instrument and the events queue
e.add_handler( AG01( pairs=pairs) )

# Create the portfolio object that will be used to
# compare the OANDA positions with the local, to
# ensure backtesting integrity.
#e.add_handler( Portfolio( prices, equity=equity, backtest=False) )

# Create the execution handler making sure to
# provide authentication commands
e.add_handler( OANDAExecutionHandler() )

# The Engine starts a thread per handler that exposes stream_to_queue(),
# so the price and transaction streams only need to be registered.
logger.info("Registering price streaming handler")
e.add_handler( ForexCandles( pairs=pairs, granularity='M1') )

logger.info("Registering transaction streaming handler")
e.add_handler( StreamingForexTransactions( pairs=pairs) )

logger.info("Starting trading engine")
e.run()
