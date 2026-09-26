#!/bin/sh
# Rebuild the shared spread set (scripts/spread_profile.py) from cron, once a
# week, on Saturday: the FX week is shut, so IG's last week is whole, and one
# week a week leaves no gap. That week costs about 2,900 of IG's 10,000 weekly
# points, shared with any live IG session.
#
#     17 7 * * 6  /path/to/parity_deriva/scripts/spread_cron.sh >> spread_cron.log 2>&1
#
# Extra arguments go to spread_profile.py (--force on a server that only
# reads the market folder). MT5 is left out: its bar spread is the bar's
# minimum, never the widest, and the bridge is a Wine terminal - the slots it
# measured once stay in the file.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
# IG's credentials are read from the environment only
set -a; . "$ROOT/parity_deriva/.env"; set +a
export PYTHONPATH="$ROOT" PARITY_DERIVA_HOME="$ROOT/parity_deriva"
# ponytail: the server's RAM is tight; the build needs a few hundred MB
ulimit -v 3000000
echo "== $(date -u '+%Y-%m-%d %H:%M UTC')"
exec nice "$ROOT/venv/bin/python" "$ROOT/parity_deriva/scripts/spread_profile.py" \
	--provider ig --weeks 1 --write "$@"
