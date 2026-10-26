"""Shared frontend chat runtime for local frontends (GA-compatible).

This module owns the frontend-layer turn lifecycle shared by clawbot and the
OpenAI-compatible API:

    restore persistent backend history -> assemble frontend prompt hooks ->
    enqueue native GA put_task -> persist backend history after final answer

It intentionally does not modify GA core.  Per-turn side context injected by
frontend hooks (for example live group peek / runtime context) is added only to
the current prompt and is not separately persisted.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from plugins.local_frontends.prompt_config import get_prompt_config
from plugins.local_frontends.session_history import (
    restore_backend_history,
    persist_agent_backend_history,
)

ROOT = Path(__file__).resolve().parents[2]
SESSION_STATE_PATH = Path(__file__).resolve().parent / "runtime" / "state" / "wechat_session_state.json"


def _default_session_key() -> str:
    """Resolve main session key from env or player-configurable identity config."""
    env_key = os.environ.get('WECHAT_PLUS_SESSION_KEY')
    if env_key:
        return env_key
    cfg_key = get_prompt_config('identity.main_session_key', '')
    if cfg_key:
        return str(cfg_key)
    return 'assistant:main'


def restore_frontend_session(agent: Any, session_key: str | None = None, *, force: bool = True) -> int:
    """Restore one logical chat session into native GA backend.history."""
    return restore_backend_history(agent, session_key or _default_session_key(), SESSION_STATE_PATH, force=force)


def persist_frontend_session(agent: Any, session_key: str | None = None) -> int:
    """Persist native GA backend.history for one logical chat session."""
    return persist_agent_backend_history(agent, session_key or _default_session_key(), SESSION_STATE_PATH)


def build_frontend_prompt(prompt: str, *, uid: str = "", hook_app: Any | None = None) -> str:
    """Run the same frontend prompt hook chain used by clawbot.

    API passes the same HookApp instance as clawbot when available, so xinghui:main
    sees one prompt assembly path regardless of display frontend.  If no hook app
    is available, the raw prompt is returned; this keeps non-xinghui sessions such
    as otto:main isolated and independent.
    """
    if hook_app is None or not hasattr(hook_app, "emit"):
        return prompt
    new_prompt = hook_app.emit("before_put_task", uid, prompt)
    return prompt if new_prompt is None else new_prompt


def enqueue_frontend_task(
    agent: Any,
    put_task: Callable[..., Any],
    prompt: str,
    *,
    session_key: str | None = None,
    uid: str = "",
    hook_app: Any | None = None,
    restore: bool = True,
    put_args: tuple = (),
    put_kwargs: dict | None = None,
) -> tuple[Any, str]:
    """Restore session, build frontend prompt, enqueue GA task, emit after hook."""
    if restore:
        restore_frontend_session(agent, session_key, force=True)
    final_prompt = build_frontend_prompt(prompt, uid=uid, hook_app=hook_app)
    kwargs = dict(put_kwargs or {})
    dq = put_task(final_prompt, *put_args, **kwargs)
    if hook_app is not None and hasattr(hook_app, "emit"):
        try:
            hook_app.emit("after_put_task", uid, final_prompt)
        except Exception:
            pass
        try:
            source = str(kwargs.get("source") or "")
            if source != "clawbot_private":
                hook_app.emit("after_message", uid, text=prompt, source="frontend.enqueue_frontend_task")
        except Exception:
            pass
    return dq, final_prompt
