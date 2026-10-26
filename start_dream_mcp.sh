#!/bin/bash
# 启动梦境 MCP 服务

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
DREAM_DIR="$PROJECT_ROOT/dream-system"
CONFIG_FILE="$PROJECT_ROOT/config/dream_config.toml"
LOG_DIR="$PROJECT_ROOT/logs"

mkdir -p "$LOG_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Config not found: $CONFIG_FILE"
    echo "📋 Creating from example..."
    cp "$PROJECT_ROOT/config/dream_config.example.toml" "$CONFIG_FILE"
    echo "⚠️  Please edit $CONFIG_FILE and update vault_path"
    exit 1
fi

echo "🌙 Starting Dream MCP Server..."
cd "$DREAM_DIR"

python3 -u mcp/dream_mcp_server.py \
    --config "$CONFIG_FILE" \
    --log-file "$LOG_DIR/dream_mcp.log" \
    2>&1 | tee -a "$LOG_DIR/dream_mcp.log"
