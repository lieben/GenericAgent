# 📦 交付打包清单

## 你需要给朋友的文件结构

```
diary-dream-system/
│
├── README.md                          ✅ 主文档（已生成）
├── QUICK_START.md                     ✅ 5分钟快速开始（已生成）
├── DEPLOYMENT.md                      ✅ 完整部署说明（已生成）
├── MEMPALACE_INTEGRATION.md           ✅ MemPalace集成指南（已生成）
├── API_DIARY.md                       ✅ 日记工具参考（已生成）
├── API_DREAM.md                       ✅ 梦境工具参考（已生成）
│
├── startup/
│   ├── start_diary_mcp.sh            ✅ 日记启动脚本（已生成）
│   ├── start_dream_mcp.sh            ✅ 梦境启动脚本（已生成）
│   └── start_both_mcp.sh             ✅ 一键启动脚本（已生成）
│
├── config/
│   ├── diary_config.example.toml     ✅ 日记配置模板（已生成）
│   └── dream_config.example.toml     ✅ 梦境配置模板（已生成）
│
├── diary-system/                     📌 需要复制
│   ├── mcp/
│   │   └── diary_mcp_server.py
│   ├── service/
│   │   ├── diary_service.py
│   │   ├── storage.py
│   │   └── recall.py
│   ├── requirements.txt
│   └── ...
│
└── dream-system/                     📌 需要复制
    ├── mcp/
    │   └── dream_mcp_server.py
    ├── service/
    │   ├── dream_service.py
    │   ├── storage.py
    │   └── ...
    ├── requirements.txt
    └── ...
```

---

## 步骤 1：准备源码

从本项目中复制：

```bash
cp -r ../projects/diary-system/ ./diary-dream-system/
cp -r ../projects/dream-system/ ./diary-dream-system/
```

---

## 步骤 2：创建配置目录并放入模板

```bash
mkdir -p diary-dream-system/config

cp ../config_diary_example.toml diary-dream-system/config/diary_config.example.toml
cp ../config_dream_example.toml diary-dream-system/config/dream_config.example.toml
```

---

## 步骤 3：创建启动脚本目录

```bash
mkdir -p diary-dream-system/startup

# 复制已生成的启动脚本（见上面的文件列表）
cp ../start_diary_mcp.sh diary-dream-system/startup/
cp ../start_dream_mcp.sh diary-dream-system/startup/
cp ../start_both_mcp.sh diary-dream-system/startup/

chmod +x diary-dream-system/startup/*.sh
```

---

## 步骤 4：打包成 ZIP

```bash
cd diary-dream-system/..
zip -r diary-dream-system.zip diary-dream-system/
# 大约 2-5MB
```

---

## 朋友收到后的操作

1. 解压 `diary-dream-system.zip`
2. 按照 `QUICK_START.md` 执行
3. 修改 `config/diary_config.toml` 和 `config/dream_config.toml`（主要是 Vault 路径）
4. 运行 `bash startup/start_both_mcp.sh`
5. 在 Agent 中配置 MCP 连接到 `http://127.0.0.1:8766` 和 `http://127.0.0.1:8767`

---

## 验证清单

- [ ] 所有源码文件完整复制
- [ ] 配置模板正确放置
- [ ] 启动脚本有执行权限（`chmod +x`）
- [ ] README 和文档都包含
- [ ] ZIP 文件大小合理（不超过 10MB）

---

**准备好了吗？** 现在可以打包发给朋友了！ 📮
