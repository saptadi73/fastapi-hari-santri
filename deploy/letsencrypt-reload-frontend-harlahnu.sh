#!/bin/sh
set -eu

if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/harlahnu.com" ]; then
    /usr/bin/systemctl reload nginx
fi
