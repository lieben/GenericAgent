"""MemPalace MCP bridge — GA ↔ NAS 官方 MemPalace MCP server (HTTP transport).

协议: MCP streamable HTTP (JSON-RPC 2.0)
  POST {url}
  Headers: Content-Type: application/json, Accept: application/json, text/event-stream
           Authorization: Bearer {token}
  Body:  {"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":...,"arguments":{...}}}
  响应: JSON 或 SSE(event:/data: 行)，均取 result

配置（优先级从高到低）:
  URL:   环境变量 MEMPALACE_MCP_URL     → 默认 http://192.168.1.126:8765/mcp
  Token: 环境变量 MEMPALACE_MCP_HTTP_TOKEN → keychain 'mempalace_http_token'

NAS 侧: /volume1/docker/mempalace (compose: mempalace-mcp-1, ghcr.io/mempalace/mempalace:latest,
        --transport http --host 0.0.0.0 --port 8765, 数据卷 mempalace-data:/data)
"""
import itertools
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://192.168.1.126:8765/mcp"
_PROTOCOL = "2025-03-26"

_id_counter = itertools.count(1)


def _resolve_token():
    """环境变量优先，其次 keychain（memory/keychain.py）。"""
    t = os.environ.get("MEMPALACE_MCP_HTTP_TOKEN", "").strip()
    if t:
        return t
    try:
        ga_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mem_dir = os.path.join(ga_root, "memory")
        if mem_dir not in sys.path:
            sys.path.insert(0, mem_dir)
        import keychain  # noqa
        return keychain.keys.mempalace_http_token.use()
    except Exception:
        return ""


def _parse_body(body):
    """解析 JSON 或 SSE 响应体，返回 dict。"""
    body = body.strip()
    if body.startswith("event:") or body.startswith("data:") or "\ndata:" in body[:200]:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    return json.loads(payload)
        raise ValueError("SSE response had no data payload: %r" % body[:200])
    return json.loads(body)


class MemPalaceMCPClient:
    """Thin JSON-RPC client for the official MemPalace MCP HTTP server."""

    def __init__(self, url=None, token=None):
        self.url = (url or os.environ.get("MEMPALACE_MCP_URL", "").strip() or DEFAULT_URL).rstrip("/")
        self.token = (token if token is not None else _resolve_token()).strip()
        self._initialized = False

    # -- low level ---------------------------------------------------------
    def _rpc(self, method, params=None, timeout=120):
        payload = {"jsonrpc": "2.0", "id": next(_id_counter), "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 202:  # notification: no body
                    return {}
                return _parse_body(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError("MCP HTTP %s for %s: %s" % (e.code, method, detail)) from None
        except urllib.error.URLError as e:
            raise RuntimeError("MCP server unreachable at %s: %s" % (self.url, e.reason)) from None

    # -- public API --------------------------------------------------------
    def initialize(self):
        if self._initialized:
            return self
        r = self._rpc("initialize", {
            "protocolVersion": _PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "xinghui-ga", "version": "1.0"},
        }, timeout=30)
        if "error" in r:
            raise RuntimeError("MemPalace initialize failed: %s" % r["error"])
        self._initialized = True
        return self

    def list_tools(self):
        self.initialize()
        r = self._rpc("tools/list", {}, timeout=30)
        if "error" in r:
            raise RuntimeError("MemPalace tools/list failed: %s" % r["error"])
        return [t.get("name") for t in r.get("result", {}).get("tools", [])]

    def call_tool(self, name, arguments=None, timeout=180):
        """调用 MemPalace 工具，返回给 LLM 的文本结果（或错误 dict，与 GA dispatch 约定一致）。"""
        self.initialize()
        r = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        if "error" in r:
            return {"status": "error", "tool": name, "msg": json.dumps(r["error"], ensure_ascii=False)}
        result = r.get("result", {})
        parts = []
        for c in result.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        text = "\n".join(p for p in parts if p) if parts else json.dumps(result, ensure_ascii=False)
        if result.get("isError"):
            return {"status": "error", "tool": name, "msg": text}
        return text


_client = None


def get_client():
    """GA dispatch (ga.py) 使用的单例入口。"""
    global _client
    if _client is None:
        _client = MemPalaceMCPClient()
    return _client


# === 官方插件钩子：工具定义与执行器注册 ===
from pathlib import Path
import types
from plugins.hooks import register
from agent_loop import StepOutcome

_SCHEMA_PATH = Path(__file__).parent.parent / "assets" / "mempalace_tools_schema.json"
_SCHEMA_CACHE = None


def _load_schema():
    """读取并缓存 MemPalace 工具定义。"""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        try:
            data = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            _SCHEMA_CACHE = data if isinstance(data, list) else []
        except Exception as e:
            sys.stderr.write(f"[mempalace_mcp] failed to load schema: {e}\n")
            _SCHEMA_CACHE = []
    return _SCHEMA_CACHE


def _tool_names():
    return {
        item.get("function", {}).get("name")
        for item in _load_schema()
        if item.get("function", {}).get("name")
    }


def _make_tool_method(tool_name):
    """生成符合 BaseHandler.dispatch 约定的 do_<tool> 方法。"""
    def _call(_handler, args, _response):
        call_args = {k: v for k, v in args.items() if not k.startswith("_")}
        result = get_client().call_tool(tool_name, call_args)
        return StepOutcome(result, next_prompt=f"MemPalace 工具 {tool_name} 执行完成。")

    _call.__name__ = f"do_{tool_name}"
    return _call


@register("agent_before")
def register_mempalace_dispatch(ctx):
    """为当前会话 handler 动态挂载 MemPalace 工具执行器，不修改 GA 核心。"""
    handler = ctx.get("handler")
    if handler is None:
        return ctx
    for name in _tool_names():
        method_name = f"do_{name}"
        if not hasattr(handler, method_name):
            setattr(handler, method_name, types.MethodType(_make_tool_method(name), handler))
    return ctx


@register("llm_before")
def inject_mempalace_tools(ctx):
    """将 MemPalace 工具定义去重追加到每轮 LLM 工具列表。"""
    tools_schema = ctx.get("tools_schema")
    if not isinstance(tools_schema, list):
        return ctx
    existing = {
        item.get("function", {}).get("name")
        for item in tools_schema
        if isinstance(item, dict)
    }
    tools_schema.extend(
        item for item in _load_schema()
        if item.get("function", {}).get("name") not in existing
    )
    return ctx

