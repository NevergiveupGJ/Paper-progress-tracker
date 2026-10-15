#!/bin/sh
# Paper Progress Tracker for Linux. Run ./start-linux.sh in a terminal, or
# double-click it in your file manager and choose Run, to start it.
# It finds Python 3.9 or newer, builds the page, starts the local server if it
# is not running yet and opens the tracker in your browser.
# Run ./stop-linux.sh to stop it.
# The same as running: python3 app/launcher.py start
cd "$(dirname "$0")" || exit 1
action="${1:-start}"

py=""
for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    py="$c"
    break
  fi
done

if [ -z "$py" ]; then
  echo "Paper Progress Tracker needs Python 3.9 or newer, and it was not found."
  echo "Install it with your package manager, for example with sudo apt install python3,"
  echo "or get it from https://www.python.org/downloads/"
  code=1
else
  "$py" app/launcher.py "$action"
  code=$?
fi
if [ "$code" -ne 0 ] && [ -t 0 ]; then
  printf "Press Enter to close. "
  read -r _
fi
exit "$code"
