# MCP调试关键发现

## 根本问题
**接口不匹配**: service层和MCP层的方法签名完全不一致

### Service层 (diary.py:127)
```python
def write_diary(self, entry: DiaryEntry, overwrite: bool = False)
```
期望参数：`(DiaryEntry对象, overwrite标志)`

### MCP层 (diary_mcp_server.py:183)
```python
result = self.diary_service.write_diary(date_obj, content)
```
实际传入：`(datetime对象, 字符串内容)`

## 错误链路
1. MCP传入 `(date_obj, content)`
2. Service层把 `date_obj` 当作 `entry` 参数
3. Service层把 `content` 当作 `overwrite` 参数（布尔值）
4. 138行调用 `self.storage.get_diary_path(entry.date)`
5. 此时 `entry` 实际是 `datetime对象`，调用 `.date` 属性获得的是 `datetime.date` 类本身（builtin方法）
6. Storage层第50行调用 `date.strftime()` 时，`date` 是类而非实例，报错

## 解决方案
需要在Service层添加一个便捷方法，或修改现有方法签名以支持两种调用方式：
1. 方案A：保持现有接口，MCP层先创建DiaryEntry对象再调用
2. 方案B：修改service接口支持直接传date+content参数
