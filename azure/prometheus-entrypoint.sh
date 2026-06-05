#!/bin/sh
# Replace the scrape target placeholder with the runtime env var,
# then exec the real Prometheus binary.
set -e

if [ -n "$PROM_REMOTE_WRITE_URL" ]; then
  CONFIG=/etc/prometheus/prometheus.yml
else
  CONFIG=/etc/prometheus/prometheus-sandbox.yml
fi

if [ -n "$SCRAPE_TARGET" ]; then
  sed -i "s|__SCRAPE_TARGET__|${SCRAPE_TARGET}|g" "$CONFIG"
fi

if [ -n "$PROM_REMOTE_WRITE_URL" ]; then
  sed -i "s|__REMOTE_WRITE_URL__|${PROM_REMOTE_WRITE_URL}|g" "$CONFIG"
fi

exec /bin/prometheus \
  --config.file="$CONFIG" \
  --storage.tsdb.retention.time=2h \
  --storage.tsdb.path=/prometheus \
  --web.listen-address=0.0.0.0:9090 \
  --web.enable-remote-write-receiver \
  --enable-feature=remote-write-receiver \
  --log.level=warn
