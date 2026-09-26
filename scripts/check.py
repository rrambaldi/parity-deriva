import pandas as pd
import numpy as np
import os
import glob
import time
import datetime
import parity_deriva.etc.settings as settings
import logging
import logging.config
from parity_deriva.data import market
from parity_deriva.data.bulksaver import BulkSaver
from parity_deriva.lib.utils import granularityToTimedelta, getLogger
logger = getLogger()


for fil in  glob.glob(market.directory() + '/DE*.hd5'):
	h =  os.path.basename(fil)[:-4]

	print(" === %s" % fil)
	s=pd.HDFStore(fil)
	kk = s.keys()
	kk = [ '/M1' ]
	s.close()
	for k in kk:
		print(" === %s%s" % (h,k))
#		print(" %s%s %d" % ( h,k, s.get_storer(k).nrows))
#		print(" %s%s MIN: %s" % ( h,k, s.get_storer(k)))
#		x = pd.read_hdf(fil, k, where='index >= 20140714 and index <= 20140715')
#		print(" %s %s MIN: %s" % ( h, k, s[k].index.min()))
#		print(" %s $s MAX: %s" % ( h, k, s[k].index.max()))
#, len(s[k].index)) 
		td = granularityToTimedelta(k)
		for m in pd.date_range(start='20060101', end='20170130', freq='D'):
			dat=pd.read_hdf(fil, k, where='index >= '+m.strftime("%Y%m%d")+'000000 and index < '+m.strftime("%Y%m%d")+'235959')
			dat.sort_index(inplace=True)
			if dat.index.min() is pd.NaT:
				continue

			next_bar = dat.index.min()
			start = next_bar
			holes = {}
			groups = [ 0, 0, 0, 0, 0]
			num = 0

			print(dat.index.min())
			if dat.index.min() + td * len(dat.index) != dat.index.max():
				print("%s MISSING BARS..." % k)

			for i in dat.index:
				was = next_bar
				while next_bar < i:
					num += 1
					next_bar += td

				if i==next_bar:
					next_bar = next_bar + td
					if num>0:
						dt = pd.read_hdf(fil, k, where='index >= '+(start+td).strftime("%Y%m%d%H%M%S")+' and index < '+i.strftime("%Y%m%d%H%M%S"))
						if len(dt)>0:
							print("GOT %d vs %d" % ( len(dt), num))
						if num<10:
							groups[0] += 1
						elif num<100:
							groups[1] += 1
						elif num<4000:
							groups[2] += 1
						elif num<10000:
							groups[3] += 1
						else:
							groups[4] += 1

						if num not in holes:
							holes[num] = 0
						holes[num] += 1
					num = 0
					start = i
					continue

				print("hu...")
				print(next_bar)
				print(i)
				os._exit(0)

			print(groups)
		
#		for ho in holes.keys():
#			print("%s.%s HOLE %d QTY: %d" % ( h,k,ho,holes[ho] ))

		os._exit(0)


#print(s.M5['2010-01-04 07:00:00'])

#a=s.M5.loc['2010-01-03 17:53:00':'2010-01-04 08:00:00'].to_dict('spit')
#i=0
#x={}
#for t in a['index']:
#	j=0
#	z={}
#	for c in a['columns']:
#		z[c] = a['data'][j][i]
#	x[t]=z
#
#
#v=pd.DataFrame().from_dict(x,orient='index')
#print(v.index.max())


