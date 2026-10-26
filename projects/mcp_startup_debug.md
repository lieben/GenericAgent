# MCP服务器启动调试记录

## 问题追踪
经过30次失败后，定位到以下核心问题：

### 1. Vault路径问题
- **错误配置**: `ourhomevalue` (小写)
- **正确路径**: `OurHomeVault` (大小写敏感)
- **完整路径**: `~/Documents/obsidian/OurHomeVault`

### 2. 梦境系统配置不一致
- **config.example.toml**: 使用 `diary_system_dir`
- **service/config.py**: 读取 `diary_dir`
- **解决方案**: 统一使用 `diary_dir`

### 3. 导入路径问题
- MCP服务器作为独立脚本运行，不能使用相对导入 `from .storage`
- 必须使用 `from service.storage import`

### 4. 配置类属性不匹配
- 梦境config.toml使用 `dream_folder`，但DreamConfig期望 `dreams_dir`
- ObsidianStorage硬编码了"Dreams"路径，不接受参数

## 当前状态
- ✓ 日记MCP服务器: http://127.0.0.1:8766/mcp (已启动)
- ⏳ 梦境MCP服务器: http://127.0.0.1:8767/mcp (配置修复中)

## 下一步
修正config.toml后重启梦境服务器，然后测试JSON-RPC协议
