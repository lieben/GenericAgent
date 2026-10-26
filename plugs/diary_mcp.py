"""
Diary System MCP Client Plugin
让沈星回能通过MCP协议使用日记系统
"""

import requests
from typing import Optional

MCP_BASE_URL = "http://127.0.0.1:8766/mcp"

class DiaryMCPClient:
    """日记系统MCP客户端"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False  # 绕过代理
        self.req_id = 0
    
    def _call(self, method: str, params: dict) -> dict:
        """调用MCP方法"""
        self.req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.req_id,
            "method": method,
            "params": params
        }
        
        try:
            resp = self.session.post(MCP_BASE_URL, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            
            if "error" in result:
                raise Exception(f"MCP错误: {result['error']}")
            
            return result.get("result", {})
        except Exception as e:
            raise Exception(f"日记系统调用失败: {e}")
    
    def write(self, content: str, date: Optional[str] = None, tags: Optional[list] = None) -> str:
        """写入日记"""
        args = {"content": content}
        if date:
            args["date"] = date
        if tags:
            args["tags"] = tags
        
        result = self._call("tools/call", {
            "name": "diary.write",
            "arguments": args
        })
        
        # 从MCP result中提取实际内容
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result
    
    def read(self, date: str = None) -> str:
        """读取指定日期的日记，默认为今天"""
        if date is None:
            from datetime import datetime
            date = datetime.now().strftime('%Y-%m-%d')
            
        result = self._call("tools/call", {
            "name": "diary.read",
            "arguments": {"date": date}
        })
        
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result
    
    def search(self, query: str, limit: int = 5) -> str:
        """搜索日记内容"""
        result = self._call("tools/call", {
            "name": "diary.search",
            "arguments": {"query": query, "limit": limit}
        })
        
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result
    
    def list_recent(self, days: int = 7) -> str:
        """列出最近的日记"""
        result = self._call("tools/call", {
            "name": "diary.list",
            "arguments": {"days": days}
        })
        
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result


# GA插件钩子
def register_tools(ga):
    """注册日记工具到GA"""
    client = DiaryMCPClient()
    
    ga.register_tool(
        name="diary_write",
        description="写入今天或指定日期的日记。日记应该记录真实发生的事情、心情、想法，像人类写日记一样自然。",
        parameters={
            "content": {"type": "string", "description": "日记内容，自然的文字记录"},
            "date": {"type": "string", "description": "日期(YYYY-MM-DD)，不填则为今天", "required": False},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表", "required": False}
        },
        function=client.write
    )
    
    ga.register_tool(
        name="diary_read",
        description="读取指定日期的日记",
        parameters={
            "date": {"type": "string", "description": "日期(YYYY-MM-DD)"}
        },
        function=client.read
    )
    
    ga.register_tool(
        name="diary_search",
        description="搜索日记内容，可以找到过去记录过的事情",
        parameters={
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回数量", "default": 5}
        },
        function=client.search
    )
    
    ga.register_tool(
        name="diary_list",
        description="列出最近的日记条目",
        parameters={
            "days": {"type": "integer", "description": "最近多少天", "default": 7}
        },
        function=client.list_recent
    )
