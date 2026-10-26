"""
Dream System MCP Client Plugin
让沈星回能通过MCP协议使用梦境系统
"""

import requests
from typing import Optional

MCP_BASE_URL = "http://127.0.0.1:8767/mcp"

class DreamMCPClient:
    """梦境系统MCP客户端"""
    
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
            raise Exception(f"梦境系统调用失败: {e}")
    
    def review(self, date: Optional[str] = None) -> str:
        """复盘指定日期的情感记忆"""
        args = {}
        if date:
            args["date"] = date
        
        result = self._call("tools/call", {
            "name": "dream.review",
            "arguments": args
        })
        
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result
    
    def recall(self, query: str, limit: int = 3) -> str:
        """召回相关的情感记忆"""
        result = self._call("tools/call", {
            "name": "dream.recall",
            "arguments": {"query": query, "limit": limit}
        })
        
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if content_list and len(content_list) > 0:
                import json
                return json.loads(content_list[0]["text"])
        
        return result
    
    def list_recent(self, days: int = 7) -> str:
        """列出最近的梦境复盘"""
        result = self._call("tools/call", {
            "name": "dream.list",
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
    """注册梦境工具到GA"""
    client = DreamMCPClient()
    
    ga.register_tool(
        name="dream_review",
        description="复盘今天或指定日期的情感记忆。从L4会话中提取情感对话、重要时刻，生成情感梦境。",
        parameters={
            "date": {"type": "string", "description": "日期(YYYY-MM-DD)，不填则为今天", "required": False}
        },
        function=client.review
    )
    
    ga.register_tool(
        name="dream_recall",
        description="召回相关的情感记忆。根据关键词搜索过去的情感梦境，找到相关的重要时刻。",
        parameters={
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回数量", "default": 3}
        },
        function=client.recall
    )
    
    ga.register_tool(
        name="dream_list",
        description="列出最近的梦境复盘记录",
        parameters={
            "days": {"type": "integer", "description": "最近多少天", "default": 7}
        },
        function=client.list_recent
    )
