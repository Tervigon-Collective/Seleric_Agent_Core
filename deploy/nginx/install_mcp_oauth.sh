#!/usr/bin/env bash
# Install the mcp.seleric.com nginx site (OAuth discovery + /oauth/ proxy) and reload.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/mcp.seleric.com"
DEST=/etc/nginx/sites-available/mcp.seleric.com
sudo cp "$DEST" "${DEST}.bak.$(date +%Y%m%d%H%M%S)"
sudo cp "$SRC" "$DEST"
sudo nginx -t
sudo systemctl reload nginx
echo "nginx reloaded with OAuth locations from $SRC"
