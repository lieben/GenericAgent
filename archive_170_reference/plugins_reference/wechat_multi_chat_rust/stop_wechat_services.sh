#!/bin/bash
# 微信相关服务停止脚本
# 优先停止 systemd user unit，避免只杀 pidfile 导致托管实例自动拉起或误杀。
set -euo pipefail

UNIT=ga-wechat-group-monitor.service
BASE=/home/aimcafe/GA_Instances/xinghui-ga/plugins/wechat_multi_chat_rust
BINARY=$BASE/target/release/wechat_multi_chat_rust
CONFIG=$BASE/config/config.json
PID_FILE=$BASE/runtime/rust_loop.pid

if command -v systemctl >/dev/null 2>&1 && systemctl --user cat "$UNIT" >/dev/null 2>&1; then
    systemctl --user stop "$UNIT"
    rm -f "$PID_FILE"
    echo "已通过 systemd 停止 $UNIT"
    exit 0
fi

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "已停止 PID=$PID"
    else
        echo "进程不存在"
    fi
    rm -f "$PID_FILE"
else
    echo "未找到PID文件，尝试精确按本项目命令查找..."
    PIDS=$(pgrep -f "$BINARY --config $CONFIG loop" || true)
    if [ -n "$PIDS" ]; then
        kill $PIDS
        echo "已停止 PID=$PIDS"
    else
        echo "未找到进程"
    fi
fi
