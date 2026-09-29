#!/bin/bash
# Double-click on macOS (or run ./start.command): starts Paper Progress Tracker and opens it.
cd "$(dirname "$0")/app"
if ! curl -s -m 1 http://127.0.0.1:47285/api/ping >/dev/null; then
  python3 build_local.py >/dev/null && (nohup python3 server.py > ../server.log 2>&1 &)
  sleep 1
fi
open http://127.0.0.1:47285/ 2>/dev/null || xdg-open http://127.0.0.1:47285/
