#!/bin/bash
#
# Keep the candle warehouse topped up, restarting the downloader if it dies.
# Stop it by removing the lock file: rm /tmp/parity-deriva-save.run
#
# Arguments are passed straight through to scripts/save.py, e.g.
#   ./save.sh --instrument DE30_EUR,EUR_USD --timeframe M1
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${PARITY_DERIVA_SAVE_LOCK:-/tmp/parity-deriva-save.run}"
LOG="${PARITY_DERIVA_SAVE_LOG:-/tmp/parity-deriva-save.log}"
VENV="${PARITY_DERIVA_VENV:-$HERE/../venv}"

# the package is imported as parity_deriva, so its parent goes on the path
export PYTHONPATH="$(dirname "$HERE")${PYTHONPATH:+:$PYTHONPATH}"
export PARITY_DERIVA_HOME="$HERE"

if [ -f "$VENV/bin/activate" ]; then
	# shellcheck disable=SC1091
	source "$VENV/bin/activate"
else
	echo "no virtualenv at $VENV, using $(command -v python3)" >&2
fi

touch "$RUN"
exec >>"$LOG" 2>&1

while [ -f "$RUN" ]; do
	echo "=== $(date -Is) starting scripts/save.py ==="
	python3 "$HERE/scripts/save.py" "$@"
	echo "=== $(date -Is) exited with $? ==="
	sleep 5
done
