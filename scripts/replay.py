import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import queue
import threading
import time

from qsforex.etc import settings
from qsforex.lib.utils import getLogger
from qsforex.data.candles import ForexCandles
from qsforex.data.transaction import StreamingForexTransactions
from qsforex.execution.execution import OANDAExecutionHandler
from qsforex.portfolio.moneymanager import MoneyManager
from qsforex.trading.engine import Engine
from qsforex.event.replay import EventReplay

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

