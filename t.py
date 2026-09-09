import parity_deriva.lib.ohlc as ohlc
import pandas as pd
import datetime
import time

a=ohlc.ohlc(t=datetime.datetime.now(), o=1, h=2, l=3, c=4)
time.sleep(1)
b=ohlc.ohlc(t=datetime.datetime.now(), o=1.1, h=2, l=3, c=4)
v=pd.DataFrame.from_dict(a.to_dict(),orient='index')
v.loc[b.t]={ 'o': b.o
                , 'h': b.h
                , 'l': b.l
                , 'c': b.c
}

print(v.head())
