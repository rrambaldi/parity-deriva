# QuantStart Forex

QSForex is an open-source event-driven backtesting and live trading platform for use in the foreign exchange ("forex") markets, currently in an "alpha" state.

It has been created as part of the Forex Trading Diary series on QuantStart.com to provide the systematic trading community with a robust trading engine that allows straightforward forex strategy implementation and testing. 

The software is provided under a permissive "MIT" license (see below).

# Current Features

* **Open-Source** - QSForex has been released under an extremely permissive open-source MIT License, which allows full usage in both research and commercial applications, without restriction, but with no warranty of any kind whatsoever.
* **Free** - QSForex is completely free and costs nothing to download or use.
* **Collaboration** - As QSForex is open-source many developers collaborate to improve the software. New features are added frequently. Any bugs are quickly determined and fixed.
* **Software Development** - QSForex is written in the Python programming language for straightforward cross-platform support. QSForex contains a suite of unit tests for the majority of its calculation code and new tests are constantly added for new features.</li>
* **Event-Driven Architecture** - QSForex is completely event-driven both for backtesting and live trading, which leads to straightforward transitioning of strategies from a research/testing phase to a live trading implementation.
* **Transaction Costs** - Spread costs are included by default for all backtested strategies.
* **Backtesting** - QSForex features intraday tick-resolution multi-day multi-currency pair backtesting.
* **Trading** - QSForex currently supports live intraday trading using the OANDA Brokerage API across a portfolio of pairs.
* **Performance Metrics** - QSForex currently supports basic performance measurement and equity visualisation via the Matplotlib and Seaborn visualisation libraries.

# Installation and Usage

1) Visit http://www.oanda.com/ and setup an account to obtain the API authentication credentials, which you will need to carry out live trading. I explain how to carry this out in this article: https://www.quantstart.com/articles/Forex-Trading-Diary-1-Automated-Forex-Trading-with-the-OANDA-API.

2) Clone this git repository into a suitable location on your machine using the following command in your terminal: ```git clone https://github.com/mhallsmoore/qsforex.git```. Alternative you can download the zip file of the current master branch at https://github.com/mhallsmoore/qsforex/archive/master.zip.

3) Create a set of environment variables for all of the settings found in the ```etc/settings.py``` file. Alternatively, you can "hard code" your specific settings by overwriting the ```os.environ.get(...)``` calls for each setting. The environment variable names are the ones used in that file (```QSFOREX_CSV_DATA_DIR```, ```OUTPUT_RESULTS_DIR```, ```QSFOREX_DATA_DIR```, ```QSFOREX_LOG_DIR```, ```OANDA_API_ACCESS_TOKEN```, ```OANDA_API_ACCOUNT_ID```):

```
# The data directory used to store your backtesting CSV files
CSV_DATA_DIR = "/path/to/your/csv/data/dir"

# The directory where the backtest.csv and equity.csv files 
# will be stored after a backtest is carried out
OUTPUT_RESULTS_DIR = "/path/to/your/output/results/dir"

# Change DOMAIN to "real" if you wish to carry out live trading
DOMAIN = "practice"

# Your OANDA API Access Token (found in your Account Details on their website)
ACCESS_TOKEN = "1234123412341234"

# Your OANDA Account ID (found in your Account Details on their website)
ACCOUNT_ID = "1234123412341234"

# Your base currency (e.g. "GBP", "USD", "EUR" etc.)
BASE_CURRENCY = "GBP"

# Your account equity in the base currency (for backtesting)
EQUITY = Decimal("100000.00")
```

4) QSForex requires **Python 3.11 or newer** (it was migrated off Python 2.7); the pinned requirements were verified on Python 3.12, and the newest NumPy/SciPy releases need 3.12. The code runs on both pandas 2.x and pandas 3.x. Create a virtual environment for the code and utilise pip to install the requirements. For instance in a Unix-based system (Mac or Linux) you might create such a directory as follows by entering the following commands in the terminal:

```
mkdir -p ~/venv/qsforex
cd ~/venv/qsforex
python3 -m venv .
```

This will create a new virtual environment to install the packages into. Assuming you downloaded the QSForex git repository into an example directory such as ```~/projects/qsforex/``` (change this directory below to wherever you installed QSForex), then in order to install the packages you will need to run the following commands:

```
source ~/venv/qsforex/bin/activate
pip install -r ~/projects/qsforex/requirements.txt
```

This will normally install pre-built wheels for NumPy, SciPy, Pandas, Scikit-Learn, Matplotlib and PyTables. There are many packages required for this to work, so please take a look at these two articles for more information:

* https://www.quantstart.com/articles/Quick-Start-Python-Quantitative-Research-Environment-on-Ubuntu-14-04
* https://www.quantstart.com/articles/Easy-Multi-Platform-Installation-of-a-Scientific-Python-Stack-Using-Anaconda

You will also need to create a symbolic link from your ```site-packages``` directory to your QSForex installation directory in order to be able to call ```import qsforex``` within the code. To do this you will need a command similar to the following:

```
ln -s ~/projects/qsforex/ ~/venv/qsforex/lib/python3.12/site-packages/qsforex
```

Make sure to change ```~/projects/qsforex``` to your installation directory and ```~/venv/qsforex/lib/python3.12/site-packages/``` to your virtualenv site packages directory (adjust the version to the Python 3 you created the venv with). Alternatively, put the directory that *contains* ```qsforex``` on ```PYTHONPATH```.

You will now be able to run the subsequent commands correctly.

## Practice/Live Trading

5) At this stage, if you simply wish to carry out practice or live trading then you can run ```python trading/run.py```, which wires an ```AG01``` strategy, a ```MoneyManager```, the OANDA execution handler and the price/transaction streams into the ```Engine```. The ```scripts/``` directory holds further ready-made wirings (```t01.py``` .. ```t05.py```, ```onlydata.py```). Do not point any of these at a live account until you have read what they do!

If you wish to create a more useful strategy, then simply create a new class with a descriptive name, e.g. ```MeanReversionMultiPairStrategy```. Strategies driven by the ```Engine``` subclass ```ExecutionHandler``` and implement ```execute_event(event)``` (see ```strategy/AG01.py``` and ```strategy/BO.py```); the backtester's example strategies instead implement ```calculate_signals(event)``` and take the ```pairs``` list plus the ```events``` queue.

Please look at ```strategy/strategy.py``` for details.

## Backtesting

6) In order to carry out any backtesting it is necessary to generate simulated forex data or download historic tick data. If you wish to simply try the software out, the quickest way to generate an example backtest is to generate some simulated data. The current data format used by QSForex is the same as that provided by the DukasCopy Historical Data Feed at https://www.dukascopy.com/swiss/english/marketwatch/historical/.

To generate some historical data, make sure that the ```CSV_DATA_DIR``` setting in ```settings.py``` is to set to a directory where you want the historical data to live. You then need to run ```generate_simulated_pair.py```, which is under the ```scripts/``` directory. It expects a single command line argument, which in this case is the currency pair in ```BBBQQQ``` format. For example:

```
cd ~/projects/qsforex
python scripts/generate_simulated_pair.py GBPUSD
```

At this stage the script is hardcoded to create a single month's data for January 2014. That is, you will see individual files, of the format ```BBBQQQ_YYYYMMDD.csv``` (e.g. ```GBPUSD_20140112.csv```) appear in your ```CSV_DATA_DIR``` for all business days in that month. If you wish to change the month/year of the data output, simply modify the file and re-run.

7) Now that the historical data has been generated it is possible to carry out a backtest. The backtest file itself is stored in ```backtest/backtest.py```, but this only contains the ```Backtest``` class. To actually execute a backtest you need to instantiate this class and provide it with the necessary modules. 

The best way to see how this is done is to look at the example Moving Average Crossover implementation in the ```examples/mac.py``` file and use this as a template. This makes use of the ```MovingAverageCrossStrategy``` which is found in ```strategy/strategy.py```. This defaults to trading both GBP/USD and EUR/USD to demonstrate multiple currency pair usage. It uses data found in ```CSV_DATA_DIR```.

To execute the example backtest, simply run the following:

```
python examples/mac.py
```

**This will take some time.** On my Ubuntu desktop system at home, with the historical data generated via ```generate_simulated_pair.py```, it takes around 5-10 mins to run. A large part of this calculation occurs at the end of the actual backtest, when the drawdown is being calculated, so please remember that the code has not hung up! Please leave it until completion.

8) If you wish to view the performance of the backtest you can simply use ```output.py``` to view an equity curve, period returns (i.e. tick-to-tick returns) and a drawdown curve:

```
python backtest/output.py
```

And that's it! At this stage you are ready to begin creating your own backtests by modifying or appending strategies in ```strategy/strategy.py``` and using real data downloaded from DukasCopy (https://www.dukascopy.com/swiss/english/marketwatch/historical/).

If you have any questions about the installation then please feel free to email me at mike@quantstart.com.

If you have any bugs or other issues that you think may be due to the codebase specifically, feel free to open a Github issue here: https://github.com/mhallsmoore/qsforex/issues

# Tests

The project ships a characterisation test suite under ```qsforex/tests/```
(plain ```unittest```, no network access, no OANDA account needed). Run it
with:

```
python -m unittest discover -s qsforex -p '*_test.py'
```

That also picks up the two original ```portfolio/*_test.py``` modules. See
```qsforex/tests/README.md``` for what each module covers and for the list of
behaviours that are pinned deliberately because they look wrong.

# License Terms

Copyright (c) 2015 Michael Halls-Moore

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Forex Trading Disclaimer

Trading foreign exchange on margin carries a high level of risk, and may not be suitable for all investors. Past performance is not indicative of future results. The high degree of leverage can work against you as well as for you. Before deciding to invest in foreign exchange you should carefully consider your investment objectives, level of experience, and risk appetite. The possibility exists that you could sustain a loss of some or all of your initial investment and therefore you should not invest money that you cannot afford to lose. You should be aware of all the risks associated with foreign exchange trading, and seek advice from an independent financial advisor if you have any doubts.