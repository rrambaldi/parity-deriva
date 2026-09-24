import sys
from pathlib import Path

here = Path(__file__).resolve().parent
# tests/ per synth, candle_forecast/ per il pacchetto, la cartella che contiene parity_deriva (store, indicatori)
sys.path[:0] = [str(here), str(here.parent), str(here.parents[2])]
