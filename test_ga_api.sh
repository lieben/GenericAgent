#!/bin/bash
# GA API 测试脚本

API_URL="http://localhost:8765"
API_KEY="${GA_API_KEY:-sk-wxxxx}"

echo "🧪 Testing GA API Server"
echo "========================"

# 1. 健康检查
echo -e "\n1️⃣  Health Check:"
curl -s "$API_URL/health" | python3 -m json.tool

# 2. 列出模型
echo -e "\n\n2️⃣  List Models:"
curl -s "$API_URL/v1/models" \
  -H "Authorization: Bearer $API_KEY" | python3 -m json.tool

# 3. 简单对话
echo -e "\n\n3️⃣  Simple Chat:"
curl -s "$API_URL/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "xavier-gemini-pro",
    "messages": [
      {"role": "user", "content": "你好，星回"}
    ]
  }' | python3 -m json.tool

echo -e "\n\n✅ Test completed!"
