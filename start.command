#!/bin/bash
# Paper Progress Tracker for macOS. Double-click this file to start it.
# It finds Python 3.9 or newer, builds the page, starts the local server if it
# is not running yet and opens the tracker in your browser.
# Double-click stop.command to stop it.
# The same as running in Terminal: python3 app/launcher.py start
cd "$(dirname "$0")" || exit 1
action="${1:-start}"

find_python() {
  # python.org, Homebrew and the Apple developer tools put Python in these places.
  # Each program is tried once. On a new Mac /usr/bin/python3 only offers to install the developer
  # tools, and trying it twice would show that offer twice.
  tried="|"
  for c in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    case "$tried" in *"|$p|"*) continue ;; esac
    tried="$tried$p|"
    if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

if ! py="$(find_python)"; then
  echo "Paper Progress Tracker needs Python 3.9 or newer, and it was not found."
  echo "If macOS offered to install the command line developer tools, you can click Install."
  echo "They include Python. Or get Python free from https://www.python.org/downloads/macos/"
  echo "Then double-click this file again."
  echo
  read -r -n 1 -p "Press D to open the download page, or any other key to close. " key
  echo
  if [ "$key" = "d" ] || [ "$key" = "D" ]; then
    open "https://www.python.org/downloads/macos/"
  fi
  exit 1
fi

"$py" app/launcher.py "$action"
code=$?
if [ "$code" -ne 0 ]; then
  echo
  read -r -n 1 -p "Press any key to close this window. " _
  echo
fi
exit "$code"
