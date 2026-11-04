#!/usr/bin/env python3
"""FastAPI / Uvicorn frontend for isolated roleplay sessions with robust anti-524 SSE streaming."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.roleplay import engine, storage

UI = ROOT / "plugins" / "roleplay" / "ui.html"
CONFIG_PATH = ROOT / "plugins" / "roleplay" / "config.json"

app = FastAPI(title="GARoleplayAPI", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _clean_api_response(text: str) -> str:
    """与 ga_api_server 对齐：对输出文本做最后兜底清洗，剔除未闭合或漏网的思维链标签及前导草稿。"""
    value = str(text or "")
    hidden_tags = (
        r"summary|think|thinking|thought|analysis|reasoning|reasoning_content|"
        r"function_calls?|function_results?|tool_calls?|tool_results?"
    )
    token_re = re.compile(
        rf"<\s*(/?)\s*({hidden_tags})\b[^>]*>", re.IGNORECASE
    )
    chunks, stack, cursor = [], [], 0
    for match in token_re.finditer(value):
        if not stack:
            chunks.append(value[cursor:match.start()])
        name = match.group(2).lower()
        if match.group(1):
            if name in stack:
                index = len(stack) - 1 - stack[::-1].index(name)
                del stack[index:]
        elif not re.search(r"/\s*>$", match.group(0)):
            stack.append(name)
        cursor = match.end()
    if not stack:
        chunks.append(value[cursor:])
    value = "".join(chunks)
    value = re.sub(
        rf"<\s*/?\s*(?:{hidden_tags})\b[^>]*\Z", "", value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def resolve_session_by_model(raw_model: str) -> tuple[str, str] | None:
    raw_model = str(raw_model or "").strip()
    if not raw_model:
        return None
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

    norm = raw_model
    if norm.startswith("roleplay/"):
        norm = norm[len("roleplay/"):]
    match = re.fullmatch(r"([^/]+)/([^/]+)", norm)
    if match:
        return match.group(1), match.group(2)

    return None


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


def check_auth(authorization: Optional[str] = None):
    expected = get_configured_key()
    if not expected:
        return True
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": {"message": "unauthorized"}})
    prefix = "Bearer "
    token = authorization[len(prefix):].strip() if authorization.startswith(prefix) else authorization.strip()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail={"error": {"message": "unauthorized"}})
    return True


def _sse_headers() -> Dict[str, str]:
    return {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _sse_data(payload: Optional[Dict[str, Any]]) -> str:
    if payload is None:
        return "data: [DONE]\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _openai_chat_chunk(chat_id: str, created: int, model: str, delta: Dict[str, Any],
                       finish_reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


@app.get("/")
@app.get("/index.html")
async def get_index():
    if not UI.is_file():
        raise HTTPException(status_code=404, detail="UI file not found")
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    return HTMLResponse(content=UI.read_text(encoding="utf-8"), headers=headers)


@app.get("/favicon.ico")
async def get_favicon():
    return Response(status_code=204)


@app.get("/api/auth/status")
async def get_auth_status():
    configured = bool(get_configured_key())
    return {"has_key": configured, "configured": configured}


@app.get("/health")
async def get_health():
    return {"ok": True}


@app.post("/api/auth/setup")
async def post_auth_setup(req: Request):
    data = await req.json()
    current_key = get_configured_key()
    if current_key:
        raise HTTPException(status_code=400, detail={"error": {"message": "Key has already been set"}})
    key_val = str(data.get("key", "")).strip()
    if not key_val:
        raise HTTPException(status_code=400, detail={"error": {"message": "Key cannot be empty"}})
    set_configured_key(key_val)
    return {"ok": True, "message": "Key initialized"}


@app.post("/api/auth/update")
async def post_auth_update(req: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    data = await req.json()
    key_val = str(data.get("key", "")).strip()
    if not key_val:
        raise HTTPException(status_code=400, detail={"error": {"message": "Key cannot be empty"}})
    set_configured_key(key_val)
    return {"ok": True, "message": "Key updated"}


@app.get("/roleplay/sessions")
async def get_roleplay_sessions(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return storage.list_sessions()


@app.post("/roleplay/sessions")
async def create_roleplay_session(req: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    data = await req.json()
    result = storage.create_session(
        data.get("slug", ""),
        data.get("session_id", ""),
        data.get("title", ""),
        data.get("canon"),
    )
    return JSONResponse(status_code=201, content=result)


@app.get("/roleplay/sessions/{slug}/{sid}")
async def get_single_session(slug: str, sid: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    try:
        return storage.get_session(slug, sid)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"message": "session not found"}})


@app.delete("/roleplay/sessions/{slug}/{sid}")
async def delete_single_session(slug: str, sid: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    try:
        storage.delete_session(slug, sid)
        return {"ok": True, "deleted": True, "slug": slug, "session_id": sid}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"message": "session not found"}})


@app.post("/roleplay/sessions/{slug}/{sid}/{action}")
async def handle_session_action(slug: str, sid: str, action: str, req: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    data = await req.json()
    if action == "chat":
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, engine.chat, slug, sid, str(data.get("message", "")))
        return {"reply": reply}
    elif action == "status":
        return storage.update_session(slug, sid, status=str(data.get("status", "")))
    elif action == "settings":
        return storage.update_session(slug, sid, canon=data.get("canon"))
    elif action == "bridge":
        return storage.bridge_receipt(slug, sid)
    elif action == "undo-last-turn":
        result = storage.undo_last_turn(slug, sid)
        engine.reset_runtime(slug, sid)
        return result
    elif action == "checkpoint":
        return {"path": str(storage.checkpoint_l4(slug, sid, data.get("payload", {})))}
    else:
        raise HTTPException(status_code=404, detail={"error": {"message": "not found"}})


@app.get("/roleplay/npcs")
async def get_npcs(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return storage.list_npcs()


@app.post("/roleplay/npcs")
async def save_npc(req: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    data = await req.json()
    nid = str(data.get("id", "")).strip()
    name = str(data.get("name", "")).strip()
    role = str(data.get("role", "")).strip()
    persona = str(data.get("persona", "")).strip()
    style = str(data.get("style", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail={"error": {"message": "NPC name is required"}})
    saved = storage.save_npc(nid, name, role, persona, style)
    return {"ok": True, "npc": saved}


@app.delete("/roleplay/npcs/{nid}")
async def delete_npc(nid: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    deleted = storage.delete_npc(nid)
    if deleted:
        return {"ok": True, "deleted": True}
    raise HTTPException(status_code=404, detail={"error": {"message": "NPC not found"}})


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    data = []
    now = int(time.time())
    for s in storage.list_sessions():
        mid = s.get("model_id") or f"roleplay/{s['slug']}/{s['session_id']}"
        data.append({
            "id": mid,
            "object": "model",
            "created": now,
            "owned_by": "roleplay",
            "root": mid,
            "parent": None,
        })
    return {"object": "list", "data": data}


@app.get("/v1/models/{model_name:path}")
async def get_model(model_name: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    mid = model_name.strip()
    target = None
    for s in storage.list_sessions():
        cur_mid = s.get("model_id") or s.get("model") or f"roleplay/{s['slug']}/{s['session_id']}"
        if mid == cur_mid or mid == f"{s['slug']}/{s['session_id']}":
            target = s
            break
    if not target:
        raise HTTPException(status_code=404, detail={"error": {"message": f"Model '{mid}' not found", "type": "invalid_request_error"}})
    return {
        "id": target.get("model_id") or f"roleplay/{target['slug']}/{target['session_id']}",
        "object": "model",
        "owned_by": "roleplay",
        "created": int(time.time()),
    }


async def _sse_generator(slug: str, sid: str, text: str, extra_system: str, chat_id: str, created: int, model: str):
    # 1. 立即打出合法首帧，建立 200 OK 并确认角色，重置上游/Cloudflare 倒计时
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"role": "assistant", "content": ""}))
    await asyncio.sleep(0)

    # 2. 将同步生成放入后台线程队列，主循环通过定时 wait 持续输出心跳保活
    import queue
    q: queue.Queue = queue.Queue()
    done_sentinel = object()

    def worker():
        try:
            for piece in engine.generate(slug, sid, text, extra_system=extra_system):
                if piece:
                    q.put(piece)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(done_sentinel)

    import threading
    t = threading.Thread(target=worker, daemon=True)
    t.start()

    loop = asyncio.get_running_loop()
    heartbeat_interval = 8.0  # 每 8 秒一次保活，远低于 Cloudflare 100s 阈值

    while True:
        try:
            # 异步非阻塞等待下一个 piece
            item = await loop.run_in_executor(None, q.get, True, heartbeat_interval)
            if item is done_sentinel:
                break
            if isinstance(item, Exception):
                print(f"[roleplay-api] worker error: {item}")
                break
            yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"content": item}))
            await asyncio.sleep(0)
        except queue.Empty:
            # 超时无数据产生（模型仍在思考/调用工具），发射空 delta 保活块与标准注释
            yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"content": ""}))
            await asyncio.sleep(0)

    # 3. 正常结束帧
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {}, finish_reason="stop"))
    yield _sse_data(None)


@app.post("/v1/chat/completions")
async def chat_completions(req: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": {"message": "Invalid JSON body"}})

    raw_model = str(data.get("model", "")).strip()
    resolved = resolve_session_by_model(raw_model)
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": f"model must be 'roleplay/<story>/<session>', '<story>/<session>', or configured model_id, got: {raw_model}"}}
        )

    slug, sid = resolved
    messages = data.get("messages")
    try:
        text = last_user_text(messages)
        extra_system = extra_system_text(messages)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": {"message": str(e)}})

    model_name = data.get("model", raw_model)
    created = int(time.time())
    chat_id = f"chatcmpl-roleplay-{created}"

    # 流式请求：使用 FastAPI StreamingResponse + 零耗心跳生成器，彻底终结 524
    if data.get("stream"):
        return StreamingResponse(
            _sse_generator(slug, sid, text, extra_system, chat_id, created, model_name),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    # 非流式请求：同样在异步线程池执行，支持长时生成不阻塞事件循环
    loop = asyncio.get_running_loop()
    ans = await loop.run_in_executor(None, engine.chat, slug, sid, text, extra_system)
    ans = _clean_api_response(ans)
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": ans},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(ans), "total_tokens": len(ans)}
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    print(f"roleplay frontend (FastAPI/Uvicorn): http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
