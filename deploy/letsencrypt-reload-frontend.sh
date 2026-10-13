#!/bin/sh
set -eu

if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/harisantri2026.com" ]; then
    /usr/bin/systemctl reload nginx
fi
