"""
Dream System MCP Server - 标准HTTP transport实现
让任意Agent可以通过MCP协议调用梦境系统

协议: MCP over HTTP (JSON-RPC 2.0)
默认端口: 8767
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# 添加dream-system到路径（注意：梦境系统在独立目录）
DREAM_ROOT = Path(__file__).parent.parent.parent / "dream-system"
sys.path.insert(0, str(DREAM_ROOT))

from service.config import DreamConfig
from service.dream import DreamService

MCP_VERSION = "2025-03-26"


class DreamMCPHandler(BaseHTTPRequestHandler):
    """MCP HTTP请求处理器"""
    
    dream_service = None  # 类变量，通过工厂函数设置
    
    def do_POST(self):
        """处理MCP JSON-RPC请求"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            request = json.loads(body)
            response = self._handle_rpc(request)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode('utf-8'))
        
        except json.JSONDecodeError:
            self._send_error(-32700, "Parse error")
        except Exception as e:
            self._send_error(-32603, f"Internal error: {e}")
    
    def _handle_rpc(self, request):
        """处理JSON-RPC请求"""
        method = request.get('method')
        params = request.get('params', {})
        req_id = request.get('id')
        
        if method == 'initialize':
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_VERSION,
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "dream-system-mcp",
                        "version": "1.0.0"
                    }
                }
            }
        
        elif method == 'tools/list':
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self._get_tools()
                }
            }
        
        elif method == 'tools/call':
            tool_name = params.get('name')
            arguments = params.get('arguments', {})
            
            try:
                result = self._call_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, indent=2)
                            }
                        ]
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
    
    def _get_tools(self):
        """返回可用工具列表"""
        return [
            {
                "name": "dream.review",
                "description": "对指定日期进行梦境复盘，提取情感记忆",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "复盘日期 (YYYY-MM-DD)，默认今天"
                        }
                    }
                }
            },
            {
                "name": "dream.recall",
                "description": "召回相关的情感记忆",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回数量，默认3"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "dream.write",
                "description": "记录一个新的梦境",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "梦境内容"
                        },
                        "date": {
                            "type": "string",
                            "description": "梦境日期 (YYYY-MM-DD)，默认今天"
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标签列表"
                        },
                        "mood": {
                            "type": "string",
                            "description": "醒来后的心情"
                        },
                        "lucidity": {
                            "type": "integer",
                            "description": "清醒度 1-5，默认3"
                        }
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "dream.search",
                "description": "搜索梦境记录",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词"
                        },
                        "days": {
                            "type": "integer",
                            "description": "搜索最近N天，默认30"
                        }
                    },
                    "required": ["keyword"]
                }
            },
            {
                "name": "dream.list",
                "description": "列出最近的梦境",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "列出最近N天，默认7"
                        }
                    }
                }
            }
        ]
    
    def _call_tool(self, tool_name, arguments):
        """调用工具"""
        if tool_name == 'dream.review':
            date_str = arguments.get('date')
            if date_str:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            else:
                date_obj = datetime.now()
            
            # 调用梦境系统的复盘功能
            result = self.dream_service.review_day(date_obj)
            
            if result and result.get('success'):
                return {
                    "success": True,
                    "date": str(result['date']),
                    "uri": result['uri'],
                    "message": result.get('message', '复盘完成')
                }
            else:
                return {
                    "success": False,
                    "error": result.get('error', 'unknown'),
                    "message": result.get('message', '复盘失败')
                }
        
        elif tool_name == 'dream.recall':
            query = arguments.get('query', '')
            limit = arguments.get('limit', 3)
            
            # 兼容召回：从事实库和梦境中检索
            results = []
            if hasattr(self, 'recall_service') and self.recall_service:
                facts = self.recall_service.search_facts(query)
                for f in facts[:limit]:
                    results.append({
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "content": f,
                        "relevance": 1.0,
                        "uri": ""
                    })
            
            # 若事实不足，从最近梦境补齐
            if len(results) < limit and hasattr(self, 'storage'):
                recent = self.storage.list_recent_dreams(days=30)
                for d in recent:
                    if len(results) >= limit:
                        break
                    preview = d.get('preview', '')
                    if not query or query in preview:
                        results.append({
                            "date": d['date'].strftime('%Y-%m-%d') if hasattr(d['date'], 'strftime') else str(d['date']),
                            "content": preview,
                            "relevance": 0.8,
                            "uri": d.get('uri', '')
                        })
            
            return {
                "success": True,
                "count": len(results),
                "memories": results
            }
        
        elif tool_name == 'dream.write':
            content = arguments.get('content', '')
            date_str = arguments.get('date')
            date_obj = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.now()
            
            # 调用存储层直接原子写入梦境复盘
            if hasattr(self, 'storage') and self.storage:
                try:
                    uri = self.storage.write_dream(date_obj, content)
                    return {
                        "success": True,
                        "uri": uri,
                        "date": date_obj.strftime("%Y-%m-%d"),
                        "message": "梦境记录成功"
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "error": str(e),
                        "message": "记录失败"
                    }
            else:
                return {
                    "success": False,
                    "error": "storage_unavailable",
                    "message": "存储组件不可用"
                }
        
        elif tool_name == 'dream.search':
            keyword = arguments.get('keyword', '')
            days = arguments.get('days', 30)
            
            matched = []
            if hasattr(self, 'storage') and self.storage:
                recent = self.storage.list_recent_dreams(days=days)
                for d in recent:
                    preview = d.get('preview', '')
                    if not keyword or keyword in preview:
                        matched.append({
                            "date": d['date'].strftime('%Y-%m-%d') if hasattr(d['date'], 'strftime') else str(d['date']),
                            "preview": preview,
                            "tags": [],
                            "uri": d.get('uri', '')
                        })
            return {
                "success": True,
                "count": len(matched),
                "results": matched
            }
        
        elif tool_name == 'dream.list':
            days = arguments.get('days', 7)
            
            entries = []
            if hasattr(self, 'storage') and self.storage:
                entries = self.storage.list_recent_dreams(days=days)
            
            return {
                "success": True,
                "count": len(entries),
                "dreams": [
                    {
                        "date": e['date'].strftime('%Y-%m-%d') if hasattr(e['date'], 'strftime') else str(e['date']),
                        "preview": e.get('preview', '')[:50] + "..." if len(e.get('preview', '')) > 50 else e.get('preview', ''),
                        "uri": e.get('uri', '')
                    } for e in entries
                ]
            }
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _send_error(self, code, message):
        """发送错误响应"""
        response = {
            "jsonrpc": "2.0",
            "error": {
                "code": code,
                "message": message
            }
        }
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Dream System MCP Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host address')
    parser.add_argument('--port', type=int, default=8767, help='Port number')
    parser.add_argument('--config', help='Config file path')
    
    args = parser.parse_args()
    
    # 加载配置
    if args.config:
        config_path = args.config
    else:
        # 使用dream-system的配置文件
        config_path = str(DREAM_ROOT / 'config.toml')
    
    # 初始化梦境服务（MCP模式：不依赖采集器，仅支持手动写入）
    config = DreamConfig(config_path)
    from service.storage import ObsidianStorage
    from service.collector import DreamCollector
    from service.filter import DEFAULT_FILTER_CONFIG
    
    storage = ObsidianStorage(config.vault_dir, config.vault_name)
    
    # 创建空采集器（MCP模式下不使用自动采集功能）
    collector = DreamCollector(
        l4_session_dirs=[],  # MCP模式不使用L4
        journal_dirs=[],  # MCP模式不使用日记
        include_group=False
    )
    
    dream_service = DreamService(storage, collector, DEFAULT_FILTER_CONFIG)
    from service.recall import DreamRecall
    recall_service = DreamRecall(storage)
    
    # 设置Handler的类变量
    DreamMCPHandler.dream_service = dream_service
    DreamMCPHandler.storage = storage
    DreamMCPHandler.recall_service = recall_service
    
    # 启动HTTP服务器
    server = HTTPServer((args.host, args.port), DreamMCPHandler)
    
    print(f"✓ Dream System MCP Server")
    print(f"  地址: http://{args.host}:{args.port}/mcp")
    print(f"  协议: MCP over HTTP (JSON-RPC 2.0)")
    print(f"  配置: {config.config_path}")
    print(f"  Vault: {config.vault_dir}/{config.vault_name}")
    print(f"  梦境目录: {config.dreams_dir}")
    print()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Server stopped")
        server.server_close()


if __name__ == '__main__':
    main()
