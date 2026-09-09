import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import queue
import threading
import time

from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.data.candles import ForexCandles
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

# Create the OANDA market price streaming class
# making sure to provide authentication commands


# Create two separate threads: One for the trading loop
# and another for the market price streaming class

e.add_handler( ForexCandles( pairs=pairs, granularity='M1') )

# Start both threads
logger.info("Starting trading engine")
e.run()

