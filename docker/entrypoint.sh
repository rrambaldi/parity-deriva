#!/bin/sh
# The container's start: what the setup wrote in /data/.env (ACCOUNTS, the
# archive, the OAuth secrets, a broker's keys) into the environment, then the
# web service. After the setup the service exits and Docker starts it again,
# so this runs again and reads the file as the setup left it.
set -e
if [ -f /data/.env ]; then
	set -a
	. /data/.env
	set +a
fi
mkdir -p /data/import
exec python /app/parity_deriva/scripts/web.py --host 0.0.0.0 --port 8731 "$@"
