import os, time
import random

NUM_PROCESSES = 7

def timeConsumingFunction(ret):
	x = 1
	for n in range(1000000):
		x *= 1

def run(id):
	pid = os.fork()
	if pid:
		return pid
	else:
		timeConsumingFunction(id)
		ret = random.choice([0,1])
		print("%d done : %d" % (id,ret))
		os._exit(ret)

children = {}
retok = {}
start_time = time.time()
for process in range(NUM_PROCESSES):
	pid = run(process)
	children[pid] = process
	retok[process] = False

print("WAIT....")
alldone=False
while not alldone:
	ret=os.waitpid(0, 0)
	proc = children[ret[0]]
	print("RET: %d -> %d" % (proc, ret[1]))
	if ret[1]!=0:
		pid = run( proc )
		children[pid] = proc
		retok[proc] = False
		print("%d respawn" % proc)
		continue

	retok[proc] = True
	alldone = True
	for p in range(NUM_PROCESSES):
		print("STATS: %d %d " % (p, retok[p]))
		alldone = alldone and retok[p]

		
