"""
Diary System MCP Server - 标准HTTP transport实现
让任意Agent可以通过MCP协议调用日记系统

协议: MCP over HTTP (JSON-RPC 2.0)
默认端口: 8766
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# 添加diary-system到路径
DIARY_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(DIARY_ROOT))

from service.config import DiaryConfig
from service.diary import DiaryService
from service.search import SimpleSearch

MCP_VERSION = "2025-03-26"


class DiaryMCPHandler(BaseHTTPRequestHandler):
    """MCP HTTP请求处理器"""
    
    diary_service = None  # 类变量，通过工厂函数设置
    search_service = None  # 搜索服务
    
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
                        "name": "diary-system-mcp",
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
                "name": "diary.write",
                "description": "写入今天的日记。接受自然语言描述，系统会自动格式化为日记格式。",
                "inputSchema": {
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
            },
            {
                "name": "diary.read",
                "description": "读取指定日期的日记",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "日期 YYYY-MM-DD，默认今天"
                        }
                    }
                }
            },
            {
                "name": "diary.search",
                "description": "搜索日记内容",
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
                "name": "diary.list",
                "description": "列出最近的日记条目",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "最近N天，默认7"
                        }
                    }
                }
            }
        ]
    
    def _call_tool(self, tool_name, arguments):
        """调用工具"""
        if tool_name == 'diary.write':
            content = arguments.get('content', '')
            date_str = arguments.get('date')
            date_obj = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.now()
            
            # 创建 DiaryEntry 对象
            from service.diary import DiaryEntry
            entry = DiaryEntry(
                date=date_obj,
                content=content,
                tags=arguments.get('tags', []),
                importance=arguments.get('importance', 3),
                mood=arguments.get('mood')
            )
            
            result = self.diary_service.write_diary(entry, overwrite=False)
            
            if result and result.get('success'):
                return {
                    "success": True,
                    "uri": result['uri'],
                    "date": result['date'],
                    "message": "日记写入成功"
                }
            else:
                return {
                    "success": False,
                    "error": result.get('error', 'unknown'),
                    "message": result.get('message', '写入失败')
                }
        
        elif tool_name == 'diary.read':
            date_str = arguments.get('date')
            date_obj = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.now()
            
            content = self.diary_service.read_diary(date_obj)
            return {
                "success": True,
                "date": date_obj.strftime('%Y-%m-%d'),
                "content": content
            }
        
        elif tool_name == 'diary.search':
            query = arguments.get('query', '')
            limit = arguments.get('limit', 10)
            
            results = self.search_service.search_diaries(query, limit=limit)
            return {
                "success": True,
                "count": len(results),
                "results": [
                    {
                        "date": r.get('date', ''),
                        "context": r.get('context', ''),
                        "tags": r.get('tags', []),
                        "uri": r.get('uri', '')
                    } for r in results
                ]
            }
        
        elif tool_name == 'diary.list':
            days = arguments.get('days', 7)
            entries = self.diary_service.list_recent_diaries(days)
            
            return {
                "success": True,
                "count": len(entries),
                "entries": [
                    {
                        "date": str(e.get('date', '')),
                        "preview": e.get('weekday', '') + ' - ' + ', '.join(e.get('tags', [])) if e.get('tags') else e.get('weekday', ''),
                        "uri": e['uri']
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
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")


def main():
    """启动MCP服务器"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Diary System MCP Server')
    parser.add_argument('--host', default='127.0.0.1', help='服务器地址')
    parser.add_argument('--port', type=int, default=8766, help='服务器端口')
    parser.add_argument('--config', default=None, help='配置文件路径')
    args = parser.parse_args()
    
    # 确定配置文件路径
    if args.config:
        config_path = args.config
    else:
        config_path = str(Path(__file__).parent.parent / 'config.toml')
    
    # 初始化日记服务
    config = DiaryConfig(config_path)
    from service.storage import ObsidianStorage
    storage = ObsidianStorage(config.vault_dir, config.diary_folder)
    diary_service = DiaryService(storage)
    
    # 初始化搜索服务
    from service.search import SimpleSearch
    search_service = SimpleSearch(storage)
    
    # 设置Handler的类变量
    DiaryMCPHandler.diary_service = diary_service
    DiaryMCPHandler.search_service = search_service
    
    # 启动HTTP服务器
    server = HTTPServer((args.host, args.port), DiaryMCPHandler)
    
    print(f"✓ Diary System MCP Server")
    print(f"  地址: http://{args.host}:{args.port}/mcp")
    print(f"  协议: MCP over HTTP (JSON-RPC 2.0)")
    print(f"  配置: {config.config_path}")
    print(f"\n可用工具: diary.write, diary.read, diary.search, diary.list")
    print(f"\n服务器启动中...")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器关闭")
        server.shutdown()


if __name__ == '__main__':
    main()
