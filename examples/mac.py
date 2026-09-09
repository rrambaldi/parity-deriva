from parity_deriva.backtest.backtest import Backtest
from parity_deriva.execution.execution import SimulatedExecution
from parity_deriva.portfolio.portfolio import Portfolio
from parity_deriva.etc import settings
from parity_deriva.strategy.strategy import MovingAverageCrossStrategy
from parity_deriva.data.price import HistoricCSVPriceHandler


if __name__ == "__main__":
    # Trade on GBP/USD and EUR/USD
    pairs = ["GBPUSD", "EURUSD"]
    
    # Create the strategy parameters for the
    # MovingAverageCrossStrategy
    strategy_params = {
        "short_window": 500, 
        "long_window": 2000
    }
   
    # Create and execute the backtest
    backtest = Backtest(
        pairs, HistoricCSVPriceHandler, 
        MovingAverageCrossStrategy, strategy_params, 
        Portfolio, SimulatedExecution, 
        equity=settings.EQUITY
    )
    backtest.simulate_trading()