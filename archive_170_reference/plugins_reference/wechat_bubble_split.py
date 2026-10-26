import re
from typing import Optional, Iterable


def _hard_chunks(s: str, limit: int) -> list[str]:
    s = str(s or '').strip()
    if not s:
        return []
    return [s[i:i + limit] for i in range(0, len(s), limit)] if len(s) > limit else [s]


def _split_chat(text: str) -> list[str]:
    """Chat mode: natural private-chat bubbles.

    Old clawbot behavior:
    - explicit blank-line paragraphs are intentional bubbles;
    - when there is only one paragraph, split on sentence-ending punctuation or
      newlines into short WeChat-like bubbles;
    - keep emoji/image control labels isolated;
    - byte-limit splitting is only a safety fallback.
    """
    s = str(text or '').strip()
    if not s:
        return []

    def _add_piece(out: list[str], piece: str) -> None:
        piece = (piece or '').strip()
        if not piece:
            return
        out.extend(_hard_chunks(piece, 1200))

    def _is_long_form(one: str) -> bool:
        """Keep genuinely long/structured answers from becoming too fragmentary."""
        s2 = str(one or '').strip()
        if len(s2) >= 120:
            return True
        if '\n' in s2:
            return True
        if re.search(r'(^|\n)\s*(?:[-*•]|\d+[.)、])\s+', s2):
            return True
        if re.search(r'```|https?://|\[FILE:|\[IMAGE_PROMPT:', s2):
            return True
        return False

    def _soft_wrap_short_chat(piece: str, target: int = 20) -> list[str]:
        """For ordinary private chat, keep bubbles around <=20 chars when natural."""
        s2 = str(piece or '').strip()
        if len(s2) <= target or _is_long_form(s2):
            return [s2] if s2 else []
        out: list[str] = []
        cur = ''
        # Prefer natural pauses; only split by length when a clause is still too long.
        for ch in s2:
            cur += ch
            if ch in '，,、：: ' and len(cur.strip()) >= 8:
                out.append(cur.strip())
                cur = ''
            elif len(cur.strip()) >= target:
                out.append(cur.strip())
                cur = ''
        if cur.strip():
            out.append(cur.strip())
        # If wrapping creates too many tiny shards, fall back to the original sentence.
        if len(out) >= 5 and len(s2) < 90:
            return [s2]
        return out or ([s2] if s2 else [])

    def _split_sentence_like(one: str) -> list[str]:
        parts: list[str] = []
        cur = ''
        for ch in one:
            cur += ch
            if ch in '。！？!?；;…~～' or ch == '\n':
                if cur.strip():
                    parts.append(cur.strip())
                cur = ''
        if cur.strip():
            parts.append(cur.strip())
        return parts or ([one.strip()] if one.strip() else [])

    def _split_text_block(block: str) -> list[str]:
        paras = [p.strip() for p in re.split(r'\n\s*\n+', block) if p.strip()]
        if len(paras) >= 2:
            return [re.sub(r'\n+', '\n', p).strip() for p in paras if p.strip()]
        one = re.sub(r'\n+', '\n', paras[0] if paras else block).strip()
        pieces: list[str] = []
        for p in _split_sentence_like(one):
            pieces.extend(_soft_wrap_short_chat(p))
        return pieces

    parts: list[str] = []
    # Keep control labels alone, then apply old chat splitting to surrounding text.
    token_re = re.compile(r'(\[\[EMOJI:[^\]：|]+\]\]|\[IMAGE_PROMPT:[\s\S]*?\])')
    pos = 0
    for m in token_re.finditer(s):
        for p in _split_text_block(s[pos:m.start()]):
            _add_piece(parts, p)
        _add_piece(parts, m.group(0).strip())
        pos = m.end()
    for p in _split_text_block(s[pos:]):
        _add_piece(parts, p)
    return [p for p in parts if p.strip()]


def before_send_text(uid: str, text: str, context_token: Optional[str] = None) -> Optional[Iterable[str]]:
    """Last-mile display splitter for the single cross-dimensional WeChat chat.

    This hook changes only bubble boundaries. The official GA payload remains
    untouched semantically; no FINAL parsing or visible-text cleanup happens
    here.
    """
    if isinstance(text, (list, tuple)):
        out: list[str] = []
        for item in text:
            if isinstance(item, (list, tuple)):
                nested = before_send_text(uid, item, context_token)
                if nested:
                    out.extend([str(x) for x in nested if str(x).strip()])
                continue
            out.extend(_split_chat(str(item or '')))
        return out if out else None
    if not isinstance(text, str):
        return None
    parts = _split_chat(text)
    return parts if parts else None


def install(app) -> None:
    if hasattr(app, 'register_hook'):
        app.register_hook('before_send_text', before_send_text)
    elif hasattr(app, 'agent') and hasattr(app.agent, 'register_hook'):
        app.agent.register_hook('before_send_text', before_send_text)


def register(app) -> None:
    install(app)
