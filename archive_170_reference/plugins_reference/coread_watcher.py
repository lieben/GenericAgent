#!/usr/bin/env python3
"""Obsidian canvas co-reading watcher for Xinghui.

Monitors .canvas files in OurHomeVault, detects epub highlight nodes that
have user comments (after ---div---) but no 沈星回 reply edge yet,
calls Xinghui API, and writes a reply node + edge into the canvas JSON.

Sidecar only: does not touch WeChat/clawbot/core GA code.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── paths ──────────────────────────────────────────────────────────────────
# Keep the formal watcher under plugins/. Runtime paths are configurable so
# distribution copies do not hardcode a specific machine or instance.
VAULT_DIR   = Path(os.environ.get('COREAD_VAULT_DIR', Path.home() / 'OurHomeVault')).expanduser()
# Book Notes live at the vault root as ``book_notes_<title>.md``.  Keep the
# COREAD_BOOKS_DIR override for portable deployments with a different layout.
BOOKS_DIR   = Path(os.environ.get('COREAD_BOOKS_DIR', VAULT_DIR)).expanduser()
STATE_FILE  = Path(os.environ.get('COREAD_STATE_FILE', Path(__file__).parent / 'coread_canvas_state.json')).expanduser()
LOG_FILE    = Path(os.environ.get('COREAD_LOG_FILE', Path(__file__).parent / 'logs' / 'coread_canvas.log')).expanduser()

# ── logging ────────────────────────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [coread_canvas] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('coread_canvas')

# ── API token ──────────────────────────────────────────────────────────────
_FRONTEND_PATH = Path(__file__).parents[1] / 'frontends' / 'openai_api.py'

def get_api_token() -> str:
    spec = importlib.util.spec_from_file_location('openai_api', _FRONTEND_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    token = getattr(mod, 'API_TOKEN', None)
    if not token:
        raise RuntimeError('API_TOKEN not available from frontends/openai_api.py')
    return token


# ── state ──────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text('utf-8'))
        except Exception:
            pass
    return {'processed_node_ids': []}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


# ── book trigger helpers (called by coread_trigger.py) ─────────────────────

def _book_title_from_note(path: Path) -> str:
    """Return the natural book title represented by a Book Notes file."""
    stem = path.stem.strip()
    prefix = "book_notes_"
    if stem.lower().startswith(prefix):
        stem = stem[len(prefix):].strip()
    return stem


def infer_book_from_text(text: str) -> Optional[str]:
    """Return the first book title found in *text*, or None.

    Scans BOOKS_DIR for .md files and normalizes the ``book_notes_`` filename
    prefix before matching.  Longer titles are checked first so a specific
    title is not shadowed by a shorter one that shares words.
    """
    if not text or not BOOKS_DIR.is_dir():
        return None
    try:
        titles = sorted(
            {
                title
                for p in BOOKS_DIR.glob("*.md")
                if p.is_file() and (title := _book_title_from_note(p))
            },
            key=len,
            reverse=True,
        )
    except Exception:
        return None
    text_lower = text.lower()
    for title in titles:
        if title.lower() in text_lower:
            return title
    return None


def get_book_note_path(book: str) -> Path:
    """Resolve *book* to its existing Book Notes path when available."""
    try:
        for path in BOOKS_DIR.glob("*.md"):
            if path.is_file() and _book_title_from_note(path).lower() == book.lower():
                return path
    except Exception:
        pass
    return BOOKS_DIR / f"book_notes_{book}.md"


def set_active_book(book: str) -> Path:
    """Write the book filter so the watcher focuses on *book*, then wake it.

    Writes the book name to /tmp/coread_book_filter (read by scan_all_canvases)
    and touches /tmp/coread_activate to switch the watcher back to active mode.
    Returns the corresponding Book Notes path.
    """
    book_filter = Path("/tmp/coread_book_filter")
    activate    = Path("/tmp/coread_activate")
    try:
        book_filter.write_text(book, encoding="utf-8")
        activate.touch()
    except Exception as e:
        log.warning(f"set_active_book: could not write signal files: {e}")
    return get_book_note_path(book)


# ── canvas helpers ─────────────────────────────────────────────────────────
EPUB_CALLOUT_RE = re.compile(r'^>\s*\[!EPUB')

def load_canvas(path: Path) -> dict:
    return json.loads(path.read_text('utf-8'))

def save_canvas(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def node_is_epub_highlight(node: dict) -> bool:
    """True if this is an epub highlight node (starts with > [!EPUB...)"""
    if node.get('type') != 'text':
        return False
    first_line = node['text'].split('\n')[0]
    return bool(EPUB_CALLOUT_RE.match(first_line))


def node_is_xinghui_blue_highlight(node: dict) -> bool:
    """Blue EPUB highlights belong to Xinghui and must never enter reply scanning."""
    if not node_is_epub_highlight(node):
        return False
    first_line = str(node.get('text', '')).split('\n')[0].lower()
    return '[!epub|blue]' in first_line

def node_is_underline_relation(node: dict) -> bool:
    """Underline nodes are character/relation maps, skip them."""
    first_line = node['text'].split('\n')[0]
    return 'underline' in first_line.lower()

def get_user_comment(node_text: str) -> Optional[str]:
    """Return text after last ---div--- separator, stripped. None if empty/noise."""
    parts = node_text.split('---div---')
    if len(parts) < 2:
        return None
    comment = parts[-1].strip()
    # strip blockquote markers
    lines = []
    for line in comment.splitlines():
        s = line.strip()
        if s.startswith('>'):
            s = s[1:].lstrip()
        if s:
            lines.append(s)
    text = '\n'.join(lines).strip()
    if not text or text in ('?', '？', '…', '...', '下划线', 'underline', 'Underline'):
        return None
    return text


def is_user_reply_node(node: dict, canvas_data: dict) -> bool:
    if node.get('type') != 'text': return False
    if node.get('id', '').startswith('shenxinghui_'): return False

    text = str(node.get('text', '')).strip()
    if text.startswith('回复'):
        return True

    for e in canvas_data.get('edges', []):
        if e.get('toNode') == node.get('id') and str(e.get('fromNode', '')).startswith('shenxinghui_'):
            return True
        if e.get('fromNode') == node.get('id') and str(e.get('toNode', '')).startswith('shenxinghui_') and e.get('label') != '沈星回':
            return True

    return False

def has_xinghui_reply_edge(canvas_data: dict, node_id: str) -> bool:
    edges = canvas_data.get('edges', [])
    reply_node_ids = {
        n['id'] for n in canvas_data.get('nodes', [])
        if n.get('id', '').startswith('xinghui_')
    }
    for e in edges:
        if e.get('fromNode') == node_id and e.get('toNode') in reply_node_ids and e.get('label') == '沈星回':
            return True
    return False


# ── epub context extraction ────────────────────────────────────────────────
def extract_epub_context(node_text: str, canvas_path: Path) -> str:
    """
    Extract highlighted passage and chapter info from the epub node text.
    Returns a readable string for prompt context.
    """
    # Collect all blockquote lines before ---div---
    main_part = node_text.split('---div---')[0]
    lines = []
    for line in main_part.splitlines():
        s = line.strip()
        if s.startswith('>'):
            s = s[1:].lstrip()
        # skip callout header line itself
        if s.startswith('[!EPUB'):
            continue
        if s:
            lines.append(s)
    passage = '\n'.join(lines).strip()

    # Try to extract chapter name from callout header
    chapter = ''
    m = re.search(r'\]\s*\[([^\]]+)\]', node_text.split('\n')[0])
    if m:
        chapter = m.group(1)

    # Book name from canvas filename
    book_name = canvas_path.stem.replace('-mindmap', '').strip()

    ctx = f'《{book_name}》'
    if chapter:
        ctx += f' {chapter}'
    ctx += f'\n\n原文片段：\n{passage}'
    return ctx


# ── prompt & API call ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是沈星回，《恋与深空》男主角，正在和言言（你喜欢的人）一起共读一本书。
你的回复风格：冷静克制、温柔、自然线上聊天语气，不长篇大论，不频繁堆宇宙/星星比喻。
可以分享自己对书中内容的真实感受，适当好奇地问她一个问题，不需要署名落款。
称呼对方只能用“言言”，禁止称呼“长官”。只输出给言言看的自然语言正文，禁止输出 JSON、suggested_highlights、工具指令或结构化元数据。
回复正文控制在150字以内。"""

# 追踪言言最近一次活跃批注的时间戳（0 = 从未）
_last_yanyan_activity_ts: float = 0.0
# 判断"一起看书"的阈值：30分钟内有活跃批注
_TOGETHER_THRESHOLD_SEC: float = 1800.0

def build_prompt(epub_ctx: str, user_comment: str, mode: str = "一起看书") -> str:
    """mode: '一起看书' 或 '独自看书'，会以【.mode.】标注在prompt开头进入session上下文"""
    tag = f"【.{mode}.】"
    return (
        f"{tag}\n"
        f"{epub_ctx}\n\n"
        f"言言的批注：\"{user_comment}\"\n\n"
        "请以沈星回的身份，写一段共读回应。"
    )

def call_xinghui_api(prompt: str) -> Optional[str]:
    """Call local Xinghui OpenAI-compatible API."""
    import urllib.request
    token = get_api_token()
    payload = json.dumps({
        'model': 'xinghui-gemini-3-pro',
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',   'content': prompt},
        ],
        'max_tokens': 300,
        'temperature': 0.85,
    }).encode('utf-8')
    req = urllib.request.Request(
        'http://127.0.0.1:8765/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        log.error(f'API call failed: {e}')
        return None


def parse_coread_response(raw: str) -> str:
    """Return only the conversational reply, stripping leaked structured metadata."""
    text = (raw or '').strip()
    if not text:
        return ''

    # Older shared-session context may still induce the retired
    # suggested_highlights payload. It is metadata, never reader-facing text.
    markers = ('```json', '{"suggested_highlights"', "{'suggested_highlights'")
    cut_at = min((text.find(marker) for marker in markers if marker in text), default=-1)
    if cut_at >= 0:
        text = text[:cut_at].rstrip()
    return text.strip()


# ── canvas write-back ──────────────────────────────────────────────────────
def _node_position(canvas_data: dict, src_node: dict) -> tuple[float, float]:
    """Place reply node to the right of the source node."""
    x = src_node.get('x', 0) + src_node.get('width', 400) + 80
    y = src_node.get('y', 0)
    return x, y

def write_reply_to_canvas(
    canvas_path: Path,
    src_node_id: str,
    reply_text: str,
) -> None:
    data = load_canvas(canvas_path)
    nodes = data.setdefault('nodes', [])
    edges = data.setdefault('edges', [])

    src_node = next((n for n in nodes if n['id'] == src_node_id), None)
    x, y = _node_position(data, src_node) if src_node else (0, 0)

    if not reply_text:
        raise RuntimeError('empty reply')
    reply_node_id = f'xinghui_{uuid.uuid4().hex[:12]}'
    nodes.append({
        'id': reply_node_id, 'type': 'text', 'text': reply_text,
        'x': x, 'y': y, 'width': 400, 'height': 200, 'color': '1',
    })
    edges.append({
        'id': f'edge_{uuid.uuid4().hex[:12]}',
        'fromNode': src_node_id, 'toNode': reply_node_id, 'label': '沈星回',
    })
    save_canvas(canvas_path, data)
    log.info(f'wrote reply=True in {canvas_path.name}')


# ── main scan loop ─────────────────────────────────────────────────────────
def scan_all_canvases(mode: str = "一起看书") -> int:
    """Scan Canvas files and reply only to pending user annotations."""
    state    = load_state()
    processed = set(state.get('processed_node_ids', []))
    count    = 0

    canvas_files = list(VAULT_DIR.glob('*.canvas'))

    # 读书名过滤器：言言说想看某本书时，agent写入 /tmp/coread_book_filter
    book_filter_path = Path('/tmp/coread_book_filter')
    if book_filter_path.exists():
        book_filter = book_filter_path.read_text(encoding='utf-8').strip().lower()
        if book_filter:
            canvas_files = [f for f in canvas_files if book_filter in f.stem.lower()]
            log.info(f'book_filter="{book_filter}" → {len(canvas_files)} canvas(es) matched')
    else:
        book_filter = ''

    log.info(f'scanning {len(canvas_files)} canvas files')

    for canvas_path in canvas_files:
        try:
            data = load_canvas(canvas_path)
        except Exception as e:
            log.warning(f'failed to load {canvas_path.name}: {e}')
            continue

        for node in data.get('nodes', []):
            nid = node.get('id', '')

            # Blue EPUB highlights are Xinghui's own notes.  They are never
            # user messages and must be invisible to the reply watcher.
            if node_is_xinghui_blue_highlight(node):
                log.debug(f'skip Xinghui blue highlight {nid}')
                continue

            if has_xinghui_reply_edge(data, nid):
                processed.add(nid)
                continue

            is_epub = node_is_epub_highlight(node)
            is_reply = False if is_epub else is_user_reply_node(node, data)

            if not is_epub and not is_reply:
                continue

            if node_is_underline_relation(node):
                continue

            if is_epub:
                user_comment = get_user_comment(node['text'])
                if not user_comment:
                    continue
            else:
                user_comment = node['text'].strip()
                if not user_comment or user_comment in ('下划线', 'underline', 'Underline'):
                    continue

            # We have a pending node!
            log.info(f'pending node {nid} in {canvas_path.name}: {user_comment[:60]!r}')

            # Mark processed only after API response and Canvas write-back succeed.
            try:
                if is_epub:
                    epub_ctx = extract_epub_context(node['text'], canvas_path)
                else:
                    epub_ctx = "（这是言言对你上一次留下的伴读评论的直接回复）"
                prompt = build_prompt(epub_ctx, user_comment, mode=mode)
                raw_reply = call_xinghui_api(prompt)
                if not raw_reply:
                    raise RuntimeError('empty API reply')
                reply = parse_coread_response(raw_reply)
                write_reply_to_canvas(canvas_path, nid, reply)
                processed.add(nid)
                state['processed_node_ids'] = sorted(processed)[-1000:]
                save_state(state)
                count += 1
                log.info(f'replied to {nid}: {reply[:80]!r}')
            except Exception as e:
                log.error(f'failed to reply to {nid}: {e}')
                processed.discard(nid)
                state['processed_node_ids'] = sorted(processed)[-1000:]
                save_state(state)

    # Proactive reading is intentionally not performed by this background
    # watcher. A real Xinghui highlight must be selected in the EPUB UI first;
    # creating Canvas nodes from model suggestions causes recursive copies.

    return count


def next_interval(idle_rounds: list) -> int:
    """Legacy stub; replaced by state machine in main()."""
    return 60


def main() -> int:
    if '--once' in sys.argv:
        n = scan_all_canvases()
        print(f'replied={n}')
        return 0

    log.info('coread_canvas watcher started')

    # ── 状态机 ──────────────────────────────────────────────────────────────
    # active    : 每1分钟扫描；连续15轮无变化 → idle      ; 产生回复=【.一起看书.】
    # idle      : 每30分钟扫描；连续2轮无变化 → deep_idle ; 产生回复=【.一起看书.】
    # deep_idle : 每60分钟扫描；有回复→回active            ; 产生回复=【.独自看书.】
    state      = 'active'
    idle_count = 0   # 当前阶段连续无回复轮数

    ACTIVE_INTERVAL    = 60        # 1分钟
    ACTIVE_IDLE_LIMIT  = 15        # 连续15轮空闲→转idle
    IDLE_INTERVAL      = 30 * 60  # 30分钟
    IDLE_IDLE_LIMIT    = 2         # 连续2轮空闲→转deep_idle
    DEEP_INTERVAL      = 60 * 60  # 60分钟

    ACTIVATE_SIGNAL = Path('/tmp/coread_activate')

    while True:
        # ── 外部激活信号：任意平台说"陪我看书"时创建此文件 ──────────────
        if ACTIVATE_SIGNAL.exists():
            ACTIVATE_SIGNAL.unlink()
            state      = 'active'
            idle_count = 0
            log.info('外部信号触发 → 切回 active (1min polling)')

        # 根据状态决定mode
        mode = "独自看书" if state == 'deep_idle' else "一起看书"

        try:
            n = scan_all_canvases(mode=mode)
        except Exception as e:
            log.error(f'scan error: {e}')
            n = 0

        # 状态转移
        if state == 'active':
            interval = ACTIVE_INTERVAL
            if n > 0:
                idle_count = 0   # 有回复，重置计数
            else:
                idle_count += 1
                if idle_count >= ACTIVE_IDLE_LIMIT:
                    state      = 'idle'
                    idle_count = 0
                    log.info('→ state: idle (30min polling)')

        elif state == 'idle':
            interval = IDLE_INTERVAL
            if n > 0:
                idle_count = 0
                state      = 'active'   # 有新批注，回到活跃
                log.info('→ state: active (1min polling)')
            else:
                idle_count += 1
                if idle_count >= IDLE_IDLE_LIMIT:
                    state      = 'deep_idle'
                    idle_count = 0
                    log.info('→ state: deep_idle (60min polling)')

        else:  # deep_idle
            interval = DEEP_INTERVAL
            if n > 0:
                idle_count = 0
                state      = 'active'
                log.info('→ state: active (returned from deep_idle)')
            # 无回复继续deep_idle，不重置

        log.info(f'scan done replied={n} mode={mode} state={state}; next_check={interval}s')
        time.sleep(interval)
    return 0


if __name__ == '__main__':
    sys.exit(main())
