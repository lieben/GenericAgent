#!/bin/sh
set -eu
URL=${1:-http://127.0.0.1:8765}
KEY=${2:-${GA_API_KEY:-}}
printf '%s\n' '--- health ---'
curl -fsS "$URL/health"
printf '\n%s\n' '--- models ---'
curl -fsS "$URL/v1/models"
printf '\n%s\n' '--- authenticated chat ---'
if [ -z "$KEY" ]; then echo '未提供 API Key，跳过聊天接口检查'; exit 0; fi
curl -fsS "$URL/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"xavier-gemini-pro","messages":[{"role":"user","content":"请只回复API连接测试成功"}]}'
printf '\n'
