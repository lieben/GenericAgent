#!/usr/bin/env python3
"""Dream System MCP Server - HTTP transport
协议: MCP over HTTP (JSON-RPC 2.0) | 默认端口: 8767
"""
import json, sys
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).parent.parent))
from service.config import DreamConfig
from service.dream import DreamService
from service.recall import DreamRecall
from service.storage import ObsidianStorage
from service.collector import DreamCollector
from service.analyzer import DreamAnalyzer
from service.filter import DEFAULT_FILTER_CONFIG

MCP_VERSION = "2025-03-26"

class DreamMCPHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, dream_service=None, recall_service=None, **kwargs):
        self.dream_service = dream_service
        self.recall_service = recall_service
        super().__init__(*args, **kwargs)
    
    def do_POST(self):
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
            response = self._handle_rpc(json.loads(body))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            self._send_error(-32603, str(e))
    
    def _handle_rpc(self, req):
        method, params, rid = req.get('method'), req.get('params', {}), req.get('id')
        
        if method == 'initialize':
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": MCP_VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "dream-system-mcp", "version": "1.0.0"}}}
        
        if method == 'tools/list':
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": "dream.review", "description": "复盘指定日期情感记忆",
                 "inputSchema": {"type": "object", "properties": {
                     "date": {"type": "string", "description": "YYYY-MM-DD"}}}},
                {"name": "dream.recall", "description": "召回相关情感记忆",
                 "inputSchema": {"type": "object", "properties": {
                     "keyword": {"type": "string"}, "days": {"type": "integer"}},
                     "required": ["keyword"]}},
                {"name": "dream.search", "description": "搜索梦境记录",
                 "inputSchema": {"type": "object", "properties": {
                     "keyword": {"type": "string"}, "days": {"type": "integer"}}}},
                {"name": "dream.list", "description": "列出最近复盘",
                 "inputSchema": {"type": "object", "properties": {
                     "days": {"type": "integer"}}}}
            ]}}
        
        if method == 'tools/call':
            result = self._call_tool(params.get('name'), params.get('arguments', {}))
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
        
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown: {method}"}}
    
    def _call_tool(self, name, args):
        if name == 'dream.review':
            date = datetime.strptime(args.get('date'), '%Y-%m-%d') if args.get('date') else datetime.now()
            r = self.dream_service.review_day(date)
            return {"success": True, "date": date.strftime('%Y-%m-%d'), "uri": r.uri,
                   "total_messages": r.analysis.total_messages, "facts_count": r.facts_count}
        
        if name == 'dream.recall':
            # 支持 keyword 召回
            kw = args.get('keyword') or args.get('query', '')
            days = args.get('days', 30)
            results = self.recall_service.search_by_keyword(kw, days=days)
            return {"success": True, "count": len(results), "results": [
                {"date": r.date.strftime('%Y-%m-%d') if hasattr(r.date, 'strftime') else str(r.date), "snippet": r.content_snippet, "uri": r.dream_uri}
                for r in results]}
        
        if name == 'dream.search':
            kw = args.get('keyword') or args.get('query', '')
            days = args.get('days', 30)
            results = self.recall_service.search_by_keyword(kw, days=days)
            return {"success": True, "count": len(results), "results": [
                {"date": r.date.strftime('%Y-%m-%d') if hasattr(r.date, 'strftime') else str(r.date), "snippet": r.content_snippet, "uri": r.dream_uri}
                for r in results]}
        
        if name == 'dream.list':
            dreams = self.dream_service.list_recent(args.get('days', 7))
            return {"success": True, "count": len(dreams), "dreams": [
                {"date": d['date'].strftime('%Y-%m-%d'), "preview": d['preview'], "uri": d['uri']}
                for d in dreams]}
        
        raise ValueError(f"Unknown tool: {name}")
    
    def _send_error(self, code, msg):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": code, "message": msg}}).encode())

def create_handler(dream, recall):
    return lambda *a, **k: DreamMCPHandler(*a, dream_service=dream, recall_service=recall, **k)

def main():
    import argparse
    p = argparse.ArgumentParser(description='Dream System MCP Server')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8767)
    p.add_argument('--config', default=None)
    args = p.parse_args()
    
    # 确定配置文件路径
    if args.config:
        config_path = args.config
    else:
        # 默认使用服务器脚本所在目录的父目录下的config.toml
        config_path = str(Path(__file__).parent.parent / 'config.toml')
    
    cfg = DreamConfig(config_path)
    storage = ObsidianStorage(
        cfg.vault_dir,
        cfg.vault_name,
        cfg.dreams_dir,
        cfg.fact_candidates_file,
        cfg.confirmed_facts_file,
        cfg.latest_context_file,
    )
    collector = DreamCollector(cfg.l4_sessions_dir, cfg.diary_dir)
    analyzer = DreamAnalyzer()
    dream_service = DreamService(storage, collector, DEFAULT_FILTER_CONFIG)
    recall_service = DreamRecall(storage)
    
    srv = HTTPServer((args.host, args.port), create_handler(dream_service, recall_service))
    
    print(f"✓ Dream System MCP Server\n  http://{args.host}:{args.port}/mcp\n  Tools: dream.review, dream.recall, dream.list\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器关闭")

if __name__ == '__main__':
    main()
