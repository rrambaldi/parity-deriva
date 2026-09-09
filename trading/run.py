import copy
from decimal import Decimal, getcontext
import logging
try:
	import Queue as queue
except ImportError:
	import queue
import threading
import time

from qsforex.execution.execution import OANDAExecutionHandler
from qsforex.portfolio.portfolio import Portfolio
from qsforex.etc import settings
from qsforex.lib.utils import getLogger
from qsforex.strategy.AG import AGStrategy
from qsforex.data.livecandles import LiveForexCandles
from qsforex.data.streaming import StreamingForexTransactions
from qsforex.trading.engine import Engine

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
strategy = AGStrategy( pairs)
e.push(strategy)

# Create the portfolio object that will be used to
# compare the OANDA positions with the local, to
# ensure backtesting integrity.
#portfolio = Portfolio( prices, equity=equity, backtest=False)
#e.push(portfolio)

# Create the eecution handler making sure to
# provide authentication commands
execution = OANDAExecutionHandler( )
e.push(execution)



# Create two separate threads: One for the trading loop
# and another for the market price streaming class

logger.info("Starting price streaming thread")
prices = LiveForexCandles( pairs, 'M1')
price_thread = threading.Thread(target=prices.stream_to_queue, args=[])
price_thread.start()

logger.info("Starting transaction streaming thread")
transaction = StreamingForexTransactions( pairs )
transaction_thread = threading.Thread(target=transaction.stream_to_queue, args=[])
transaction_thread.start()

# Start both threads
logger.info("Starting trading engine")
e.run()

