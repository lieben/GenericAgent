"""Co-reading natural trigger for WeChat/API frontends.

When Yanyan naturally mentions an existing book note title in an incoming
message, switch the Obsidian co-reading watcher to that book.  This plugin is
frontend-hook only: it does not touch WeChat GUI/send pipeline and does not
modify GA core.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WATCHER_PATH = ROOT / "plugins" / "coread_watcher.py"
LOG_PATH = ROOT / "temp" / "coread_trigger.log"

_LAST_KEY = ""
_LAST_TS = 0.0
_DEBOUNCE_SEC = float(os.environ.get("COREAD_TRIGGER_DEBOUNCE_SECONDS", "30"))


def _log(msg: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _load_watcher() -> Any:
    spec = importlib.util.spec_from_file_location("coread_watcher", WATCHER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load watcher: {WATCHER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("coread_watcher", mod)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _text_from_args(*args: Any, **kwargs: Any) -> str:
    if isinstance(kwargs.get("text"), str):
        return kwargs.get("text") or ""
    if isinstance(kwargs.get("incoming_text"), str):
        return kwargs.get("incoming_text") or ""
    if args:
        # HookApp after_message may be called as (msg_dict,) by WeChat or
        # as (uid, text=prompt) by shared API runtime.
        first = args[0]
        if isinstance(first, dict):
            for key in ("text", "content", "message"):
                val = first.get(key)
                if isinstance(val, str):
                    return val
        if len(args) >= 2 and isinstance(args[1], str):
            return args[1]
        if isinstance(first, str) and not kwargs:
            return first
    return ""


def _maybe_switch(text: str, source: str = "") -> dict[str, Any]:
    global _LAST_KEY, _LAST_TS
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}
    mod = _load_watcher()
    book = mod.infer_book_from_text(text)
    if not book:
        return {"ok": False, "reason": "no_book"}
    now = time.time()
    key = book.lower()
    if key == _LAST_KEY and now - _LAST_TS < _DEBOUNCE_SEC:
        return {"ok": True, "book": book, "debounced": True}
    path = mod.set_active_book(book)
    _LAST_KEY = key
    _LAST_TS = now
    _log(f"switch source={source or '-'} book={book!r} path={path} text={text[:160]!r}")
    return {"ok": True, "book": book, "path": str(path)}


def register(app: Any) -> bool:
    """Install on HookApp-compatible frontends."""
    if not hasattr(app, "register_hook"):
        return False

    def _after(*args: Any, **kwargs: Any) -> dict[str, Any]:
        text = _text_from_args(*args, **kwargs)
        source = str(kwargs.get("source") or "frontend.after_message")
        try:
            return _maybe_switch(text, source=source)
        except Exception as e:
            _log(f"ERROR {type(e).__name__}: {e}; text={text[:160]!r}")
            return {"ok": False, "reason": type(e).__name__}

    app.register_hook("wechat_incoming_message", _after)
    app.register_hook("after_message", _after)
    return True


# Exposed for direct tests.
trigger_from_text = _maybe_switch
