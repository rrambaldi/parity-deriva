#!/bin/sh
# A terminal for one more MT5 account: a portable copy of the installed one
# under C:\MT5\<name>, with algo trading switched on, and the MT5_TERMINALS
# entry that points a session at it. A terminal holds one login at a time,
# so every account needs its own - see lib/mt5.terminals.
#
#     scripts/mt5_add_account.sh acc2 18813 $PARITY_DERIVA_DATA_DIR/mt5/mq2.txt
#
# then paste the entry it prints into MT5_TERMINALS (etc/settings.py) and
# start its bridge: scripts/mt5_bridge.sh 18813
set -e
NAME=$1 PORT=$2 CREDENTIALS=$3
if [ -z "$NAME" ] || [ -z "$PORT" ] || [ -z "$CREDENTIALS" ]; then
	echo "usage: $0 <name> <bridge port> <credentials file>" >&2
	exit 2
fi
case "$NAME" in *[!A-Za-z0-9_-]*) echo "name: letters, digits, - and _ only" >&2; exit 2;; esac
MT5_WINE_DIR=${MT5_WINE_DIR:-${PARITY_DERIVA_DATA_DIR:-$HOME/DATA-dev}/mt5}
C="$MT5_WINE_DIR/prefix/drive_c"
SOURCE="$C/Program Files/MetaTrader 5"
TARGET="$C/MT5/$NAME"
if [ -e "$TARGET" ]; then echo "$TARGET exists already" >&2; exit 1; fi
mkdir -p "$TARGET"
# the program, not the first account's data: no logs, no bases, no profile
for f in "$SOURCE"/*.exe "$SOURCE"/*.dll; do [ -e "$f" ] && cp "$f" "$TARGET/"; done
mkdir -p "$TARGET/Config"
cp "$SOURCE/Config/servers.dat" "$TARGET/Config/" 2>/dev/null || true 
# the licence accepted once: without it the terminal waits on its dialog
cp "$SOURCE/Config/terminal.lic" "$TARGET/Config/" 2>/dev/null || true
# [Experts] Enabled=1 is the Algo Trading button: off, every order is refused
python3 - "$TARGET/Config/common.ini" <<'PY'
import sys
open(sys.argv[1], 'w', encoding='utf-16', newline='').write(
    '[Experts]\r\nAllowLiveTrading=1\r\nAllowDllImport=0\r\nEnabled=1\r\nAccount=0\r\nProfile=0\r\n')
PY
echo "terminal ready in $TARGET"
echo "MT5_TERMINALS entry:"
echo "    {'credentials': '$CREDENTIALS', 'bridge': '127.0.0.1:$PORT',"
echo "     'terminal': 'C:\\\\MT5\\\\$NAME\\\\terminal64.exe', 'portable': True},"
