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
from parity_deriva.data.transaction import StreamingForexTransactions
from parity_deriva.execution.execution import OANDAExecutionHandler
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading.engine import Engine
from parity_deriva.event.replay import EventReplay

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

# Create the strategy/signal generator, passing the 
# instrument and the events queue

# Create the portfolio object that will be used to
# compare the OANDA positions with the local, to
# ensure backtesting integrity.
#portfolio = Portfolio( prices, equity=equity, backtest=False)
#e.push(portfolio)

e.add_handler(EventReplay())



# Create two separate threads: One for the trading loop
# and another for the market price streaming class

# Start both threads
logger.info("Starting replay engine")
e.run()

