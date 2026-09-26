# parity-deriva in one container: the web service on 8731, its data in /data.
# The first start asks how people get in, what the server is for and where its
# data comes from, at /setup (the code to open it is in the container's log).
FROM python:3.12-slim

# openssl: the setup makes a certificate authority and the client certificates
RUN apt-get update \
	&& apt-get install -y --no-install-recommends openssl \
	&& rm -rf /var/lib/apt/lists/*

# the requirements alone first, so a change to the code does not reinstall them
COPY requirements.txt /app/parity_deriva/requirements.txt
RUN pip install --no-cache-dir -r /app/parity_deriva/requirements.txt

COPY . /app/parity_deriva

# the package is imported as parity_deriva, so its parent is on the path;
# PARITY_DERIVA_HOME is where getLogger() finds etc/logging.conf
ENV PYTHONPATH=/app \
	PARITY_DERIVA_HOME=/app/parity_deriva \
	PARITY_DERIVA_DATA_DIR=/data \
	PARITY_DERIVA_LOG_DIR=/data \
	PARITY_DERIVA_IMPORT_DIR=/data/import \
	PARITY_DERIVA_WIZARD=1 \
	PYTHONUNBUFFERED=1

# not root: uid 1000 owns /data, which a named volume copies on first use
RUN useradd --uid 1000 --create-home parity \
	&& mkdir -p /data/import \
	&& chown -R parity:parity /data \
	&& chmod +x /app/parity_deriva/docker/entrypoint.sh
USER parity
WORKDIR /app/parity_deriva

VOLUME /data
EXPOSE 8731
ENTRYPOINT ["/app/parity_deriva/docker/entrypoint.sh"]
