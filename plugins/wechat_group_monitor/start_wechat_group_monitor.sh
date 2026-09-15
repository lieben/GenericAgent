#!/bin/bash
set -euo pipefail
PLUGIN_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PID_FILE="$PLUGIN_DIR/wechat_group_monitor.pid"
# WeFlow credentials are stored in this plugin's own wechat_monitor_config.json.
printf '%s\n' "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
cd "$PLUGIN_DIR"
exec python3 -u "$PLUGIN_DIR/wechat_xavier_group_monitor.py" "$@"
