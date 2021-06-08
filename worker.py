#!/usr/bin/env python3
import os
import sys
import getopt
import hashlib
import time
import platform
import logging
import subprocess
import re
from datetime import datetime

VERSION = "%s v0.1" % sys.argv[0]

USAGE = """
This script loops through the paths in the list `basepaths` and searches for
subdirectories whose names match the corresponding pattern in `patterns`. For
each matching directory, enter that directory and run the command specified
by `cmd`. Note that basepaths are not traversed recursively.

Multiple workers are allowed to run on the same set of `basepaths` and
`patterns`. We use a simple lockfile mechanism to avoid race conditions where
more than one worker is executing a run. To take responsibility for a directory,
a worker must create a file called `worker.lock` using "x" access mode in that
directory. This requires python3.

Entries in patterns are strings interpreted as Python Regular Expressions. See
the syntax at <https://docs.python.org/3/library/re.html>

Usage: python3 %s [-v] [-h] | -b <path> [-b <path2> ...] -p <pattern>
           [-p <pattern2> ...] -c <cmd> --maxjobs=<maxjobs> --maxhours=<maxhours>
	-v or --version   print the version and exit
	-h or --help      print usage and exit
	-b or --basepath  include path in the list of basepaths
	-p or --pattern   include pattern in the list of patterns
	-c or --cmd       command to launch each job
	--maxjobs         max # of jobs to run before stopping (default: unlimited)
	--maxhours        max # of hours to run before exiting (default: unlimited)
""" % sys.argv[0]

# ---- Begin parsing command line args -----
basepaths = []
patterns = []
cmd = []
max_jobs = sys.maxsize
max_hours = sys.maxsize

def parse():
	longopts = ["version", "help", "basepath=", "pattern=", "cmd=", "maxjobs=",
		"maxhours="]
	options, arguments = getopt.getopt(
		sys.argv[1:], # Arguments
		'vhb:p:c:',   # Short option definitions
		longopts)     # Long option definitions
	for o, a in options:
		if o in ("-v", "--version"):
			print(VERSION)
			sys.exit()
		if o in ("-h", "--help"):
			print(USAGE)
			sys.exit()
		if o in ("-b", "--basepath"):
			basepaths.append(a)
		if o in ("-p", "--pattern"):
			patterns.append(a)
		if o in ("-c", "--cmd"):
			# We need present the command as a list of tokens later when we
			# invoke it with subprocess. This might not be the most robust way
			# to take the input, but let's see how well it works.
			for tok in a.split(' '):
				cmd.append(tok)
		if o in ("--maxjobs"):
			maxjobs = a
		if o in ("--maxhours"):
			maxhours = a
	try:
		operands = [int(arg) for arg in arguments]
	except ValueError:
		raise SystemExit(USAGE)
	if len(basepaths) == 0:
		raise RuntimeError("Must provide at least one basepath")
	if len(patterns) == 0:
		raise RuntimeError("Must provide at least one pattern")
	if len(cmd) == 0:
		raise RuntimeError("Must provide a command")

parse()
# ---- End parsing command line args -----

# Take now to be the starting time
start_time = datetime.now()
elapsed_hours = 0

# Set up logging
logging.basicConfig(
	format = '%(asctime)s - %(message)s',
	level = logging.INFO,
	datefmt = '%Y-%m-%d %H:%M:%S')

# Create a hash that represents a (somewhat) unique ID for this run
# Use hostname with time appended
str2hash = platform.node() + str(time.time())
result = hashlib.md5(str2hash.encode())
worker_id = result.hexdigest()
logging.info("Worker ID: %s" % worker_id)

# Get the current working directory
homepath = os.getcwd()
logging.info("Working directory: %s" % homepath)

# Throw an exception if L != len(patterns)
L = len(basepaths)
if L != len(patterns):
	msg = "Length %d of basepaths is not equal to length %d of patterns"
	raise RuntimeError(msg % (L, len(patterns)))

keep_looping = True
processed_jobs = 0

# ----- Finally, start the main loop -----
while keep_looping:
	# Reset this to False. We need to find least one new unlocked file later
	# to set this to True, otherwise we'll stop looping.
	keep_looping = False
	logging.info("Searching %d basepaths for available work" % L)

	for i in range(L):
		basepath = basepaths[i]
		pattern = patterns[i]

		logging.info("Basepath[%d]: %s  Pattern: %s" % (i, basepath, pattern))

		for subdir in os.listdir(basepath):
			# Ignore entries that are not directories
			if not os.path.isdir(subdir):
				continue

			# Ignore entries that don't match the pattern
			match = re.search(pattern, subdir)
			if not match:
				continue

			# Workers coordinate through the existence of this lockfile
			lockfile = os.path.join(basepath, subdir + os.path.sep + "worker.lock")

			# Check if the lockfile exists. If so, we can ignore this folder
			if os.path.isfile(lockfile):
				logging.info("Lockfile in %s exists, skipping" % subdir)
				continue

			# If we find at least one subdir without a lock, there might
			# be more work to do. Set keep_looping to True
			keep_looping = True

			# There is no lockfile, so see if we can acquire it ourselves
			try:
				with open(lockfile, 'x') as f:
					f.write("Reserved by worker: %s" % worker_id)
					logging.info("Lockfile in %s acquired" % subdir)

					# Now change to the directory of the job
					path = os.path.join(basepath, subdir)
					os.chdir(path)

					# Run the job. Make sure to save stdout and stderr steams
					stdout = os.path.join(path, "worker.out")
					stderr = os.path.join(path, "worker.err")
					with open(stdout, 'w') as g, open(stderr, 'w') as h:
						subprocess.call(cmd, stdout = g, stderr = h)

					# Increment the number of jobs we have processed
					processed_jobs += 1
			except FileExistsError:
				logging.warn("Could not lock: %s" % lockfile)
			finally:
				# Change back to the home path
				os.chdir(homepath)

			elapsed_hours = (datetime.now() - start_time).total_seconds() / 60**2
			logging.info("Processed %d jobs so far" % processed_jobs)
			logging.info("Ran for %f hours so far" % elapsed_hours)

			if processed_jobs >= max_jobs:
				logging.info("Reached limit of %d jobs" % max_jobs)
				break

			if elapsed_hours >= max_hours:
				logging.info("Reached limit of %d hours" % max_hours)
				break

logging.info("Done")
