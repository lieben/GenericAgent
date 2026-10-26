#!/bin/bash
# 同时启动日记和梦境 MCP 服务

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
LOG_DIR="$PROJECT_ROOT/logs"

mkdir -p "$LOG_DIR"

echo "🚀 Starting both Diary and Dream MCP services..."
echo ""

# 后台启动两个服务
bash "$SCRIPT_DIR/start_diary_mcp.sh" &
DIARY_PID=$!

sleep 2

bash "$SCRIPT_DIR/start_dream_mcp.sh" &
DREAM_PID=$!

echo ""
echo "✅ Services started"
echo "   📅 Diary MCP: http://127.0.0.1:8766 (PID: $DIARY_PID)"
echo "   🌙 Dream MCP:  http://127.0.0.1:8767 (PID: $DREAM_PID)"
echo ""
echo "📋 Logs:"
echo "   - $LOG_DIR/diary_mcp.log"
echo "   - $LOG_DIR/dream_mcp.log"
echo ""
echo "🛑 To stop: kill $DIARY_PID $DREAM_PID"
echo ""

wait
