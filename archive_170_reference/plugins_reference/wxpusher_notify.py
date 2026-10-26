"""WxPusher notification tool plugin.

Adds a single outbound notification tool to normal tool-enabled GA sessions.
It intentionally does not inject into tool-less HTTP API sessions.
Secrets are referenced from the shared keychain and never logged.
"""
from __future__ import annotations

import json
import re
import types
import urllib.error
import urllib.request

from agent_loop import StepOutcome
from plugins.hooks import register

_TOOL_NAME = "wxpusher_notify"
_ENDPOINT = "https://wxpusher.zjiecode.com/api/send/message"

# Model outputs can occasionally expose an internal planning preamble as the
# tool argument.  Never send those messages to a phone notification.
_INTERNAL_NOTIFICATION_MARKERS = (
    "initiating understanding",
    "context-aware message",
    "adhering to strict format",
    "internal reasoning",
    "chain of thought",
)


def _reject_internal_notification(text: str) -> str | None:
    """Return a rejection reason, or None when text is user-visible content."""
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if normalized == "skip":
        return "notification skipped"
    if any(marker in normalized for marker in _INTERNAL_NOTIFICATION_MARKERS):
        return "notification contains internal model text"
    # Treat a standalone SKIP line as a skip only when it is clearly a control
    # response, without suppressing ordinary prose that happens to mention it.
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if lines and lines[-1] == "skip" and len(lines) <= 3:
        return "notification skipped"
    return None


_TOOL_SCHEMA = {"type": "function", "function": {
    "name": _TOOL_NAME,
    "description": "Send a WxPusher phone notification to 言言. Use only when she explicitly asks for a push/reminder, or when a previously agreed task/reminder condition is reached. Outbound action: do not use speculatively or duplicate-send.",
    "parameters": {"type": "object", "properties": {
        "content": {"type": "string", "description": "Notification text shown directly in the phone notification summary"},
        "url": {"type": "string", "description": "Optional public URL opened when the notification is tapped"}
    }, "required": ["content"]}
}}


def _has_tool(tools_schema):
    return any(
        isinstance(item, dict)
        and item.get("function", {}).get("name") == _TOOL_NAME
        for item in (tools_schema or [])
    )


def send_notification(content: str, url: str = "", timeout: int = 15) -> dict:
    text = str(content or "").strip()
    if not text:
        return {"status": "error", "message": "content is required"}
    rejection = _reject_internal_notification(text)
    if rejection:
        return {"status": "skipped", "message": rejection}
    if len(text) > 1000:
        return {"status": "error", "message": "content exceeds 1000 characters"}

    # Import via the established shared-memory module; values are used only in
    # the request body and are never returned, printed, or persisted here.
    from keychain import keys
    token = keys.wxpusher_app_token.use()
    uid = keys.wxpusher_uid.use()
    payload = {
        "appToken": token,
        "content": text,
        "summary": text[:100],
        "contentType": 1,
        "uids": [uid],
        "verifyPay": False,
    }
    target = str(url or "").strip()
    if target:
        if not target.startswith(("https://", "http://")):
            return {"status": "error", "message": "url must start with http:// or https://"}
        payload["url"] = target

    request = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return {"status": "error", "http_status": exc.code, "message": "WxPusher HTTP error"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "error", "message": f"WxPusher request failed: {type(exc).__name__}"}

    if body.get("code") == 1000:
        data = body.get("data") or []
        message_id = data[0].get("messageId") if data and isinstance(data[0], dict) else None
        return {"status": "success", "message_id": message_id, "delivered_to": 1}
    return {"status": "error", "code": body.get("code"), "message": body.get("msg") or body.get("message") or "WxPusher rejected request"}


def _do_wxpusher_notify(self, args, response):
    result = send_notification(args.get("content", ""), args.get("url", ""))
    next_prompt = "\n"
    if hasattr(self, "_get_anchor_prompt"):
        next_prompt = self._get_anchor_prompt(skip=args.get("_index", 0) > 0)
    return StepOutcome(result, next_prompt=next_prompt)


@register("llm_before")
def register_wxpusher_tool(ctx):
    tools_schema = ctx.get("tools_schema")
    # HTTP API deliberately passes [] and must remain a one-shot chat-only path.
    if isinstance(tools_schema, list) and tools_schema and not _has_tool(tools_schema):
        tools_schema.append(_TOOL_SCHEMA)

    handler = ctx.get("handler")
    if handler is not None and not hasattr(handler, "do_wxpusher_notify"):
        handler.do_wxpusher_notify = types.MethodType(_do_wxpusher_notify, handler)
