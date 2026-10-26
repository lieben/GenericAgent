#!/bin/bash
# 微信相关服务启动脚本（微信打开后手动执行）
# 优先使用 systemd user unit，只允许通过唯一 ga-wechat-group-monitor.service 启动，避免重复实例。
set -euo pipefail

UNIT=ga-wechat-group-monitor.service
BASE=/home/aimcafe/GA_Instances/xinghui-ga/plugins/wechat_multi_chat_rust
LOG=$BASE/runtime/wechat_services.log
BINARY=$BASE/target/release/wechat_multi_chat_rust
CONFIG=$BASE/config/config.json
PID_FILE=$BASE/runtime/rust_loop.pid

mkdir -p "$BASE/runtime"

if command -v systemctl >/dev/null 2>&1 && systemctl --user cat "$UNIT" >/dev/null 2>&1; then
    systemctl --user start "$UNIT"
    MAINPID=$(systemctl --user show "$UNIT" -p MainPID --value 2>/dev/null || true)
    if [ -n "${MAINPID:-}" ] && [ "$MAINPID" != "0" ]; then
        echo "$MAINPID" > "$PID_FILE"
        echo "已通过 systemd 启动/确认运行 $UNIT (PID=$MAINPID)"
    else
        echo "已请求 systemd 启动 $UNIT"
    fi
    exit 0
fi

# fallback：仅在没有 systemd unit 的环境使用 nohup。
if pgrep -f "$BINARY --config $CONFIG loop" >/dev/null 2>&1; then
    PID=$(pgrep -f "$BINARY --config $CONFIG loop" | head -n1)
    echo "$PID" > "$PID_FILE"
    echo "已在运行 (PID=$PID)"
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动 wechat_multi_chat_rust loop (fallback nohup)..." | tee -a "$LOG"
cd "$BASE"
DISPLAY=:1 nohup "$BINARY" --config "$CONFIG" loop >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"
echo "已启动 PID=$!"
