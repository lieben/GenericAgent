#!/bin/bash
set -euo pipefail
PLUGIN_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PID_FILE="$PLUGIN_DIR/wechat_private_monitor.pid"
if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${old_pid}" ] && [ "${old_pid}" != "$$" ] && kill -0 "${old_pid}" 2>/dev/null; then
    echo "{\"private_monitor_skip\": true, \"reason\": \"pidfile_alive\", \"pid\": ${old_pid}}" >&2
    exit 1
  fi
fi
printf '%s\n' "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
cd "$PLUGIN_DIR"
exec python3 -u "$PLUGIN_DIR/wechat_xavier_private_monitor.py" "$@"
