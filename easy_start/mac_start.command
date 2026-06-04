#!/bin/bash
set -u

cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Local environment was not found. Running installer first."
  echo
  exec easy_start/mac_install_and_run.command
fi

echo "Starting DataProcess Web..."
echo "Leave this window open while using the app."
echo
.venv/bin/python web_app.py
STATUS=$?
echo
echo "DataProcess closed."
read -r -p "Press Return to close this window."
exit "$STATUS"
