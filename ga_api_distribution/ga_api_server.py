#!/usr/bin/env python3
"""
GA API Server - OpenAI Compatible API for GenericAgent
将整个 GA 系统封装为 OpenAI 兼容的 API 服务
"""
import os, sys, json, time, threading, re, queue, asyncio, urllib.request, urllib.error
import base64, binascii, mimetypes, tempfile
import urllib.parse
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
        "name": "GenericAgent desktop session",
        "description": "The local GenericAgent desktop session exposed through an OpenAI-compatible API",
        "created": 1725523200
    },
    "safety": {
        "dangerous_operations_require_confirm": True,
        "dangerous_keywords": [
            "delete", "rm -rf", "sudo", "DROP TABLE", "DROP DATABASE",
            "format", "重启", "关机", "shutdown", "reboot",
            "kill -9", "pkill", "删除", "清空", "格式化"
        ]
    },
    "mempalace": {
        "enabled": False,
        "wing": "your_api_wing",
        "search_limit": 3,
        "max_distance": 0.85
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

# ==================== MemPalace 集成辅助函数 ====================

_mempalace_client = None

def _get_mempalace_client():
    global _mempalace_client
    if _mempalace_client is None:
        try:
            from ga_api_mempalace import MemPalaceMCPClient
            _mempalace_client = MemPalaceMCPClient()
        except Exception as exc:
            print(f"[MemPalace] Failed to initialize client: {exc}")
    return _mempalace_client

def _search_mempalace_sync(query: str, wing: str = "your_api_wing", limit: int = 3, max_distance: float = 0.85) -> str:
    """从 MemPalace 检索相关历史记忆"""
    mp_cfg = CONFIG.get("mempalace", {})
    if not mp_cfg.get("enabled", False):
        return ""
    client = _get_mempalace_client()
    if not client:
        return ""
    try:
        clean_q = re.sub(r"\[\d{4}-\d{2}-\d{2}[^\]]*\]", "", query)
        clean_q = re.sub(r"【.*?】", "", clean_q).strip()
        if not clean_q:
            return ""
        # 截断 query 到 200 字符以内符合 MemPalace 要求
        clean_q = clean_q[:200]
        res = client.call_tool("mempalace_search", {
            "wing": wing,
            "query": clean_q,
            "limit": limit,
            "max_distance": max_distance
        })
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                pass
        if isinstance(res, dict) and res.get("results"):
            matched_texts = []
            for item in res["results"]:
                c = item.get("content", "").strip()
                if c:
                    matched_texts.append(c)
            if matched_texts:
                return "\n---\n".join(matched_texts)
    except Exception as exc:
        print(f"[MemPalace] Search exception: {exc}")
    return ""

def _add_drawer_sync(content: str, wing: str = "your_api_wing", room: str = "chat_history"):
    """异步或后台写入 MemPalace"""
    mp_cfg = CONFIG.get("mempalace", {})
    if not mp_cfg.get("enabled", False):
        return
    client = _get_mempalace_client()
    if not client:
        return
    try:
        client.call_tool("mempalace_add_drawer", {
            "wing": wing,
            "room": room,
            "content": content,
            "added_by": "ga_api_server"
        })
    except Exception as exc:
        print(f"[MemPalace] Add drawer exception: {exc}")

def _save_turn_to_mempalace_async(user_text: str, assistant_text: str):
    """在后台线程中将本次对话完整入库"""
    def _worker():
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 格式化存入
        entry = f"[{now_str}] 对话记录\n言言: {user_text}\n沈星回: {assistant_text}"
        _add_drawer_sync(entry)
    threading.Thread(target=_worker, daemon=True).start()

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


_IMAGE_DATA_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$")
_ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"}


def _materialize_image_url(url: str, temp_paths: list[str]) -> str:
    """Turn an OpenAI image URL into a local path; never return/send its base64."""
    if not isinstance(url, str) or not url:
        raise ValueError("image_url has no URL")
    match = _IMAGE_DATA_RE.match(url)
    if match:
        mime, encoded = match.groups()
        if mime not in _ALLOWED_IMAGE_MIMES:
            raise ValueError(f"unsupported image MIME type: {mime}")
        try:
            raw = base64.b64decode(re.sub(r"\\s+", "", encoded), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid image data URL") from exc
        suffix = mimetypes.guess_extension(mime) or ".img"
    elif url.startswith(("/", "./", "../")):
        path = os.path.abspath(url)
        if not os.path.isfile(path):
            raise ValueError("image file does not exist")
        return path
    else:
        # Remote URLs are downloaded to a private temporary file, never forwarded as data URLs.
        request = urllib.request.Request(url, headers={"User-Agent": "GenericAgent/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(25 * 1024 * 1024 + 1)
            if len(raw) > 25 * 1024 * 1024:
                raise ValueError("image is too large")
        suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".img"
    fd, path = tempfile.mkstemp(prefix="ga-api-image-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
    except Exception:
        try: os.unlink(path)
        except OSError: pass
        raise
    temp_paths.append(path)
    return path


def _prepare_multimodal_content(content, temp_paths: list[str]):
    """Extract text and convert OpenAI image blocks into bridge imageMetas paths."""
    text_parts = []
    image_metas = []
    if isinstance(content, str):
        return content, image_metas
    if not isinstance(content, list):
        return "", image_metas
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text") and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        elif kind in ("image_url", "input_image"):
            image = part.get("image_url") if kind == "image_url" else part
            url = image.get("url") if isinstance(image, dict) else None
            image_metas.append({"path": _materialize_image_url(url, temp_paths)})
    return "\n".join(text_parts), image_metas


def _safe_preview(value, limit=160):
    """Log request shape only; never log image data or raw multimodal blocks."""
    text = _text_content(value).replace("data:image/", "data:image/[redacted]/")
    return text[:limit] + ("..." if len(text) > limit else "")


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
BRIDGE_SESSION_ID = os.environ.get("GA_XAVIER_SESSION_ID", "")
BRIDGE_SESSION_TITLE = os.environ.get("GA_XAVIER_SESSION_TITLE", "desktop-session")
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

def _sse_data(payload) -> str:
    """Serialize one OpenAI SSE frame. payload=None emits the terminal [DONE]."""
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
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


def _openai_chat_completion(chat_id: str, created: int, model: str, content: str,
                            finish_reason: str = "stop") -> Dict[str, Any]:
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _iter_content_chunks(text: str, size: int = 24):
    """Split cleaned text into small SSE deltas so clients can render incrementally."""
    if not text:
        yield ""
        return
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def _sse_chat_stream(chat_id: str, created: int, model: str, content: str):
    """Standard OpenAI chat.completion.chunk SSE: role → content deltas → stop → [DONE]."""
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"role": "assistant", "content": ""}))
    for piece in _iter_content_chunks(content):
        if piece:
            yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"content": piece}))
            await asyncio.sleep(0)
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {}, finish_reason="stop"))
    yield _sse_data(None)


async def _sse_text_completion_stream(cmpl_id: str, created: int, model: str, text: str):
    """Standard OpenAI text_completion SSE: text deltas → stop → [DONE]."""
    for piece in _iter_content_chunks(text):
        if piece:
            yield _sse_data({
                "id": cmpl_id,
                "object": "text_completion",
                "created": created,
                "model": model,
                "choices": [{"text": piece, "index": 0, "logprobs": None, "finish_reason": None}],
            })
            await asyncio.sleep(0)
    yield _sse_data({
        "id": cmpl_id,
        "object": "text_completion",
        "created": created,
        "model": model,
        "choices": [{"text": "", "index": 0, "logprobs": None, "finish_reason": "stop"}],
    })
    yield _sse_data(None)


def _sse_headers() -> Dict[str, str]:
    # Disable proxy/nginx buffering so third-party clients see chunks immediately.
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
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


def _bridge_json(method, path, payload=None, timeout=30, allow_500=False):
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
        # Bridge returns 500 when BrokenPipeError occurs on stderr logging,
        # but the turn itself will complete. Allow caller to handle this gracefully.
        if exc.code == 500 and allow_500:
            print(f"[API] Bridge returned 500 (likely BrokenPipeError), caller will poll for reply...")
            return None
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
        
        # DEBUG: 打印实际发送的内容
        print(f"[_run_api_turn] DEBUG prompt content (first 150 chars): {body['prompt'][:150]}")
        if 'display' in body:
            print(f"[_run_api_turn] DEBUG display field (first 100 chars): {body['display'][:100]}")
        
        # Try to submit prompt; if bridge returns 500 (BrokenPipeError from stderr),
        # don't fail immediately — the turn itself will complete, we just poll for the reply.
        try:
            _bridge_json("POST", f"/session/{BRIDGE_SESSION_ID}/prompt", body, timeout=60)
        except HTTPException as exc:
            if exc.status_code == 502 and "HTTP 500" in str(exc.detail):
                print(f"[API] Bridge returned 500 (likely BrokenPipeError), continuing to poll for reply...")
            else:
                raise
        
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
    """Send this turn to desktop GA.

    Platform instructions must go into the agent-facing prompt. The desktop
    bridge treats `display` as UI-only bubble text and never feeds it to the
    model — that is why stuffing PR into display previously did nothing.
    """
    attachments = dict(attachments or {})
    instruction_text = _text_content(instructions).strip()

    # 检索 MemPalace 相关长期记忆
    mp_memory = _search_mempalace_sync(query)
    if mp_memory:
        mp_context = f"【来自记忆宫殿的历史相关记忆】\n{mp_memory}\n"
        if instruction_text:
            instruction_text = f"{mp_context}\n{instruction_text}"
        else:
            instruction_text = mp_context
        print(f"[_execute_api_turn] Injected MemPalace memories into instructions")

    agent_prompt = query
    if instruction_text:
        agent_prompt = (
            "【第三方平台指令，仅本轮有效】\n"
            f"{instruction_text}\n"
            "【用户消息】\n"
            f"{query}"
        )
        # Keep the Xavier-chat bubble as the user-facing text, never the PR body.
        if not str(attachments.get("display") or "").strip():
            attachments["display"] = query
        print(f"[_execute_api_turn] Injected platform instructions ({len(instruction_text)}ch) into agent prompt")
    
    reply = await asyncio.to_thread(_run_api_turn, agent_prompt, None, attachments)
    
    # 将本次对话异步全量写入 MemPalace
    _save_turn_to_mempalace_async(query, reply)
    
    return reply


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
    cmpl_id = f"cmpl-{created}"
    model = body.get("model", CONFIG["model"]["id"])
    if body.get("stream"):
        return StreamingResponse(
            _sse_text_completion_stream(cmpl_id, created, model, text),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )
    return {
        "id": cmpl_id,
        "object": "text_completion",
        "created": created,
        "model": model,
        "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI 兼容的聊天接口"""

    # Debug: log request shape only; never print multimodal content (may contain image data).
    print(f"[ChatCompletions] Request: instructions={bool(getattr(request, 'instructions', None))}, "
          f"system={bool(getattr(request, 'system', None))}, developer={bool(getattr(request, 'developer', None))}, "
          f"messages={len(request.messages) if request.messages else 0}")
    if request.messages:
        for i, msg in enumerate(request.messages[:3]):
            content = msg.content
            print(f"    [{i}] role={msg.role}, content_type={type(content).__name__}, "
                  f"text_length={len(_content_text(content))}, "
                  f"image_blocks={sum(isinstance(p, dict) and p.get('type') in ('image_url', 'input_image') for p in content) if isinstance(content, list) else 0}")
    
    # 检查是否包含确认令牌
    has_confirmation = check_confirmation_token(request.messages)
    
    # 危险操作检测（如果没有确认令牌）
    if not has_confirmation:
        dangerous_keyword = is_dangerous_request(request.messages)
        if dangerous_keyword:
            token = generate_confirmation_token(request.messages)
            warn_text = (
                f"⚠️  检测到危险操作关键词：「{dangerous_keyword}」\n\n"
                f"为了安全，需要你确认是否真的要执行这个操作。\n\n"
                f"如果确认，请在下一条消息中包含以下令牌：\n\n"
                f"`{token}`\n\n"
                f"令牌有效期 5 分钟。"
            )
            created = int(time.time())
            chat_id = f"chatcmpl-danger-{created}"
            if request.stream:
                return StreamingResponse(
                    _sse_chat_stream(chat_id, created, request.model, warn_text),
                    media_type="text/event-stream",
                    headers=_sse_headers(),
                )
            return JSONResponse(
                status_code=200,
                content=_openai_chat_completion(chat_id, created, request.model, warn_text),
            )
    
    # 获取用户消息；纯图片消息也是合法输入。
    user_message = ""
    user_content = None
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_content = msg.content
            break
    if user_content is None:
        raise HTTPException(status_code=400, detail="No user message found")

    temp_image_paths = []
    try:
        user_text, content_image_metas = _prepare_multimodal_content(user_content, temp_image_paths)
        if not user_text.strip() and not content_image_metas:
            raise HTTPException(status_code=400, detail="No user message found")
        user_message = user_text.strip() or "请分析用户发送的图片。"

        # Extract platform instructions and marker
        platform_instructions, platform_marker = _chat_request_instructions(request)
        if platform_instructions:
            print(f"[ChatCompletions] Platform instructions ({len(platform_instructions)}ch)")
            if platform_marker:
                print(f"[ChatCompletions] Extracted platform marker: {platform_marker}")
        else:
            print(f"[ChatCompletions] No platform instructions received")

        # Inject timestamp for temporal context
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        timestamped_message = f"[{timestamp}]"
        if platform_marker:
            timestamped_message += f" {platform_marker}"
        timestamped_message += f" {user_message}"
        print(f"[ChatCompletions] Final user message: {_safe_preview(timestamped_message)}")

        attachments = {k: v for k, v in {
            "files": request.files, "imageMetas": request.imageMetas, "images": request.images
        }.items() if v}
        if content_image_metas:
            attachments["imageMetas"] = (attachments.get("imageMetas") or []) + content_image_metas

        full_response = await _execute_api_turn(timestamped_message, request.messages,
                                                 instructions=platform_instructions,
                                                 attachments=attachments)
        content = _clean_api_response(full_response)
        created = int(time.time())
        chat_id = f"chatcmpl-{created}"
        if request.stream:
            return StreamingResponse(
                _sse_chat_stream(chat_id, created, request.model, content),
                media_type="text/event-stream",
                headers=_sse_headers(),
            )
        return _openai_chat_completion(chat_id, created, request.model, content)
    finally:
        for path in temp_image_paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"[ChatCompletions] Could not remove temporary image: {exc}")

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
