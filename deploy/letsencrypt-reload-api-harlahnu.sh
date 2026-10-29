#!/bin/sh
set -eu

if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/api.harlahnu.com" ]; then
    /usr/bin/systemctl reload nginx
fi
