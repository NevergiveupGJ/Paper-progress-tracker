#!/bin/sh
# Paper Progress Tracker for Linux. Run ./stop-linux.sh to stop it.
# Your data is saved all the time, so stopping never loses anything.
cd "$(dirname "$0")" || exit 1
exec sh ./start-linux.sh stop
