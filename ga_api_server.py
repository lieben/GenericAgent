#!/usr/bin/env python3
"""
GA API Server - OpenAI Compatible API for GenericAgent
将整个 GA 系统封装为 OpenAI 兼容的 API 服务
"""
import os, sys, json, time, threading, re, queue, asyncio, urllib.request, urllib.error
import base64, binascii, mimetypes, tempfile
import urllib.parse
import copy
try:
    import fcntl
except ImportError:
    fcntl = None
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

# 添加 GA 路径
script_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(script_dir))
from agentmain import GenericAgent


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
        "id": "agent-gemini-pro",
        "name": "Agent Gemini Pro (GenericAgent)",
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
    },
    "identity": {
        "self_nickname": "助手",
        "special_nickname": "用户"
    },
    "mempalace": {
        "enabled": True,
        "wing": "agent_api",
        "search_limit": 3,
        "max_distance": 0.85,
        "lore": {
            "enabled": False,
            "wing": "lore",
            "max_hits": 3,
            "max_chars": 1600,
            "index_file": "temp/lore_entries.json",
            "recall_header": "【知识库·按需召回】以下是命中的词条原文，只在本轮话题涉及这些词时使用；不要改写成新设定，不要把未命中的内容当成已知。"
        }
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


def _identity_names():
    """读取身份昵称配置：self_nickname=AI 自称，special_nickname=对方称呼。
    所有涉及具体人名的逻辑一律走这里，禁止硬编码。"""
    ident = CONFIG.get("identity", {}) or {}
    self_name = str(ident.get("self_nickname", "") or "").strip() or "助手"
    partner_name = str(ident.get("special_nickname", "") or "").strip() or "用户"
    return self_name, partner_name

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

_lore_index = None
_LORE_STOP = {
    "今天", "昨天", "明天", "现在", "我们", "你们", "他们", "这个", "那个",
    "什么", "怎么", "为什么", "不是", "没有", "一个", "一下", "可以", "知道",
    "觉得", "还是", "然后", "就是", "因为", "所以", "如果", "已经", "自己",
    "游戏", "世界", "百科", "词条",
}
# 词条库专属的高频词（如某个作品/世界观的通用词）通过配置追加，代码本身保持中性。
_LORE_STOP |= set(
    (CONFIG.get("mempalace", {}).get("lore", {}) or {}).get("extra_stop_tokens") or []
)


def _load_lore_index():
    """读取知识库标题索引（路径来自配置）。只在命中标题时召回，避免语义检索误配。"""
    global _lore_index
    if _lore_index is not None:
        return _lore_index
    lore_cfg = CONFIG.get("mempalace", {}).get("lore", {})
    rel = lore_cfg.get("index_file", "temp/lore_entries.json")
    path = Path(__file__).resolve().parent / rel
    titles = []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        for item in items:
            title = str(item.get("title", "")).strip()
            if title:
                titles.append(title)
    except Exception as exc:
        print(f"[MemPalace] Lore index unavailable: {exc}")
    titles.sort(key=len, reverse=True)
    _lore_index = titles
    return _lore_index


def _match_lore_titles(query: str, max_hits: int = 3):
    text = (query or "").strip()
    if not text:
        return []
    hits = []
    occupied = [False] * len(text)
    for title in _load_lore_index():
        if len(title) <= 2 and title in _LORE_STOP:
            continue
        start = 0
        while True:
            pos = text.find(title, start)
            if pos < 0:
                break
            end = pos + len(title)
            if not any(occupied[pos:end]):
                hits.append(title)
                for i in range(pos, end):
                    occupied[i] = True
                break
            start = pos + 1
        if len(hits) >= max_hits:
            break
    return hits


def _extract_lore_search_query(messages, default_query: str = "") -> str:
    """从输入消息中精确提取用于知识库召回的真实用户发言正文。
    严禁将人设提示词、群规、群友昵称、时间戳、引用前缀扫描进知识库匹配池。
    """
    raw_texts = []
    for m in messages or []:
        if getattr(m, "role", "") == "user":
            content = str(getattr(m, "content", "") or "")
            if content.strip():
                raw_texts.append(content)
    if not raw_texts and default_query:
        raw_texts.append(default_query)
    if not raw_texts:
        return ""

    # 取最新的一条用户发言进行提取
    latest_text = raw_texts[-1]

    # 如果是群聊/私聊包装格式，精确定位到真实的新消息正文
    if "需要回复的新消息：" in latest_text:
        target = latest_text.split("需要回复的新消息：", 1)[1]
    elif "【新消息】" in latest_text:
        target = latest_text.split("【新消息】", 1)[1]
    elif "【最近消息】" in latest_text:
        target = latest_text.split("【最近消息】", 1)[1]
    else:
        target = latest_text

    cleaned_lines = []
    for line in target.splitlines():
        line = line.strip()
        if not line:
            continue
        # 遇到后续的系统要求、边界提示或尾部限制直接截断
        if line.startswith("只输出严格") or line.startswith("可回应的群成员") or line.startswith("历史背景"):
            break
        # 剥离时间戳 [23:12] 或 [2026-10-11 ...]
        line = re.sub(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", "", line)
        line = re.sub(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*", "", line)
        # 剥离来源、UID、群昵称等元数据 【来源微信群：...｜群昵称：...】
        line = re.sub(r"【[^】]*】", "", line)
        line = line.strip()
        if line:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _search_lore_sync(query: str) -> str:
    """命中知识库标题时，取对应原文；闲聊不注入。"""
    mp_cfg = CONFIG.get("mempalace", {})
    lore_cfg = mp_cfg.get("lore", {})
    if not mp_cfg.get("enabled", True) or not lore_cfg.get("enabled", True):
        return ""
    max_hits = int(lore_cfg.get("max_hits", 3))
    titles = _match_lore_titles(query, max_hits)
    if not titles:
        return ""
    client = _get_mempalace_client()
    if not client:
        return ""
    wing = lore_cfg.get("wing", "lore")
    max_chars = int(lore_cfg.get("max_chars", 1600))
    blocks = []
    try:
        for title in titles:
            res = client.call_tool("mempalace_search", {
                "query": f"【世界书】{title}",
                "wing": wing,
                "limit": 8,
                "max_distance": 1.2,
            })
            if isinstance(res, dict) and res.get("status") == "error":
                print(f"[MemPalace] Lore search error: {res.get('msg')}")
                continue
            data = json.loads(res) if isinstance(res, str) else res
            chosen = ""
            for item in data.get("results", []):
                text = str(item.get("text", "")).strip()
                if text.startswith(f"【世界书】{title}\n") or text.startswith(f"【世界书】{title}"):
                    chosen = text
                    break
            if chosen:
                blocks.append(chosen)
        if not blocks:
            return ""
        body = "\n\n".join(blocks)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n…（词条已截断）"
        header = lore_cfg.get(
            "recall_header",
            "【知识库·按需召回】以下是命中的词条原文，只在本轮话题涉及这些词时使用；"
            "不要改写成新设定，不要把未命中的内容当成已知。",
        )
        return f"{header}\n{body}"
    except Exception as exc:
        print(f"[MemPalace] Lore search exception: {exc}")
        return ""


def _search_mempalace_sync(query: str, wing: str = "agent_api", limit: int = 3, max_distance: float = 0.85) -> str:
    """从 MemPalace 检索相关历史记忆，支持同时检索日常记忆翼与沈星回母库翼"""
    mp_cfg = CONFIG.get("mempalace", {})
    if not mp_cfg.get("enabled", True):
        return ""
    client = _get_mempalace_client()
    if not client:
        return ""
    try:
        clean_q = re.sub(r"\[\d{4}-\d{2}-\d{2}[^\]]*\]", "", query)
        clean_q = re.sub(r"【.*?】", "", clean_q).strip()
        if not clean_q:
            return ""
        clean_q = clean_q[:200]
        
        # 目标翼：主翼（日常聊天/历史） + V2母库翼（角色亲历/事实/设定）
        target_wings = [wing]
        lore_wing = mp_cfg.get("lore_wing", "wing_xinghui")
        if lore_wing and lore_wing != wing:
            target_wings.append(lore_wing)
            
        all_matched = []
        for w in target_wings:
            # 母库翼放宽阈值至 1.15 以便捕捉语义关联，主日常翼按配置
            dist = 1.15 if w == lore_wing else max_distance
            res = client.call_tool("mempalace_search", {
                "wing": w,
                "query": clean_q,
                "limit": limit,
                "max_distance": dist
            })
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    pass
            if isinstance(res, dict) and res.get("results"):
                for item in res["results"]:
                    c = item.get("content", "").strip()
                    if c:
                        prefix = "【亲历设定与主线事实】" if w == lore_wing else "【过往互动记忆】"
                        all_matched.append(f"{prefix}\n{c}")
        if all_matched:
            return "\n---\n".join(all_matched[:limit + 2])
    except Exception as exc:
        print(f"[MemPalace] Search exception: {exc}")
    return ""

def _add_drawer_sync(content: str, wing: str = "agent_api", room: str = "chat_history"):
    """异步或后台写入 MemPalace"""
    mp_cfg = CONFIG.get("mempalace", {})
    if not mp_cfg.get("enabled", True):
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
        # 格式化存入（昵称走 identity 配置，禁止硬编码）
        self_name, partner_name = _identity_names()
        entry = f"[{now_str}] 对话记录\n{partner_name}: {user_text}\n{self_name}: {assistant_text}"
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
    # WeChat/desktop style inline uploads: [{name, mime_type, data_base64}].
    fileUploads: Optional[List[Dict[str, Any]]] = None
    imageMetas: Optional[List[Dict[str, Any]]] = None
    images: Optional[List[Any]] = None

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]

# ==================== 全局状态 ====================

# API owns its own GenericAgent process.  The desktop UI is a separate channel.
# The fixed session file is used as the durable hand-off for agent short memory.
BRIDGE_SESSION_ID = os.environ.get("GA_AGENT_SESSION_ID", "sess-53138a368967")
BRIDGE_SESSION_TITLE = "agent-chat"
SESSION_FILE = script_dir / "temp" / "desktop_sessions" / f"{BRIDGE_SESSION_ID}.json"
SESSION_LOCK_FILE = script_dir / "temp" / "desktop_sessions" / f"{BRIDGE_SESSION_ID}.api.lock"
api_agent = None
api_agent_thread = None
api_lock = threading.RLock()  # serialize turns in this API process
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

def _session_history_from_disk():
    try:
        with SESSION_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return copy.deepcopy(data.get("llm_history") or [])
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[API] could not load agent session history: {exc}")
        return []

def _persist_api_history(history):
    """Atomically update only llm_history, preserving desktop messages/metadata."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = None
    try:
        if fcntl:
            lock = open(SESSION_LOCK_FILE, "a+", encoding="utf-8")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = {}
        if SESSION_FILE.exists():
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        data["llm_history"] = copy.deepcopy(history)
        data["updated_at"] = time.time()
        tmp = SESSION_FILE.with_suffix(SESSION_FILE.suffix + ".api.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, SESSION_FILE)
    finally:
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

# API-only dialogue store. Never write the desktop/native session history.
API_CHAT_FILE = script_dir / "temp" / "api_chat_history.json"


def _dialogue_text(message):
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def _clean_chat_reply(text):
    # Unwrap only the known final-answer envelope, not arbitrary JSON prose.
    value = strip_final_info_marker(text)
    value = re.sub(r"(?is)<summary\b[^>]*>.*?</summary>", "", value).strip()
    try:
        envelope = json.loads(value)
        if isinstance(envelope, dict) and isinstance(envelope.get("content"), str):
            value = envelope["content"]
    except (ValueError, TypeError):
        pass
    value = _clean_api_response(value)
    # Remove only a leading history metadata header, never timestamps in prose.
    value = re.sub(r"^\s*\[(?:时间未知（旧记录）|\d{4}-\d{2}-\d{2}[T ][^\]\n]+)\]\s*", "", value)
    value = re.sub(r"^\s*(?:【\.[^\n]*?\.】|【来源未标记】)\s*", "", value)
    return value.strip()


def _is_json_dict_string(s):
    if not isinstance(s, str):
        return False
    s_trimmed = s.strip()
    if s_trimmed.startswith("{") and s_trimmed.endswith("}"):
        try:
            val = json.loads(s_trimmed)
            return isinstance(val, dict)
        except Exception:
            return False
    return False


def _extract_wechat_record_info(text, default_timestamp=None):
    """
    Extracts clean conversation content, platform source tag, and timestamp from WeChat prompts.
    Returns (clean_text, source_marker, timestamp) or None if not a recognized WeChat prompt.
    """
    self_name, partner_name = _identity_names()
    if f"这是{self_name}和{partner_name}的微信私聊" in text or ("微信私聊" in text and "微信号：" in text):
        # Source marker is authored by the WeChat private-chat service and travels
        # inside the prompt as a 【.xxx.】 tag; GA only extracts it (never hardcodes).
        m_src = re.search(r"【\.([^】\n]*?)\.】", text)
        src = f"【.{m_src.group(1)}.】" if m_src else "【.微信私聊.】"
        target_part = ""
        if "需要综合回复的新消息：" in text:
            target_part = text.split("需要综合回复的新消息：", 1)[1]
        elif "【新消息】" in text:
            target_part = text.split("【新消息】", 1)[1]
        else:
            target_part = text

        lines = [l.strip() for l in target_part.splitlines() if l.strip()]
        valid_lines = []
        msg_ts = None
        for l in lines:
            if l.startswith("只输出严格") or l.startswith("历史背景") or l.startswith("【历史"):
                break
            m_full = re.search(r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\])?\s*(?:【来源微信私聊：([^｜]+).*?】)?\s*(.*)", l)
            if m_full:
                t_str, nick, body = m_full.group(1), m_full.group(2), m_full.group(3).strip()
                if t_str and not msg_ts:
                    msg_ts = t_str
                self_name, partner_name = _identity_names()
                speaker = nick.strip() if nick else partner_name
                if body and not body.startswith("只输出严格"):
                    valid_lines.append(f"{speaker}: {body}")
            else:
                if not l.startswith("【") and not l.startswith("只输出严格"):
                    valid_lines.append(l)

        content = "\n".join(valid_lines) if valid_lines else target_part[:300].strip()
        ts = msg_ts or default_timestamp
        return content, src, ts

    if "来源微信群：" in text or "眼前的群聊，是你和大家身处的公共空间" in text or "现在已经处于高频互动中" in text:
        m_group = re.search(r"来源微信群：([^｜|\n]+)", text)
        group_name = m_group.group(1).strip() if m_group else ""
        src = f"【.微信群聊·{group_name}.】" if group_name else "【.微信群聊.】"

        target_part = ""
        if "需要回复的新消息：" in text:
            target_part = text.split("需要回复的新消息：", 1)[1]
        elif "【新消息】" in text:
            target_part = text.split("【新消息】", 1)[1]
        elif "【最近消息】" in text:
            target_part = text.split("【最近消息】", 1)[1]
        else:
            target_part = text

        lines = [l.strip() for l in target_part.splitlines() if l.strip()]
        valid_lines = []
        msg_ts = None
        for l in lines:
            if l.startswith("只输出严格") or l.startswith("历史背景") or l.startswith("可回应的群成员"):
                break
            # Check for current standard format: 【来源微信群：群名｜昵称｜角色...】
            m_new = re.search(r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\])?\s*【来源微信群：[^｜\n]+｜([^｜\n]+)｜([^】\n]+)】\s*(.*)", l)
            if m_new:
                t_str, nick, role_info, body = m_new.group(1), m_new.group(2).strip(), m_new.group(3).strip(), m_new.group(4).strip()
                if t_str and not msg_ts:
                    msg_ts = t_str
                speaker_role = "言言" if "言言" in role_info else "群友"
                speaker_tag = f"[{nick}｜{speaker_role}]"
                if body and not body.startswith("只输出严格"):
                    valid_lines.append(f"{speaker_tag}: {body}")
                continue

            m_full = re.search(r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\])?\s*(?:【[^】]*群昵称：([^｜]+)[^】]*】)?\s*(.*)", l)
            if m_full:
                t_str, nick, body = m_full.group(1), m_full.group(2), m_full.group(3).strip()
                if t_str and not msg_ts:
                    msg_ts = t_str
                if nick and body:
                    speaker_tag = f"[{nick.strip()}｜群友]"
                    valid_lines.append(f"{speaker_tag}: {body}")
                elif body and not body.startswith("只输出严格"):
                    valid_lines.append(body)
            else:
                if not l.startswith("【") and not l.startswith("只输出严格"):
                    valid_lines.append(l)

        content = "\n".join(valid_lines) if valid_lines else target_part[:300].strip()
        ts = msg_ts or default_timestamp
        return content, src, ts

    return None


def _dialogue_record(role, text, source="", timestamp=None):
    # Preserve old timestamps; never invent dates for imported conversation.
    wechat_info = _extract_wechat_record_info(text, timestamp)
    if wechat_info:
        text, source, timestamp = wechat_info
    else:
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2}[^] ]*(?: [^]]+)?)\]\s*", text)
        if match:
            timestamp = match.group(1)
            text = text[match.end():]
        marker = _extract_platform_marker(text)
        if marker:
            source = marker
            text = text.replace(marker, "", 1).lstrip()
    return {"role": role, "content": text, "source": source,
            "timestamp": timestamp}


def _load_api_dialogue():
    if API_CHAT_FILE.exists():
        return json.loads(API_CHAT_FILE.read_text(encoding="utf-8"))["messages"]
    # One-time read-only import. Do not follow temporary prompt file pointers.
    records, pending, answer = [], None, None
    def flush():
        if pending and answer:
            records.extend([pending, _dialogue_record("assistant", answer, pending["source"])])
    for message in _session_history_from_disk():
        role, text = message.get("role"), _dialogue_text(message).strip()
        blocks = message.get("content", [])
        if isinstance(blocks, list) and any(
                isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result")
                for b in blocks):
            continue
        if role == "user":
            flush()
            pending, answer = None, None
            if "Long user prompt saved to" in text or not text:
                continue
            if "【用户消息】" in text:
                text = text.rsplit("【用户消息】", 1)[-1].strip()
            pending = _dialogue_record("user", text)
        elif role == "assistant" and pending:
            reply = _clean_chat_reply(text)
            if reply:
                answer = reply
    flush()
    return records


def _clean_history_content(content: str, role: str) -> str:
    """提取送入模型的历史消息纯文本。
    - assistant 历史：解包残留 JSON 结构、剔除思维链标签；
    - user 历史中的群聊记录：群监控每次都会把最新的整包【群聊近期上下文】随当轮 query 发送。
      为了避免在历史队列中重复叠加大段旧背景导致滚雪球，对历史里的群聊轮次提取其当轮真实新增对话；
      私聊记录和非群快照内容则 100% 完整保留。
    - 当轮 query（最新的群历史上下文）不受此函数影响，保持完整送模。
    """
    if not isinstance(content, str) or not content:
        return ""
    text = content.strip()
    if role == "assistant":
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    text = data.get("content") or data.get("reply") or text
            except Exception:
                pass
        text = re.sub(r"<(?:think|thought|summary)>.*?</(?:think|thought|summary)>", "", text, flags=re.DOTALL).strip()
    elif role == "user":
        if "【群聊近期上下文】" in text and "【本轮待处理的新增内容】" in text:
            parts = text.split("【本轮待处理的新增内容】", 1)
            new_dialogue = parts[1]
            new_dialogue = re.sub(r"^（[^）]*）：\s*", "", new_dialogue).strip()
            m = re.match(r"^(\[[^\]]+\])?", text)
            time_tag = (m.group(1) + " ") if m and m.group(1) else ""
            text = f"{time_tag}[群聊] {new_dialogue}"
    return text.strip()


def _api_max_turns():
    cfg = load_config()
    try:
        value = cfg.get("chat_history", {}).get("max_turns", CONFIG.get("chat_history", {}).get("max_turns", 15))
        turns = int(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Configure chat_history.max_turns in ga_api_config.json") from exc
    if turns < 1:
        raise RuntimeError("chat_history.max_turns must be a positive integer")
    return turns


def _api_chat_history(records=None):
    records = _load_api_dialogue() if records is None else records
    history = []
    for record in records[-2 * _api_max_turns():]:
        stamp = record.get("timestamp") or "时间未知（旧记录）"
        marker = record.get("source") or "【来源未标记】"
        clean_text = _clean_history_content(record.get("content", ""), record.get("role", "user"))
        text = f"[{stamp}] {marker} {clean_text}"
        history.append({"role": record["role"],
                        "content": [{"type": "text", "text": text}]})
    return history


def _save_api_dialogue(records):
    API_CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = API_CHAT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "messages": records,
                               "updated_at": datetime.now().astimezone().isoformat()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, API_CHAT_FILE)


def _run_api_chat_turn(query, instructions="", attachments=None):
    # Serialise load -> native execution -> save as one transaction.
    with api_lock:
        if api_agent is None or api_agent.is_running:
            raise HTTPException(status_code=503, detail="GenericAgent is busy")
        records = _load_api_dialogue()
        api_agent.llmclient.backend.history = _api_chat_history(records)
        original_extra = list(api_agent.extra_sys_prompts)
        _self_name, _partner_name = _identity_names()
        api_agent.extra_sys_prompts.append(
            "【API 历史元信息约定】历史对白开头的时间戳、来源暗号、来源未标记和时间未知标签"
            "仅用于识别历史来源，不是回复正文格式。生成正文不得复制这些前缀；"
            "新回复生成时间由 API 自动记录，不要自行生成或猜测元信息。\n"
            "【群聊发言人识别铁律】历史或当前消息中，每条微信群聊发言均已标注其真实完整的群昵称与角色（格式为 `[真实群昵称｜群友]: ...` 或 `[言言｜言言]: ...`）。\n"
            f"- 必须看清每句话最前面的具体昵称，准确分辨是谁说的话、在回复谁或提到了谁；\n"
            f"- 凡是标记为 `[群友]` 的均属于群内其他具体成员，不是{_partner_name}；唯有明确标记为 `[{_partner_name}]` 的，才是{_partner_name}本人发言！\n"
            f"- 严禁将其他群成员的发言、观点或动作安在{_partner_name}头上；在群聊中决定回复或 @ 某人时，必须使用对方具体的真实昵称。\n"
            f"【沉浸第一人称约定】与{_partner_name}对话交流或自称时，必须始终使用第一人称“我”，"
            f"严禁在对白中以第三人称（如“{_self_name}”）称呼自己（如严禁出现“{_self_name}就在旁边”、“{_self_name}觉得”等）。")
        if instructions:
            api_agent.extra_sys_prompts.append(
                "【API 当轮特殊指令及召回内容，仅本轮有效】\n" + instructions)
        received_at = datetime.now().astimezone().isoformat()
        try:
            raw_reply = _run_api_turn_native(query, attachments=attachments)
            reply = _clean_chat_reply(raw_reply)
            # The native 'done' queue item precedes its finally/status reset.
            deadline = time.monotonic() + 5
            while api_agent.is_running and time.monotonic() < deadline:
                time.sleep(0.01)
            if api_agent.is_running:
                raise HTTPException(status_code=503, detail="Native turn still finishing")
            
            # Check if this turn resulted in an empty reply or negative group decision
            # e.g. {"continue": false, "content": ""}
            is_silent = not reply.strip()
            if not is_silent:
                try:
                    parsed_json = json.loads(reply)
                    if isinstance(parsed_json, dict) and not str(parsed_json.get("content", "")).strip():
                        is_silent = True
                except (ValueError, TypeError):
                    pass

            if not is_silent:
                current = _dialogue_record("user", query, timestamp=received_at)
                records.extend([current, {
                    "role": "assistant", "content": reply,
                    "source": current["source"],
                    "timestamp": datetime.now().astimezone().isoformat()}])
                _save_api_dialogue(records)
            else:
                print(f"[_run_api_chat_turn] Silent turn or empty response; not recording to persistent history.")
            return raw_reply if _is_json_dict_string(raw_reply) else reply
        finally:
            # Tool protocol remains untouched during execution. Only rebuild
            # next-turn API dialogue after the native run has fully finished.
            def restore():
                api_agent.extra_sys_prompts[:] = original_extra
                api_agent.llmclient.backend.history = _api_chat_history(records)
            if not api_agent.is_running:
                restore()
            else:
                # A timed-out native run may still be using its tool history.
                # Defer cleanup; all new API turns remain rejected while busy.
                def deferred_restore():
                    with api_lock:
                        while api_agent.is_running:
                            time.sleep(0.1)
                        restore()
                threading.Thread(target=deferred_restore, daemon=True).start()


def _start_api_agent():
    global api_agent, api_agent_thread
    api_agent = GenericAgent()
    disk_history = _api_chat_history()
    if api_agent.llmclient is not None:
        api_agent.llmclient.backend.history = disk_history
    api_agent.no_print = True
    api_agent.inc_out = False
    api_agent_thread = threading.Thread(target=api_agent.run, name="ga-api-agent", daemon=True)
    api_agent_thread.start()
    print(f"   Independent GenericAgent started; agent session={BRIDGE_SESSION_ID}, history={len(disk_history)}")

@asynccontextmanager
async def lifespan(app):
    """Start the official GenericAgent directly; desktop UI is not required."""
    _start_api_agent()
    yield
    print("🛑 Shutting down independent API GenericAgent...")

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
    """独立 API 健康检查；不探测也不依赖桌面 Bridge。"""
    return {
        "status": "ok",
        "agent_running": bool(api_agent and getattr(api_agent, "handler", None)),
        "agent_busy": bool(api_agent and api_agent.is_running),
        "transport": "independent_genericagent",
        "session": BRIDGE_SESSION_ID,
        "history_file": str(API_CHAT_FILE),
        "chat_history_max_turns": _api_max_turns(),
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


async def _sse_chat_stream_with_heartbeat(chat_id: str, created: int, model: str, task: asyncio.Task, heartbeat_interval: float = 12.0):
    """OpenAI chat.completion.chunk SSE with keep-alive comment frames during model thinking."""
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"role": "assistant", "content": ""}))
    
    # 当上游桌面端或大模型在思考/生成时，每隔 heartbeat_interval 秒发送 SSE 注释帧保活
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat_interval)
            break
        except asyncio.TimeoutError:
            # 标准 SSE 注释帧，Cloudflare 识别为数据活动重置 100s 超时计数器，客户端安全忽略
            yield ": keepalive\n\n"
            
    full_response = await task
    content = _clean_api_response(full_response)
    for piece in _iter_content_chunks(content):
        if piece:
            yield _sse_data(_openai_chat_chunk(chat_id, created, model, {"content": piece}))
            await asyncio.sleep(0)
    yield _sse_data(_openai_chat_chunk(chat_id, created, model, {}, finish_reason="stop"))
    yield _sse_data(None)


def _clean_api_response(text: str) -> str:
    """API 对外只返回桌面端当前可见的最后一轮正文，不暴露工具过程/摘要。"""
    value = strip_final_info_marker(text)
    # 与桌面端 splitContentTurnSegs 同源：只取最后一个轮次。
    parts = re.split(r'\**LLM Running \(Turn \d+\) \.\.\.\**', value, flags=re.IGNORECASE)
    value = (parts[-1] if parts else value)
    # 只处理对外文本，不改变 Agent 执行/历史/附件。嵌套及未闭合块均保守隐藏。
    hidden_tags = (
        r'summary|think|thinking|thought|analysis|reasoning|reasoning_content|'
        r'function_calls?|function_results?|tool_calls?|tool_results?'
    )
    token_re = re.compile(
        rf'<\s*(/?)\s*({hidden_tags})\b[^>]*>', re.IGNORECASE
    )
    chunks, stack, cursor = [], [], 0
    for match in token_re.finditer(value):
        if not stack:
            chunks.append(value[cursor:match.start()])
        name = match.group(2).lower()
        if match.group(1):
            if name in stack:
                # 异常嵌套也不提前释放外层隐藏区。
                index = len(stack) - 1 - stack[::-1].index(name)
                del stack[index:]
        elif not re.search(r'/\s*>$', match.group(0)):
            stack.append(name)
        cursor = match.end()
    if not stack:
        chunks.append(value[cursor:])
    value = ''.join(chunks)
    # 截断在标签本身中间时，不将协议碎片交付客户端。
    value = re.sub(
        rf'<\s*/?\s*(?:{hidden_tags})\b[^>]*\Z', '', value,
        flags=re.IGNORECASE,
    )
    # 兼容 GA 文本化协议块：🛠️ Tool / Tool Result 到下一段正文前均隐藏。
    value = re.sub(r'(?ims)^\s*(?:🛠️\s*)?(?:Tool|工具)\s*:.*?(?=\n\s*\n|\Z)', '', value)
    value = re.sub(r'(?ims)^\s*(?:🛠️\s*)?(?:Tool Result|工具结果)\s*:.*?(?=\n\s*\n|\Z)', '', value)

    # 拦截纯 JSON 控制块泄露（如 ```json{"content": ""}``` 或裸 {"continue": false, "content": ""}）
    # 避免静默/留白或解析兜底失败时原始协议穿透给前端
    def _strip_control_json(s: str) -> str:
        s_clean = s.strip()
        if not s_clean:
            return ""
        # 剥离可能存在的 markdown 代码块
        unwrapped = s_clean
        if unwrapped.startswith("```"):
            unwrapped = re.sub(r"^```(?:json)?\s*", "", unwrapped, flags=re.I)
            unwrapped = re.sub(r"\s*```$", "", unwrapped).strip()
        m = re.search(r"\{[\s\S]*\}", unwrapped)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    ctrl_keys = {"continue", "audience", "reply_to", "mention", "content", "reason", "decision"}
                    # 如果字段属于纯控制结构
                    if set(data.keys()).issubset(ctrl_keys | {"thinking", "thought"}):
                        content_val = data.get("content", data.get("reply", data.get("message", None)))
                        if content_val is not None:
                            return str(content_val).strip()
                        return ""
            except Exception:
                pass
        return s

    value = _strip_control_json(value)
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
            if msg.role in ("system", "developer"):
                text = _text_content(msg.content)
                if text.strip():
                    values.append(text)
                    if not platform_marker:
                        platform_marker = _extract_platform_marker(text)
                # Keep every system/developer instruction for this turn only.
    
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


# ==================== 附件直传（图片 + 文件） ====================

_UPLOAD_ROOT = script_dir / "temp" / "api_uploads"
_UPLOAD_MAX_BYTES = 40 * 1024 * 1024
_INLINE_TEXT_MAX = 20000
_INLINE_TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log",
                     ".yaml", ".yml", ".ini", ".toml", ".py", ".js", ".ts", ".html", ".xml"}


def _prune_upload_dirs(max_age_seconds=86400):
    """Drop per-turn upload dirs left behind by older turns."""
    try:
        now = time.time()
        for entry in _UPLOAD_ROOT.iterdir():
            try:
                if entry.is_dir() and now - entry.stat().st_mtime > max_age_seconds:
                    import shutil
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _inline_text_payload(name, mime, raw):
    """Return decoded text for small text-like uploads, else None."""
    ext = os.path.splitext(name)[1].lower()
    if not (mime.startswith("text/") or ext in _INLINE_TEXT_EXTS):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    if len(text) > _INLINE_TEXT_MAX:
        text = text[:_INLINE_TEXT_MAX] + f"\n…（内容过长，已截断，完整文件在 {name}）"
    return text


def _materialize_uploaded_files(uploads):
    """Write inline base64 uploads to disk so GA tools can read them.

    Returns a list of {path, name, mime, text}; text is the decoded body for
    small text-like files (None for binary/large ones). Never raises: a bad
    entry is skipped with a log line instead of failing the whole chat turn.
    """
    if not isinstance(uploads, (list, tuple)) or not uploads:
        return []
    _prune_upload_dirs()
    turn_dir = None
    saved = []
    for entry in uploads:
        if not isinstance(entry, dict):
            continue
        name = os.path.basename(str(entry.get("name") or entry.get("filename") or "upload.bin"))
        mime = str(entry.get("mime_type") or entry.get("mime") or
                   mimetypes.guess_type(name)[0] or "application/octet-stream")
        payload = entry.get("data_base64") or entry.get("data") or ""
        raw = None
        if isinstance(payload, str) and payload:
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", payload), validate=False)
            except (ValueError, binascii.Error) as exc:
                print(f"[_run_api_turn] file decode failed for {name}: {exc}")
                continue
        else:
            local = entry.get("path")
            if isinstance(local, str) and local and os.path.isfile(local):
                try:
                    with open(local, "rb") as fh:
                        raw = fh.read()
                except OSError as exc:
                    print(f"[_run_api_turn] file read failed for {local}: {exc}")
                    continue
        if not raw:
            continue
        if len(raw) > _UPLOAD_MAX_BYTES:
            print(f"[_run_api_turn] file skipped (too large): {name} {len(raw)} bytes")
            continue
        if turn_dir is None:
            _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            turn_dir = tempfile.mkdtemp(prefix="turn-", dir=str(_UPLOAD_ROOT))
        path = os.path.join(turn_dir, name)
        try:
            with open(path, "wb") as fh:
                fh.write(raw)
        except OSError as exc:
            print(f"[_run_api_turn] file write failed for {name}: {exc}")
            continue
        saved.append({"path": path, "name": name, "mime": mime,
                      "text": _inline_text_payload(name, mime, raw)})
    return saved


def _patch_chat_for_images(client, image_blocks):
    """Inject base64 image blocks into this turn's first LLM call.

    NativeToolClient.chat() drops blocks without a text field before calling
    backend.ask(), so image blocks must be appended at the backend level.
    Same idea as frontends/desktop_bridge.py::_patch_chat_for_images, but the
    API backend is usually a MixinSession facade, whose ask() is read-only;
    in that case the routed implementation (_ask_impl) is wrapped instead.
    Returns a callable that restores the original call path.
    """
    if not image_blocks:
        return lambda: None
    try:
        from llmcore import NativeToolClient, MixinSession
    except Exception as exc:
        print(f"[_run_api_turn] cannot import llmcore classes: {exc}")
        return lambda: None
    if not isinstance(client, NativeToolClient):
        print(f"[_run_api_turn] client {type(client).__name__} is not NativeToolClient; images not injected")
        return lambda: None

    backend = client.backend
    state = {"injected": False}

    def _inject(prompt):
        state["injected"] = True
        try:
            if isinstance(prompt, dict) and isinstance(prompt.get("content"), list):
                prompt["content"].extend(image_blocks)
                print(f"[_run_api_turn] injected {len(image_blocks)} image block(s) into the first LLM call")
            else:
                print("[_run_api_turn] first LLM call had no content list; images not injected")
        except Exception as exc:
            print(f"[_run_api_turn] image injection failed: {exc}")

    # Path A: mixin facade — wrap the routed ask implementation.
    if isinstance(backend, MixinSession):
        previous_impl = backend._ask_impl

        def patched_impl(session, prompt):
            if not state["injected"]:
                _inject(prompt)
            resp = yield from previous_impl(session, prompt)
            return resp

        try:
            backend._ask_impl = patched_impl
        except AttributeError as exc:
            print(f"[_run_api_turn] cannot patch mixin ask impl: {exc}")
            return lambda: None
        print(f"[_run_api_turn] image patch installed on MixinSession ({backend.current_name})")

        def restore():
            if backend.__dict__.get("_ask_impl") is patched_impl:
                backend._ask_impl = previous_impl

        return restore

    # Path B: plain session — wrap backend.ask.
    previous = getattr(backend, "ask")
    had_own = "ask" in backend.__dict__

    def patched_ask(msg):
        if not state["injected"]:
            _inject(msg)
        resp = yield from previous(msg)
        return resp

    try:
        backend.ask = patched_ask
    except AttributeError as exc:
        print(f"[_run_api_turn] cannot patch backend.ask: {exc}")
        return lambda: None
    print(f"[_run_api_turn] image patch installed on {type(backend).__name__}.ask")

    def restore():
        if backend.__dict__.get("ask") is patched_ask:
            if had_own:
                backend.ask = previous
            else:
                try:
                    del backend.ask
                except AttributeError:
                    pass

    return restore


def _run_api_turn_native(query, initial_user_content=None, attachments=None):
    """Run one turn on the official GenericAgent without contacting desktop Bridge."""
    global api_agent
    with api_lock:
        if api_agent is None:
            raise HTTPException(status_code=503, detail="GenericAgent is not ready")
        prompt = initial_user_content if initial_user_content is not None else query
        # Collect attached images (materialized local paths) into base64 blocks so
        # the native GA turn can feed them to the multimodal model. Without this the
        # image blocks are silently dropped before reaching the LLM client.
        image_blocks = []
        attachment_notes = []
        if isinstance(attachments, dict):
            for meta in (attachments.get("imageMetas") or []):
                path = meta.get("path") if isinstance(meta, dict) else meta
                if not isinstance(path, str) or not path:
                    continue
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    mime = mimetypes.guess_type(path)[0] or "image/png"
                    image_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(raw).decode(),
                        },
                    })
                except Exception as exc:
                    print(f"[_run_api_turn] image attach failed for {path}: {exc}")
            uploads = attachments.get("fileUploads") or attachments.get("files") or []
            saved_files = _materialize_uploaded_files(uploads)
            for item in saved_files:
                attachment_notes.append(
                    f"- {item['name']}（{item['mime']}）→ {item['path']}")
                if item.get("text") is not None:
                    attachment_notes.append(
                        f"  文件内容如下：\n<<<FILE {item['name']}>>>\n{item['text']}\n<<<END FILE>>>")
        if attachment_notes:
            note = ("\n\n【本轮附件】用户随消息上传了以下文件，内容已直接附在下方；"
                    "小文本文件无需再调用工具读取，二进制/大文件可按路径读取：\n"
                    + "\n".join(attachment_notes))
            prompt = str(prompt) + note
        if image_blocks:
            hint = (f"\n\n【本轮图片】本消息已直接附带 {len(image_blocks)} 张图片，"
                    "图片内容随消息一起送达，可直接查看。无需再用任何工具去微信/远程电脑/WeFlow 取图或 OCR。")
            prompt = str(prompt) + hint
        print(f"[_run_api_turn] independent GenericAgent prompt (first 150 chars): {str(prompt)[:150]} "
              f"(images={len(image_blocks)}, files={len(attachment_notes)})")
        # 只读快照，避免异常结束时误取上一轮回复。
        before_history = api_agent.llmclient.backend.history
        previous_last = before_history[-1] if before_history else None
        restore_images = _patch_chat_for_images(api_agent.llmclient, image_blocks)
        try:
            outq = api_agent.put_task(prompt, source="api", images=image_blocks)
            deadline = time.time() + 600
            full = ""
            while time.time() < deadline:
                try:
                    item = outq.get(timeout=1.0)
                except queue.Empty:
                    continue
                if item.get("next"):
                    full += str(item["next"])
                if "done" in item:
                    done = item.get("done") or full
                    # done is authoritative; next chunks may be cumulative or incremental.
                    if done:
                        full = str(done)
                    # 原生流可能将 thinking_delta 无标签拼入 done；只在本轮
                    # 最终消息确有结构化思考块时改用 text 块，绝不修改历史。
                    history = api_agent.llmclient.backend.history
                    last = history[-1] if history else None
                    if (isinstance(last, dict) and last is not previous_last
                            and last.get("role") == "assistant"):
                        blocks = last.get("content")
                        if isinstance(blocks, list) and any(
                            isinstance(b, dict) and b.get("type") in
                            ("thinking", "redacted_thinking", "reasoning")
                            for b in blocks
                        ):
                            full = "".join(
                                b.get("text", "") for b in blocks
                                if isinstance(b, dict) and b.get("type") == "text"
                                and isinstance(b.get("text"), str)
                            )
                            print("[API] Structured final text selected; thinking excluded")
                    return strip_final_info_marker(full)
            raise HTTPException(status_code=504, detail="GenericAgent response timeout")
        finally:
            restore_images()


# ==================== /llm 模型切换命令 ====================
# 任何调用本 API 的平台，只要在对话里发送 "/llm" 或 "/llm <编号>"，
# 即可查看 / 切换底层真正工作的模型（与飞书 /llm 行为一致）。
# 命令不会进入对话历史，也不会占用模型的上下文。

_LLM_CMD_RE = re.compile(r'^\s*/llm(?:\s+(\S+))?\s*$', re.IGNORECASE)


def _is_llm_command(text: str) -> bool:
    """判断一条用户消息是否为 /llm 命令。"""
    return bool(_LLM_CMD_RE.match(_text_content(text or "")))


def _handle_llm_command(text: str) -> str:
    """执行 /llm 命令并返回给用户的文本。

    /llm        -> 列出所有可用模型（→ 标记当前）
    /llm <编号> -> 切换到指定编号的模型
    """
    global api_agent
    m = _LLM_CMD_RE.match(_text_content(text or ""))
    if not m:
        return "用法: /llm 查看模型列表；/llm <编号> 切换模型"
    arg = m.group(1)
    with api_lock:
        if api_agent is None or not getattr(api_agent, "llmclient", None):
            return "❌ 当前没有可用的 LLM 配置"
        try:
            if arg is None:
                items = api_agent.list_llms()
                if not items:
                    return "❌ 当前没有可用的 LLM 配置"
                lines = [f"{'→' if cur else '  '} [{i}] {name}"
                         for i, name, cur in items]
                return ("LLMs:\n" + "\n".join(lines)
                        + f"\n\n当前: [{api_agent.llm_no}] {api_agent.get_llm_name()}"
                        + "\n发送 /llm <编号> 切换。")
            try:
                no = int(arg)
            except ValueError:
                return f"用法: /llm <0-{len(api_agent.list_llms()) - 1}>"
            total = len(api_agent.list_llms())
            if no < 0 or no >= total:
                return f"❌ 编号超出范围，可用: 0-{total - 1}"
            api_agent.next_llm(no)
            return f"✅ 已切换到 [{api_agent.llm_no}] {api_agent.get_llm_name()}"
        except Exception as exc:
            return f"❌ 切换失败: {exc}"


def _llm_command_response(text: str, model: str, stream: bool):
    """把 /llm 命令结果包成与普通回复一致的响应（兼容流式/非流式）。"""
    reply = _handle_llm_command(text)
    created = int(time.time())
    if stream:
        chat_id = f"chatcmpl-llm-{created}"
        return StreamingResponse(
            _sse_chat_stream(chat_id, created, model, reply),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )
    return _openai_chat_completion(f"chatcmpl-llm-{created}", created, model, reply)


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

    # 仅扫描真实用户发言正文；命中知识库标题才追加词条，闲聊或人设规则不注入。
    lore_query = _extract_lore_search_query(messages, default_query=query)
    lore_text = _search_lore_sync(lore_query)
    if lore_text:
        instruction_text = f"{instruction_text}\n\n{lore_text}".strip()
        print(f"[_execute_api_turn] Injected lore worldbook ({len(lore_text)}ch)")

    reply = await asyncio.to_thread(
        _run_api_chat_turn, query, instruction_text, attachments
    )

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
    # /llm 命令：任何调用方均可查看/切换底层模型，不进入对话历史
    if _is_llm_command(user_message):
        reply = _handle_llm_command(user_message)
        model = body.get("model", CONFIG["model"]["id"])
        return {"id": f"resp-{int(time.time())}", "object": "response",
                "created_at": int(time.time()), "model": model,
                "output": [{"type": "message", "id": f"msg-{int(time.time())}",
                            "role": "assistant", "content": [{"type": "output_text", "text": reply}]}],
                "output_text": reply, "status": "completed"}
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
    # /llm 命令：任何调用方均可查看/切换底层模型，不进入对话历史
    if _is_llm_command(prompt):
        text = _handle_llm_command(prompt)
        created = int(time.time())
        cmpl_id = f"cmpl-llm-{created}"
        model = body.get("model", CONFIG["model"]["id"])
        if body.get("stream"):
            return StreamingResponse(
                _sse_text_completion_stream(cmpl_id, created, model, text),
                media_type="text/event-stream",
                headers=_sse_headers(),
            )
        return {
            "id": cmpl_id, "object": "text_completion", "created": created,
            "model": model,
            "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
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

        # /llm 命令：任何调用方均可查看/切换底层模型，不进入对话历史
        if _is_llm_command(user_message):
            return _llm_command_response(user_message, request.model, bool(request.stream))

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
            "files": request.files, "fileUploads": getattr(request, "fileUploads", None),
            "imageMetas": request.imageMetas, "images": request.images
        }.items() if v}
        if content_image_metas:
            attachments["imageMetas"] = (attachments.get("imageMetas") or []) + content_image_metas

        created = int(time.time())
        chat_id = f"chatcmpl-{created}"

        # 异步启动生成任务
        turn_task = asyncio.create_task(
            _execute_api_turn(
                timestamped_message,
                request.messages,
                instructions=platform_instructions,
                attachments=attachments,
            )
        )

        if request.stream:
            # 流式传输：立即返回响应并在等待思考时持续输出 SSE 保活帧，彻底解决 Cloudflare 524 超时
            return StreamingResponse(
                _sse_chat_stream_with_heartbeat(chat_id, created, request.model, turn_task),
                media_type="text/event-stream",
                headers=_sse_headers(),
            )

        # 非流式传输直接等待完成
        full_response = await turn_task
        content = _clean_api_response(full_response)
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
