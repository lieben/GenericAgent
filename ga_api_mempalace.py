"""Thin MemPalace MCP client for the Xavier API encapsulation.

Talks to the friend's NAS MemPalace over MCP streamable HTTP (JSON-RPC 2.0).
This module is the telephone handset only — the palace itself (data volume,
compose, token) is configured on NAS, not in this repo.

  URL:   MEMPALACE_MCP_URL          → default http://192.168.1.126:8765/mcp
  Token: MEMPALACE_MCP_HTTP_TOKEN   → else keychain 'mempalace_http_token'
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


def _app_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here) if os.path.basename(here) == "plugins" else here


def _resolve_token():
    """环境变量优先，其次 keychain（memory/keychain.py）。仓库不存密钥。"""
    t = os.environ.get("MEMPALACE_MCP_HTTP_TOKEN", "").strip()
    if t:
        return t
    try:
        mem_dir = os.path.join(_app_root(), "memory")
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
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 202:
                    return {}
                return _parse_body(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError("MCP HTTP %s for %s: %s" % (e.code, method, detail)) from None
        except urllib.error.URLError as e:
            raise RuntimeError("MCP server unreachable at %s: %s" % (self.url, e.reason)) from None

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
        """调用 MemPalace 工具，返回文本结果（或错误 dict）。"""
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
    global _client
    if _client is None:
        _client = MemPalaceMCPClient()
    return _client
