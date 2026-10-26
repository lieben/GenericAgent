#!/bin/bash
# 启动日记 MCP 服务

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
DIARY_DIR="$PROJECT_ROOT/diary-system"
CONFIG_FILE="$PROJECT_ROOT/config/diary_config.toml"
LOG_DIR="$PROJECT_ROOT/logs"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 检查配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Config not found: $CONFIG_FILE"
    echo "📋 Creating from example..."
    cp "$PROJECT_ROOT/config/diary_config.example.toml" "$CONFIG_FILE"
    echo "⚠️  Please edit $CONFIG_FILE and update vault_path"
    exit 1
fi

echo "📅 Starting Diary MCP Server..."
cd "$DIARY_DIR"

python3 -u mcp/diary_mcp_server.py \
    --config "$CONFIG_FILE" \
    --log-file "$LOG_DIR/diary_mcp.log" \
    2>&1 | tee -a "$LOG_DIR/diary_mcp.log"
