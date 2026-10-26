"""
Diary & Dream MCP Client Plugin
让GA通过MCP协议调用日记和梦境系统

配置：
  diary_mcp_url: 日记系统MCP地址 (默认 http://127.0.0.1:8766/mcp)
  dream_mcp_url: 梦境系统MCP地址 (默认 http://127.0.0.1:8767/mcp)
"""
import itertools
import json
import os
import urllib.request
import urllib.error

DEFAULT_DIARY_URL = "http://127.0.0.1:8766/mcp"
DEFAULT_DREAM_URL = "http://127.0.0.1:8767/mcp"
_id_counter = itertools.count(1)


class MCPClient:
    """通用MCP HTTP客户端"""
    
    def __init__(self, url):
        self.url = url.rstrip("/")
        self._initialized = False
    
    def _rpc(self, method, params=None, timeout=60):
        """发送JSON-RPC请求"""
        payload = {
            "jsonrpc": "2.0",
            "id": next(_id_counter),
            "method": method
        }
        if params:
            payload["params"] = params
        
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8')
                return json.loads(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8')[:300]
            raise RuntimeError(f"MCP HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"MCP server unreachable: {e.reason}")
    
    def initialize(self):
        """初始化MCP连接"""
        if self._initialized:
            return self
        
        result = self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "xinghui-ga", "version": "1.0"}
        })
        
        if "error" in result:
            raise RuntimeError(f"MCP initialize failed: {result['error']}")
        
        self._initialized = True
        return self
    
    def list_tools(self):
        """获取可用工具列表"""
        result = self._rpc("tools/list")
        if "error" in result:
            raise RuntimeError(f"tools/list failed: {result['error']}")
        return result.get("result", {}).get("tools", [])
    
    def call_tool(self, name, arguments=None):
        """调用工具"""
        result = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {}
        })
        
        if "error" in result:
            raise RuntimeError(f"Tool {name} failed: {result['error']}")
        
        # 提取文本内容
        content = result.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "")
            return json.loads(text) if text else {}
        
        return result.get("result", {})


# 全局客户端实例
_diary_client = None
_dream_client = None


def _get_diary_client():
    """获取日记系统客户端"""
    global _diary_client
    if _diary_client is None:
        url = os.environ.get("DIARY_MCP_URL", DEFAULT_DIARY_URL)
        _diary_client = MCPClient(url).initialize()
    return _diary_client


def _get_dream_client():
    """获取梦境系统客户端"""
    global _dream_client
    if _dream_client is None:
        url = os.environ.get("DREAM_MCP_URL", DEFAULT_DREAM_URL)
        _dream_client = MCPClient(url).initialize()
    return _dream_client


# ============= 导出的工具函数 =============

def diary_write(content, date=None):
    """写入日记
    
    Args:
        content: 日记内容（自然语言）
        date: 可选，日期 YYYY-MM-DD
    """
    client = _get_diary_client()
    args = {"content": content}
    if date:
        args["date"] = date
    return client.call_tool("diary.write", args)


def diary_read(date=None):
    """读取日记
    
    Args:
        date: 可选，日期 YYYY-MM-DD，默认今天
    """
    client = _get_diary_client()
    args = {"date": date} if date else {}
    return client.call_tool("diary.read", args)


def diary_search(keyword, days=30):
    """搜索日记
    
    Args:
        keyword: 搜索关键词
        days: 搜索最近N天
    """
    client = _get_diary_client()
    return client.call_tool("diary.search", {"keyword": keyword, "days": days})


def diary_list(days=7):
    """列出最近的日记
    
    Args:
        days: 最近N天
    """
    client = _get_diary_client()
    return client.call_tool("diary.list", {"days": days})


def dream_review(date=None):
    """复盘指定日期的情感记忆
    
    Args:
        date: 可选，日期 YYYY-MM-DD，默认今天
    """
    client = _get_dream_client()
    args = {"date": date} if date else {}
    return client.call_tool("dream.review", args)


def dream_recall(keyword, days=30):
    """召回相关情感记忆
    
    Args:
        keyword: 关键词
        days: 搜索天数
    """
    client = _get_dream_client()
    return client.call_tool("dream.recall", {"keyword": keyword, "days": days})


def dream_list(days=7):
    """列出最近的梦境复盘
    
    Args:
        days: 最近N天
    """
    client = _get_dream_client()
    return client.call_tool("dream.list", {"days": days})


# ============= GA插件钩子 =============

def llm_before(ga_ctx):
    """GA插件钩子：注册工具到模型上下文"""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "diary_write",
                "description": "写入今天的日记。接受自然语言描述，系统会自动格式化为日记格式。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "日记内容（自然语言）"
                        },
                        "date": {
                            "type": "string",
                            "description": "可选：指定日期 YYYY-MM-DD，默认今天"
                        }
                    },
                    "required": ["content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "diary_read",
                "description": "读取指定日期的日记",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "日期 YYYY-MM-DD，默认今天"
                        }
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "diary_search",
                "description": "搜索日记内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "搜索关键词"},
                        "days": {"type": "integer", "description": "搜索最近N天，默认30"}
                    },
                    "required": ["keyword"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "dream_review",
                "description": "复盘指定日期的情感记忆，提取关系事实候选",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天"}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "dream_recall",
                "description": "召回相关情感记忆（我们之前聊过类似的话题吗）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "关键词"},
                        "days": {"type": "integer", "description": "搜索天数，默认30"}
                    },
                    "required": ["keyword"]
                }
            }
        }
    ]
    
    # 注册工具
    for tool in tools:
        ga_ctx.register_tool(tool)
