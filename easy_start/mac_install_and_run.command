#!/bin/bash
set -u

cd "$(dirname "$0")/.."

echo "DataProcess Web installer"
echo "This will create a local .venv folder and install dependencies."
echo

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Could not find Python 3.10, 3.11, or 3.12."
  echo "Please install Python 3.12 from https://www.python.org/downloads/"
  echo
  read -r -p "Press Return to close this window."
  exit 2
fi

"$PYTHON_BIN" easy_start/setup_env.py --run
STATUS=$?
echo
if [ "$STATUS" -eq 0 ]; then
  echo "DataProcess closed."
else
  echo "Installer exited with code $STATUS."
fi
read -r -p "Press Return to close this window."
exit "$STATUS"
