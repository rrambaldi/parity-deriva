import numpy as np
import pandas as pd


def create_drawdowns(pnl):
    """
    Calculate the largest peak-to-trough drawdown of the PnL curve
    as well as the duration of the drawdown. Requires that the 
    pnl_returns is a pandas Series.

    Parameters:
    pnl - A pandas Series representing period percentage returns.

    Returns:
    drawdown, duration - Highest peak-to-trough drawdown and duration.
    """

    # Calculate the cumulative returns curve 
    # and set up the High Water Mark
    # Seed with the first observation, not with 0: seeding with 0 made hwm[1]
    # equal pnl[1] whatever happened, so drawdown[1] was always exactly 0 and
    # an opening fall was invisible.
    idx = pnl.index
    if len(idx) == 0:
        empty = pd.Series(index=idx, dtype=float)
        return empty, float('nan'), float('nan')
    hwm = [pnl.iloc[0]]

    # Create the drawdown and duration series
    drawdown = pd.Series(index=idx, dtype=float)
    duration = pd.Series(index=idx, dtype=float)
    drawdown.iloc[0] = 0.0
    duration.iloc[0] = 0.0

    # Loop over the index range
    for t in range(1, len(idx)):
        hwm.append(max(hwm[t-1], pnl.iloc[t]))
        drawdown.iloc[t] = (hwm[t]-pnl.iloc[t])
        duration.iloc[t] = (0 if drawdown.iloc[t] == 0 else duration.iloc[t-1]+1)
    return drawdown, drawdown.max(), duration.max()
