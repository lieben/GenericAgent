# ❓ 排查指南

## 问题 1：MCP 服务无法启动

**症状:** 运行启动脚本后没有反应或报错

**排查步骤:**

1. 检查 Python 版本
```bash
python3 --version  # 需要 3.10+
```

2. 检查依赖是否安装
```bash
cd diary-system
pip list | grep -E "fastapi|starlette|pydantic"
```

如果缺少，执行：
```bash
pip install -r requirements.txt
```

3. 查看日志
```bash
tail -50f logs/diary_mcp.log
# 或
tail -50f logs/dream_mcp.log
```

4. 测试端口是否被占用
```bash
lsof -iTCP:8766 -sTCP:LISTEN  # 日记
lsof -iTCP:8767 -sTCP:LISTEN  # 梦境
```

如果端口被占用，修改配置文件中的 `port` 字段。

---

## 问题 2：Agent 连接失败

**症状:** Agent 无法连接到 MCP 服务

**排查步骤:**

1. 确保两个服务都在运行
```bash
curl http://127.0.0.1:8766/health
curl http://127.0.0.1:8767/health
```

都应该返回 `{"status": "ok"}`

2. 检查 Obsidian Vault 路径是否正确
```bash
# 在配置文件中检查
cat config/diary_config.toml | grep vault_path
```

路径必须绝对路径且存在

3. 检查 Obsidian 文件夹是否创建
```bash
# 用你配置的 vault_path 替换
ls -la ~/Documents/Obsidian/LifeVault/Diary/
```

---

## 问题 3：写日记失败

**症状:** 调用 `diary.write` 返回错误

**排查步骤:**

1. 检查日记文件夹权限
```bash
chmod 755 ~/Documents/Obsidian/LifeVault/Diary/
```

2. 检查磁盘空间
```bash
df -h ~/Documents/Obsidian/
```

3. 查看具体错误信息
```bash
tail -20 logs/diary_mcp.log
```

---

## 问题 4：梦境复盘为空

**症状:** `dream.review` 返回无结果

**排查步骤:**

1. 检查会话数据路径
```bash
# 在配置文件中
cat config/dream_config.toml | grep sessions_dir
```

2. 确认路径中有会话文件
```bash
ls -la /path/to/sessions/
```

3. 查看日志
```bash
tail -50 logs/dream_mcp.log
```

---

## 问题 5：MemPalace 连接失败

**症状:** 启用了 MemPalace 但上传失败

**排查步骤:**

1. 检查 MemPalace 服务是否在线
```bash
curl http://192.168.1.126:8765/status
```

2. 检查 Bearer Token 是否正确
```bash
# 在配置文件中
cat config/dream_config.toml | grep token
```

3. 查看错误日志
```bash
grep -i "mempalace\|token" logs/dream_mcp.log
```

4. 临时禁用 MemPalace 进行测试
```bash
# 在配置中改为
enable_mempalace = false
```

---

## 常见错误码

| 状态码 | 含义 | 解决方案 |
|--------|------|--------|
| 404 | 文件或工具未找到 | 检查工具名称或文件路径 |
| 500 | 服务器错误 | 查看日志，重启服务 |
| 503 | 服务不可用 | 检查 MemPalace 连接 |
| 422 | 请求参数错误 | 检查参数类型和格式 |

---

## 获得更多帮助

- **GitHub Issues** - 提交问题报告
- **日志路径** - `logs/` 目录中的详细日志
- **配置验证** - 运行 `python3 validate_config.py`（可选）

---

**还有问题？** 检查日志、查看参数格式、确认网络连接。 ✨
