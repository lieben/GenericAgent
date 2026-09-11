#!/usr/bin/env python3
"""
GA API Server - OpenAI Compatible API for GenericAgent
将整个 GA 系统封装为 OpenAI 兼容的 API 服务
"""
import os, sys, json, time, threading, re, queue, asyncio, urllib.request, urllib.error
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

# 添加 GA 路径
script_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(script_dir))


# 与桌面端 frontends/desktop_bridge.py 的 _FINAL_INFO_RE 保持一致；
# API 服务不导入桌面 WebSocket 模块，避免引入不相关的 aiohttp 依赖。
_FINAL_INFO_RE = re.compile(r'\n*`{5}\n*\[Info\] Final response to user\.\n*`{5}\s*$')


def strip_final_info_marker(text: Any) -> str:
    return _FINAL_INFO_RE.sub('', str(text or ''))

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse, JSONResponse
    from pydantic import BaseModel, field_validator
    import uvicorn
except ImportError:
    print("❌ Missing dependencies. Install with:")
    print("   pip install fastapi uvicorn pydantic")
    sys.exit(1)

# ==================== 配置 ====================

CONFIG_FILE = script_dir / "ga_api_config.json"

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8765
    },
    "auth": {
        "api_key_env": "GA_API_KEY",
        "require_auth": True
    },
    "model": {
        "id": "xavier-gemini-pro",
        "name": "Xavier Gemini Pro (GenericAgent)",
        "description": "家里的GA系统 - 全工具访问权限",
        "created": 1725523200
    },
    "safety": {
        "dangerous_operations_require_confirm": True,
        "dangerous_keywords": [
            "delete", "rm -rf", "sudo", "DROP TABLE", "DROP DATABASE",
            "format", "重启", "关机", "shutdown", "reboot",
            "kill -9", "pkill", "删除", "清空", "格式化"
        ]
    }
}

def load_config():
    """加载配置文件，不存在则创建默认配置"""
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))
        print(f"✅ Created default config: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

CONFIG = load_config()

# 从环境变量读取 API Key，默认使用 sk-wxxxx
API_KEY = os.getenv(CONFIG["auth"]["api_key_env"], "sk-wxxxx")
if CONFIG["auth"]["require_auth"]:
    if API_KEY == "sk-wxxxx":
        print(f"ℹ️  Using default API Key: sk-wxxxx")
        print(f"   Override with: export {CONFIG['auth']['api_key_env']}=your-secret-key")
    else:
        print(f"✅ Using custom API Key from {CONFIG['auth']['api_key_env']}")

# ==================== Pydantic Models ====================

def _text_content(value):
    """Extract text only for fields that semantically require text (logs/prompts)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text", "output_text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _content_text(value):
    """Text used for queue display/safety checks without altering attachment blocks."""
    return _text_content(value) if not isinstance(value, str) else value


def _with_prefix(content, prefix):
    """Prefix multimodal content with a text block; preserve every original block."""
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}] + content
    return f"{prefix} {content}" if content else prefix


class Message(BaseModel):
    role: str
    # Preserve OpenAI/Responses multimodal blocks verbatim.  The API is only
    # a transport layer; GA's native client decides how to handle attachments.
    content: Any = ""

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    # Some clients (and the Responses-style adapters used by them) send the
    # per-conversation instruction outside messages.  Keep these as transient
    # request fields; they are never written to GA's persistent prompt.
    instructions: Any = ""
    system: Any = ""
    developer: Any = ""
    # Attachment metadata is passed untouched to desktop_bridge.
    files: Optional[List[Dict[str, Any]]] = None
    imageMetas: Optional[List[Dict[str, Any]]] = None
    images: Optional[List[Any]] = None

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]

# ==================== 全局状态 ====================

# API is a transport-only facade over the already-running desktop bridge.
BRIDGE_URL = os.environ.get("GA_DESKTOP_BRIDGE_URL", "http://127.0.0.1:14168")
BRIDGE_SESSION_ID = os.environ.get("GA_XAVIER_SESSION_ID", "sess-53138a368967")
BRIDGE_SESSION_TITLE = "Xavier-chat"
api_agent = None
api_agent_thread = None
api_lock = threading.Lock()  # 排队执行
confirmation_tokens: Dict[str, Dict] = {}  # 确认令牌缓存

# ==================== 安全检查 ====================

def is_dangerous_request(messages: List[Message]) -> Optional[str]:
    """检测是否包含危险操作"""
    if not CONFIG["safety"]["dangerous_operations_require_confirm"]:
        return None
    
    # 检查最后一条用户消息
    user_msg = ""
    for msg in reversed(messages):
        if msg.role == "user":
            user_msg = _text_content(msg.content).lower()
            break
    
    if not user_msg:
        return None
    
    # 检查危险关键词
    for keyword in CONFIG["safety"]["dangerous_keywords"]:
        if keyword.lower() in user_msg:
            return keyword
    
    return None

def generate_confirmation_token(messages: List[Message]) -> str:
    """生成确认令牌"""
    token = f"CONFIRM-{int(time.time())}-{hash(str(messages)) % 10000:04d}"
    confirmation_tokens[token] = {
        "messages": [m.dict() for m in messages],
        "created": time.time()
    }
    # 清理过期令牌（5分钟）
    now = time.time()
    expired = [k for k, v in confirmation_tokens.items() if now - v["created"] > 300]
    for k in expired:
        del confirmation_tokens[k]
    return token

def check_confirmation_token(messages: List[Message]) -> bool:
    """检查是否包含有效的确认令牌"""
    user_msg = ""
    for msg in reversed(messages):
        if msg.role == "user":
            user_msg = _text_content(msg.content)
            break
    
    # 查找令牌
    token_match = re.search(r'CONFIRM-\d+-\d{4}', user_msg)
    if not token_match:
        return False
    
    token = token_match.group()
    if token in confirmation_tokens:
        # 令牌有效，删除它
        del confirmation_tokens[token]
        return True
    
    return False

# ==================== API 端点 ====================

@asynccontextmanager
async def lifespan(app):
    """Attach to the desktop bridge; never create or restore a GA instance."""
    print("🚀 Starting transport API for existing desktop session...")
    print(f"   Bridge: {BRIDGE_URL}  Session: {BRIDGE_SESSION_TITLE} ({BRIDGE_SESSION_ID})")
    yield
    print("🛑 Shutting down transport API (desktop GA remains running)...")

app = FastAPI(
    title="GenericAgent API",
    description="OpenAI-compatible API for GenericAgent System",
    version="0.1.0",
    lifespan=lifespan
)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """鉴权中间件"""
    if not CONFIG["auth"]["require_auth"] or not API_KEY:
        return await call_next(request)
    
    # 放行健康检查和模型列表
    if request.url.path in ["/health", "/v1/models"]:
        return await call_next(request)
    
    # 检查 Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Missing or invalid Authorization header", "type": "auth_error"}}
        )
    
    token = auth_header[7:]  # 去掉 "Bearer "
    if token != API_KEY:
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key", "type": "auth_error"}}
        )
    
    return await call_next(request)

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "agent_running": True,
        "bridge": BRIDGE_URL,
        "session": BRIDGE_SESSION_TITLE
    }

@app.get("/v1/models")
async def list_models():
    """列出可用模型"""
    return {
        "object": "list",
        "data": [
            {
                "id": CONFIG["model"]["id"],
                "object": "model",
                "created": CONFIG["model"]["created"],
                "owned_by": "genericagent-local",
                "permission": [],
                "root": CONFIG["model"]["id"],
                "parent": None,
            }
        ]
    }

def _clean_api_response(text: str) -> str:
    """API 对外只返回桌面端当前可见的最后一轮正文，不暴露工具过程/摘要。"""
    value = strip_final_info_marker(text)
    # 与桌面端 splitContentTurnSegs 同源：只取最后一个轮次。
    parts = re.split(r'\**LLM Running \(Turn \d+\) \.\.\.\**', value, flags=re.IGNORECASE)
    value = (parts[-1] if parts else value)
    # 删除轮摘要，以及工具调用/结果/思考协议块；这些只属于桌面端折叠展示层。
    value = re.sub(r'<summary>[\s\S]*?</summary>\s*', '', value, flags=re.IGNORECASE)
    value = re.sub(r'<thinking>[\s\S]*?</thinking>\s*', '', value, flags=re.IGNORECASE)
    value = re.sub(r'<function_calls>[\s\S]*?</function_calls>\s*', '', value, flags=re.IGNORECASE)
    value = re.sub(r'<function_results>[\s\S]*?</function_results>\s*', '', value, flags=re.IGNORECASE)
    # 兼容 GA 文本化协议块：🛠️ Tool / Tool Result 到下一段正文前均隐藏。
    value = re.sub(r'(?ims)^\s*(?:🛠️\s*)?(?:Tool|工具)\s*:.*?(?=\n\s*\n|\Z)', '', value)
    value = re.sub(r'(?ims)^\s*(?:🛠️\s*)?(?:Tool Result|工具结果)\s*:.*?(?=\n\s*\n|\Z)', '', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def _request_prompts(messages, instructions=""):
    prompts = [m.content for m in messages
               if m.role in ("system", "developer") and m.content.strip()]
    if instructions:
        instruction_text = _text_content(instructions)
        if instruction_text.strip():
            prompts.insert(0, instruction_text)
    return prompts


def _extract_platform_marker(text):
    """Extract platform marker like 【.微信平台跨次元对话.】 from text."""
    import re
    match = re.search(r'【\.[^】]+\.】', text)
    return match.group(0) if match else None


def _chat_request_instructions(request):
    """Collect platform instructions from both top-level fields and messages array.
    Returns (full_instructions, platform_marker)."""
    values = []
    platform_marker = None
    
    # 1. Extract from top-level fields (OpenAI standard)
    for value in (request.instructions, request.system, request.developer):
        text = _text_content(value)
        if text.strip():
            values.append(text)
            if not platform_marker:
                platform_marker = _extract_platform_marker(text)
    
    # 2. Extract from messages array (Chatbox style: first system message)
    if request.messages:
        for msg in request.messages:
            if msg.role == "system":
                text = _text_content(msg.content)
                if text.strip():
                    values.append(text)
                    if not platform_marker:
                        platform_marker = _extract_platform_marker(text)
                break  # Only take the first system message
    
    full_instructions = "\n".join(values)
    return full_instructions, platform_marker


def _bridge_json(method, path, payload=None, timeout=30):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BRIDGE_URL + path, data=data, headers=headers, method=method)
    try:
        # Explicitly bypass system proxy for local loopback bridge communication
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise HTTPException(status_code=502, detail=f"desktop bridge HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"desktop bridge unavailable: {exc}")


def _bridge_messages(after=0):
    return _bridge_json("GET", f"/session/{BRIDGE_SESSION_ID}/messages?after={after}&limit=200", timeout=30)


def _run_api_turn(query, initial_user_content=None, attachments=None):
    """Submit untouched content to desktop_bridge and return its native assistant text."""
    with api_lock:
        before = _bridge_messages(0)
        baseline = int(before.get("msgSeq", 0) or 0)
        body = {"prompt": initial_user_content if initial_user_content is not None else query}
        # These are transport fields only. The desktop bridge/GA owns all decoding.
        if attachments:
            body.update(attachments)
        _bridge_json("POST", f"/session/{BRIDGE_SESSION_ID}/prompt", body, timeout=60)
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(0.5)
            state = _bridge_messages(baseline)
            for msg in state.get("messages", []):
                if msg.get("role") == "assistant" and int(msg.get("id", 0) or 0) > baseline:
                    content = msg.get("content", "")
                    return _text_content(content) if not isinstance(content, str) else content
            if state.get("status") in ("error", "failed"):
                raise HTTPException(status_code=502, detail=state.get("lastError") or "desktop GA turn failed")
        raise HTTPException(status_code=504, detail="desktop GA response timeout")


async def _execute_api_turn(query, messages, instructions="", attachments=None):
    raw_content = next((m.content for m in reversed(messages) if m.role == "user"), query)
    return await asyncio.to_thread(_run_api_turn, query, raw_content, attachments)


@app.post("/v1/responses")
async def responses(request: Request):
    """OpenAI Responses API 兼容层：复用 GA 的现有聊天执行链。"""
    body = await request.json()
    raw_input = body.get("input", "")
    try:
        if isinstance(raw_input, str):
            messages = [{"role": "user", "content": raw_input}] if raw_input else []
        elif isinstance(raw_input, list):
            messages = []
            for item in raw_input:
                if not isinstance(item, dict) or item.get("type", "message") != "message":
                    raise ValueError("Only message input items are supported")
                messages.append({"role": item.get("role", "user"),
                                 "content": item.get("content", "")})
        else:
            raise ValueError("input must be a string or message array")
        if not messages and isinstance(body.get("messages"), list):
            messages = body["messages"]
        messages = [Message(**m) for m in messages]
        instructions = _text_content(body.get("instructions"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user_message = next((m.content for m in reversed(messages)
                         if m.role == "user" and _content_text(m.content).strip()), "")
    if not user_message:
        raise HTTPException(status_code=400, detail="No user input found")
    full_response = await _execute_api_turn(user_message, messages, instructions)
    model = body.get("model", CONFIG["model"]["id"])
    full_response = _clean_api_response(full_response)
    return {"id": f"resp-{int(time.time())}", "object": "response",
            "created_at": int(time.time()), "model": model,
            "output": [{"type": "message", "id": f"msg-{int(time.time())}",
                        "role": "assistant", "content": [{"type": "output_text", "text": full_response}]}],
            "output_text": full_response, "status": "completed"}


@app.post("/v1/completions")
async def completions(request: Request):
    """OpenAI legacy text-completions compatibility for clients such as Chatbox."""
    body = await request.json()
    prompt = body.get("prompt", "")
    if isinstance(prompt, list):
        prompt = prompt[-1] if prompt else ""
    prompt = _text_content(prompt)
    # A few adapters put the transient instruction outside the prompt.
    instructions = _text_content(body.get("instructions", body.get("system", body.get("developer", ""))))
    # Debug: log received fields to verify platform settings injection
    print(f"[Completions] prompt={len(str(prompt))}ch instructions={len(str(instructions))}ch "
          f"raw_instr={len(str(body.get('instructions','')))}ch raw_sys={len(str(body.get('system','')))}ch raw_dev={len(str(body.get('developer','')))}ch")
    if instructions:
        preview = str(instructions)[:80].replace('\n', ' ')
        print(f"[Completions] instructions preview: {preview}")
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="No prompt found")
    messages = [Message(role="user", content=prompt)]
    full_response = await _execute_api_turn(prompt, messages, instructions=instructions)
    text = _clean_api_response(full_response)
    created = int(time.time())
    return {
        "id": f"cmpl-{created}",
        "object": "text_completion",
        "created": created,
        "model": body.get("model", CONFIG["model"]["id"]),
        "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI 兼容的聊天接口"""

    
    # Debug: log raw request fields to diagnose platform instructions
    print(f"[ChatCompletions] Raw request fields:")
    print(f"  - instructions: {getattr(request, 'instructions', None)}")
    print(f"  - system: {getattr(request, 'system', None)}")
    print(f"  - developer: {getattr(request, 'developer', None)}")
    print(f"  - messages count: {len(request.messages) if request.messages else 0}")
    if request.messages:
        for i, msg in enumerate(request.messages[:3]):  # Show first 3 messages
            content_preview = msg.content[:200] if len(msg.content) > 200 else msg.content
            print(f"    [{i}] role={msg.role}, content_length={len(msg.content)}")
            print(f"        content: {content_preview}")
    
    # 检查是否包含确认令牌
    has_confirmation = check_confirmation_token(request.messages)
    
    # 危险操作检测（如果没有确认令牌）
    if not has_confirmation:
        dangerous_keyword = is_dangerous_request(request.messages)
        if dangerous_keyword:
            token = generate_confirmation_token(request.messages)
            return JSONResponse(
                status_code=200,
                content={
                    "id": f"chatcmpl-danger-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"⚠️  检测到危险操作关键词：「{dangerous_keyword}」\n\n"
                                     f"为了安全，需要你确认是否真的要执行这个操作。\n\n"
                                     f"如果确认，请在下一条消息中包含以下令牌：\n\n"
                                     f"`{token}`\n\n"
                                     f"令牌有效期 5 分钟。"
                        },
                        "finish_reason": "stop"
                    }]
                }
            )
    
    # 获取用户消息
    user_message = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_message = msg.content
            break
    
    if not _content_text(user_message).strip():
        raise HTTPException(status_code=400, detail="No user message found")
    
    # Extract platform instructions and marker
    platform_instructions, platform_marker = _chat_request_instructions(request)
    if platform_instructions:
        print(f"[ChatCompletions] Platform instructions ({len(platform_instructions)}ch):")
        print(f"--- BEGIN FULL CONTENT ---")
        print(platform_instructions)
        print(f"--- END FULL CONTENT ---")
        if platform_marker:
            print(f"[ChatCompletions] Extracted platform marker: {platform_marker}")
    else:
        print(f"[ChatCompletions] No platform instructions received")
    # Inject platform marker into user message (like timestamp)
    if platform_marker:
        user_message = f"{platform_marker} {user_message}"
        print(f"[ChatCompletions] Injected platform marker into user message")
    
    attachments = {k: v for k, v in {
        "files": request.files, "imageMetas": request.imageMetas, "images": request.images
    }.items() if v}
    full_response = await _execute_api_turn(user_message, request.messages,
                                             instructions=platform_instructions,
                                             attachments=attachments)

    # 返回 OpenAI 格式响应
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": _clean_api_response(full_response)
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 0,  # 暂不统计
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

# ==================== 主程序 ====================

def main():
    """启动 API 服务器"""
    print("\n" + "="*60)
    print("  GenericAgent API Server")
    print("  将整个 GA 系统封装为 OpenAI 兼容 API")
    print("="*60 + "\n")
    
    host = CONFIG["server"]["host"]
    port = CONFIG["server"]["port"]
    
    print(f"📡 Server will start at: http://{host}:{port}")
    print(f"📋 Config file: {CONFIG_FILE}")
    print(f"🔑 API Key env: {CONFIG['auth']['api_key_env']}")
    print(f"🤖 Model ID: {CONFIG['model']['id']}\n")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )

if __name__ == "__main__":
    main()
