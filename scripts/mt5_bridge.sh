#!/bin/sh
# The MetaTrader5 package is Windows-only, so it runs under Wine and is served
# to this side over rpyc - see lib/mt5.py. This starts that server; leave it
# running (nohup, tmux, a systemd user unit) while a stack is on provider mt5.
#
# The rpyc classic server runs whatever it is sent, so it listens on
# 127.0.0.1 and nowhere else.
#
# One bridge per terminal, so one per MT5 account: the port is the one the
# account's MT5_TERMINALS entry names ('bridge'), 18812 by default.
#
#     scripts/mt5_bridge.sh          # the first account
#     scripts/mt5_bridge.sh 18813    # a second, made by mt5_add_account.sh
#
# MT5_WINE_DIR holds wine-*/ , prefix/ (Python 3.12 + MetaTrader5 + rpyc) and
# the terminals under prefix/drive_c.
set -e
MT5_WINE_DIR=${MT5_WINE_DIR:-/mnt/HC_Volume_37718599/rrambaldi/mt5}
MT5_BRIDGE_PORT=${1:-${MT5_BRIDGE_PORT:-18812}}
export WINEPREFIX="$MT5_WINE_DIR/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/$(id -u)-runtime}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
WINE=$(ls -d "$MT5_WINE_DIR"/wine-*/bin | head -1)/wine
PY='C:\users\'"$(id -un)"'\AppData\Local\Programs\Python\Python312\python.exe'

# the terminal is a GUI program even when nobody looks at it
if [ -z "$DISPLAY" ]; then
	export DISPLAY=:99
	pgrep -f "Xvfb :99" >/dev/null || { Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 & sleep 2; }
fi

exec "$WINE" "$PY" -c "from rpyc.cli.rpyc_classic import ClassicServer; ClassicServer.run()" --host 127.0.0.1 --port "$MT5_BRIDGE_PORT"
