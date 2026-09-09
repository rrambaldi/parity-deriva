import qsforex.lib.ohlc as ohlc
import pandas as pd
import datetime
import time

a=ohlc.ohlc(1,2,3,4,datetime.datetime.now())
time.sleep(1)
b=ohlc.ohlc(1.1,2,3,4,datetime.datetime.now())
v=pd.DataFrame().from_dict(a.to_dict(),orient='index')
v.loc[b.t]={ 'o': b.o
                , 'h': b.h
                , 'l': b.l
                , 'c': b.c
}

v.head()
