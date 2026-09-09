#!/bin/bash
RUN=/tmp/save.run
touch $RUN
exec 1>/tmp/log
exec 2>/tmp/err
source ../bin/activate
while [ -f $RUN ]; do
	python /home/rrambaldi/qsforex/qsforex/s.py
done
