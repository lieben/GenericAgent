#!/usr/bin/env python3
"""Standalone HTTP/OpenAI-compatible frontend for isolated roleplay sessions."""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.roleplay import engine, storage

UI = ROOT / "plugins" / "roleplay" / "ui.html"
MODEL_RE = re.compile(r"^roleplay/([^/]+)/([^/]+)$")
CONFIG_PATH = ROOT / "plugins" / "roleplay" / "config.json"


def get_configured_key() -> str:
    env_key = os.environ.get("ROLEPLAY_API_KEY", "").strip()
    if env_key:
        return env_key
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return str(cfg.get("api_key", "")).strip()
        except Exception:
            pass
    return ""


def set_configured_key(new_key: str):
    new_key = str(new_key).strip()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["api_key"] = new_key
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_session_by_model(raw_model: str) -> tuple[str, str] | None:
    raw_model = str(raw_model or "").strip()
    if not raw_model:
        return None
    # 1. 精确匹配 sessions 中的 model_id、model 或标准 roleplay/<slug>/<session_id>
    for x in storage.list_sessions():
        slug = x.get("slug")
        sid = x.get("session_id")
        m_id = x.get("model_id")
        m_name = x.get("model")
        candidates = {
            m_id,
            m_name,
            f"roleplay/{slug}/{sid}",
            f"{slug}/{sid}",
            f"roleplay-{slug}-{sid}",
            f"rp-{slug}-{sid}",
            f"{slug}-{sid}",
        }
        candidates.discard(None)
        candidates.discard("")
        if raw_model in candidates:
            return slug, sid

    # 2. 斜杠路径解析
    norm = raw_model
    if norm.startswith("roleplay/"):
        norm = norm[len("roleplay/"):]
    match = re.fullmatch(r"([^/]+)/([^/]+)", norm)
    if match:
        return match.group(1), match.group(2)

    return None


def body_json(handler: BaseHTTPRequestHandler) -> dict:
    size = int(handler.headers.get("Content-Length", "0") or 0)
    raw = handler.rfile.read(size) if size else b"{}"
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def message_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def last_user_text(messages) -> str:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = message_content_text(msg.get("content", "")).strip()
            if text:
                return text
    raise ValueError("a user message is required")


def extra_system_text(messages) -> str:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    parts = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") in {"system", "developer"}:
            text = message_content_text(msg.get("content", "")).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def bearer_token(header: str) -> str:
    prefix = "Bearer "
    if not header.startswith(prefix):
        return ""
    return header[len(prefix):].strip()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GARoleplay/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[roleplay-api] " + (fmt % args) + "\n")

    def send_bytes(self, status: int, data: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if content_type.startswith("text/html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, value):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def begin_sse_chat(self, model: str):
        chunk_id = "chatcmpl-roleplay"
        created = int(time.time())

        def write(value):
            payload = json.dumps(value, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(b": roleplay-ready\n\n")
        self.wfile.flush()
        write({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        return chunk_id, created, write

    def finish_sse_chat(self, model: str, chunk_id: str, created: int, write):
        write({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def error_json(self, status: int, exc: Exception):
        self.send_json(status, {"error": {"message": str(exc), "type": exc.__class__.__name__}})

    def is_authorized(self) -> bool:
        configured = get_configured_key()
        if not configured:
            return True
        token = bearer_token(self.headers.get("Authorization", ""))
        return hmac.compare_digest(token, configured)

    def require_authorized(self) -> bool:
        if self.is_authorized():
            return True
        self.send_json(401, {"error": {"message": "unauthorized"}})
        return False

    def do_OPTIONS(self):
        self.send_bytes(204, b"")

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self.send_bytes(200, UI.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/favicon.ico":
                self.send_bytes(204, b"", "image/x-icon")
                return
            if path == "/api/auth/status":
                configured = bool(get_configured_key())
                self.send_json(200, {"configured": configured})
                return
            if not self.require_authorized():
                return
            elif path == "/health":
                self.send_json(200, {"ok": True, "service": "isolated-roleplay"})
            elif path == "/roleplay/sessions":
                self.send_json(200, {"sessions": storage.list_sessions()})
            elif path == "/roleplay/npcs":
                self.send_json(200, {"npcs": storage.list_npcs()})
            elif path == "/v1/models":
                models = [
                    {
                        "id": x.get("model_id") or x.get("model") or f"roleplay/{x['slug']}/{x['session_id']}",
                        "object": "model",
                        "owned_by": "roleplay",
                        "created": int(time.time()),
                    }
                    for x in storage.list_sessions()
                ]
                self.send_json(200, {"object": "list", "data": models})
            elif path.startswith("/v1/models/"):
                mid = path[len("/v1/models/"):]
                target_session = None
                for x in storage.list_sessions():
                    cur_mid = x.get("model_id") or x.get("model") or f"roleplay/{x['slug']}/{x['session_id']}"
                    if mid == cur_mid or mid == f"{x['slug']}/{x['session_id']}":
                        target_session = x
                        break
                if target_session:
                    self.send_json(200, {
                        "id": target_session.get("model_id") or f"roleplay/{target_session['slug']}/{target_session['session_id']}",
                        "object": "model",
                        "owned_by": "roleplay",
                        "created": int(time.time()),
                    })
                else:
                    self.send_json(404, {"error": {"message": f"Model '{mid}' not found", "type": "invalid_request_error"}})
            else:
                match = re.fullmatch(r"/roleplay/sessions/([^/]+)/([^/]+)", path)
                if match:
                    slug, sid = match.groups()
                    self.send_json(200, storage.get_session(slug, sid))
                else:
                    self.send_json(404, {"error": {"message": "not found"}})
        except Exception as exc:
            self.error_json(500, exc)

    def do_DELETE(self):
        if not self.require_authorized():
            return
        try:
            path = urlparse(self.path).path
            match_npc = re.fullmatch(r"/roleplay/npcs/([^/]+)", path)
            if match_npc:
                nid = match_npc.group(1)
                deleted = storage.delete_npc(nid)
                if deleted:
                    self.send_json(200, {"ok": True, "deleted": True})
                else:
                    self.send_json(404, {"error": {"message": "NPC not found"}})
                return

            match = re.fullmatch(r"/roleplay/sessions/([^/]+)/([^/]+)", path)
            if not match:
                self.send_json(404, {"error": {"message": "not found"}})
                return
            slug, sid = match.groups()
            storage.delete_session(slug, sid)
            self.send_json(200, {"ok": True, "deleted": True, "slug": slug, "session_id": sid})
        except KeyError:
            self.send_json(404, {"error": {"message": "session not found"}})
        except Exception as exc:
            self.error_json(500, exc)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            data = body_json(self)
            if path == "/api/auth/setup":
                current_key = get_configured_key()
                if current_key:
                    self.send_json(400, {"error": {"message": "Key has already been set"}})
                    return
                key_val = str(data.get("key", "")).strip()
                if not key_val:
                    self.send_json(400, {"error": {"message": "Key cannot be empty"}})
                    return
                set_configured_key(key_val)
                self.send_json(200, {"ok": True, "message": "Key initialized"})
                return

            if not self.require_authorized():
                return

            if path == "/api/auth/update":
                key_val = str(data.get("key", "")).strip()
                if not key_val:
                    self.send_json(400, {"error": {"message": "Key cannot be empty"}})
                    return
                set_configured_key(key_val)
                self.send_json(200, {"ok": True, "message": "Key updated"})
                return

            if path == "/roleplay/npcs":
                nid = str(data.get("id", "")).strip()
                name = str(data.get("name", "")).strip()
                role = str(data.get("role", "")).strip()
                persona = str(data.get("persona", "")).strip()
                style = str(data.get("style", "")).strip()
                if not name:
                    self.send_json(400, {"error": {"message": "NPC name is required"}})
                    return
                saved = storage.save_npc(nid, name, role, persona, style)
                self.send_json(200, {"ok": True, "npc": saved})
                return
            if path == "/roleplay/sessions":
                result = storage.create_session(
                    data.get("slug", ""),
                    data.get("session_id", ""),
                    data.get("title", ""),
                    data.get("canon"),
                )
                self.send_json(201, result)
                return
            if path == "/v1/chat/completions":
                raw_model = str(data.get("model", "")).strip()
                resolved = resolve_session_by_model(raw_model)
                if not resolved:
                    raise ValueError(f"model must be 'roleplay/<story>/<session>', '<story>/<session>', or configured model_id, got: {data.get('model')}")
                slug, sid = resolved
                messages = data.get("messages")
                text = last_user_text(messages)
                extra_system = extra_system_text(messages)
                model_name = data.get("model")
                if data.get("stream"):
                    chunk_id, created, write = self.begin_sse_chat(data["model"])
                    stop_heartbeat = threading.Event()

                    def heartbeat_worker():
                        while not stop_heartbeat.wait(timeout=10.0):
                            try:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                            except Exception:
                                break

                    hb_thread = threading.Thread(target=heartbeat_worker, daemon=True)
                    hb_thread.start()
                    try:
                        for piece in engine.generate(slug, sid, text, extra_system=extra_system):
                            step = 12
                            for i in range(0, len(piece), step):
                                sub = piece[i:i+step]
                                write({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": data["model"], "choices": [{"index": 0, "delta": {"content": sub}, "finish_reason": None}]})
                    finally:
                        stop_heartbeat.set()
                        hb_thread.join(timeout=1.0)
                    self.finish_sse_chat(data["model"], chunk_id, created, write)
                    return
                if not data.get("stream"):
                    ans = engine.chat(slug, sid, text, extra_system=extra_system)
                    self.send_json(200, {
                        "id": "chatcmpl-roleplay",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": data["model"],
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": ans},
                            "finish_reason": "stop"
                        }],
                        "usage": {"prompt_tokens": 0, "completion_tokens": len(ans), "total_tokens": len(ans)}
                    })
                    return
            match = re.fullmatch(r"/roleplay/sessions/([^/]+)/([^/]+)/(chat|status|settings|bridge|checkpoint|undo-last-turn)", path)
            if not match:
                self.send_json(404, {"error": {"message": "not found"}})
                return
            slug, sid, action = match.groups()
            if action == "chat":
                self.send_json(200, {"reply": engine.chat(slug, sid, str(data.get("message", "")))})
            elif action == "status":
                self.send_json(200, storage.update_session(slug, sid, status=str(data.get("status", ""))))
            elif action == "settings":
                self.send_json(200, storage.update_session(slug, sid, canon=data.get("canon")))
            elif action == "bridge":
                self.send_json(200, storage.bridge_receipt(slug, sid))
            elif action == "undo-last-turn":
                result = storage.undo_last_turn(slug, sid)
                engine.reset_runtime(slug, sid)
                self.send_json(200, result)
            else:
                self.send_json(200, {"path": str(storage.checkpoint_l4(slug, sid, data.get("payload", {})))})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self.error_json(400, exc)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            traceback.print_exc()
            self.error_json(500, exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"roleplay frontend: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
