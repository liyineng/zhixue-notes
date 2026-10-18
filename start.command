#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Please install Python 3.11+ from https://www.python.org/downloads/macos/'
  read -r -p 'Press Enter to close...'
  exit 1
fi
python3 launch.py "$@"
result=$?
if [ "$result" -ne 0 ] && [ "$#" -eq 0 ]; then
  read -r -p 'Startup failed. Read the error above. Press Enter to close...'
fi
exit "$result"
