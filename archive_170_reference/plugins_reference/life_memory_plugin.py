"""Top-level loader for the reusable life-memory plugin.

GA only auto-discovers plugins/*.py, while the implementation lives in the
``plugins/life_memory/`` package.  These callbacks are opt-in and side-effect
light: prompt context is exposed as a side channel and must not be appended to
backend history.
"""
from __future__ import annotations

import os
from typing import Any, Dict

try:
    from plugins import hooks
    from plugins.life_memory.core import build_context, remember_text
except Exception:  # pragma: no cover
    hooks = None
    build_context = None
    remember_text = None


def _agent(ctx: Dict[str, Any]) -> str:
    raw = ctx.get("agent") or ctx.get("agent_name") or ctx.get("main_session_key") or os.environ.get("GA_AGENT") or "default"
    return str(raw).split(":")[0]


def _incoming(ctx: Dict[str, Any]) -> str:
    for k in ("incoming_text", "text", "message", "user_text", "content"):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    msg = ctx.get("msg")
    if isinstance(msg, dict):
        for k in ("text", "content"):
            v = msg.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _source(ctx: Dict[str, Any]) -> str:
    return str(ctx.get("source") or ctx.get("frontend") or ctx.get("channel") or "hook")


def before_prompt(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not build_context:
        return ctx
    extra = build_context(_agent(ctx), _incoming(ctx))
    if extra:
        existing = ctx.get("side_context") or ctx.get("prompt_context") or ""
        ctx["side_context"] = (str(existing).rstrip() + "\n\n" + extra).strip() if existing else extra
    return ctx


def after_message(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not remember_text:
        return ctx
    text = _incoming(ctx)
    if text:
        try:
            mem = remember_text(_agent(ctx), text, source=_source(ctx))
            if mem:
                ctx["life_memory_saved"] = {"id": mem.get("id"), "label": mem.get("label"), "text": mem.get("text")}
        except Exception as e:
            ctx["life_memory_error"] = repr(e)
    return ctx


if hooks is not None:
    hooks.register("life_memory.before_prompt")(before_prompt)
    hooks.register("life_memory.after_message")(after_message)
    hooks.register("before_put_task")(before_prompt)
    hooks.register("after_put_task")(after_message)
    hooks.register("wechat_incoming_message")(after_message)

def _coerce_ctx(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Accept GA hook styles and normalize them to a dict context."""
    if args and isinstance(args[0], dict):
        ctx = dict(args[0])
    else:
        ctx = {}
        if len(args) >= 1:
            ctx["agent"] = args[0]
        if len(args) >= 2:
            ctx["incoming_text"] = args[1]
    ctx.update(kwargs)
    return ctx


def install(app):
    """Install on lightweight frontends that expose register_hook(name, fn).

    The callback returns prompt text only for before_put_task.  Life-memory
    context is appended as side-channel prompt material and is never written to
    backend.history by this plugin.
    """
    if not hasattr(app, "register_hook"):
        return False

    def _before(uid=None, prompt=None, *args, **kwargs):
        ctx = {
            "agent": os.environ.get("GA_AGENT") or "xinghui",
            "source": "frontend.before_put_task",
            "incoming_text": prompt or getattr(app, "last_incoming_text", "") or "",
        }
        res = before_prompt(ctx)
        extra = res.get("side_context") or ""
        if extra and isinstance(prompt, str):
            return prompt.rstrip() + "\n\n" + extra
        return prompt

    def _after(msg=None, *args, **kwargs):
        text = ""
        if isinstance(msg, str):
            text = msg
        elif isinstance(msg, dict):
            text = msg.get("text") or msg.get("content") or msg.get("message") or ""
        text = text or getattr(app, "last_incoming_text", "") or ""
        ctx = {
            "agent": os.environ.get("GA_AGENT") or "xinghui",
            "source": "frontend.after_message",
            "incoming_text": text,
            "msg": msg,
        }
        return after_message(ctx)

    app.register_hook("before_put_task", _before)
    app.register_hook("wechat_incoming_message", _after)
    app.register_hook("after_message", _after)
    return True

