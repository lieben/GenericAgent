"""Roleplay bridge plugin.

Exposes the isolated roleplay reader as a dynamic tool without modifying
GA core files (ga.py / assets/tools_schema.json).
"""
import json
import types

from agent_loop import StepOutcome
from plugins.hooks import register

_TOOL_NAME = "roleplay_story"

_TOOL_SCHEMA = {"type": "function", "function": {
    "name": _TOOL_NAME,
    "description": "Read isolated roleplay stories as read-only context for review, critique, or discussion. Does not write or advance the story.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Optional fuzzy search text: title, slug, session id, model, etc."},
        "slug": {"type": "string", "description": "Roleplay model slug, e.g. 00hermes/gemini-3.1-pro"},
        "session_id": {"type": "string", "description": "Roleplay session id, e.g. 00"},
        "max_turns": {"type": "integer", "description": "Return only the latest N turns; <=0 returns all turns"},
        "include_canon": {"type": "boolean", "description": "Whether to include story settings/persona/NPC cards"},
        "include_summary": {"type": "boolean", "description": "Whether to include persisted summary state"}
    }}
}}


def _arg(args, name, default, typ=None):
    v = args.get(name, default)
    if typ is int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    if typ is bool:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(v)
    return v


def _has_tool(tools_schema):
    for item in tools_schema or []:
        try:
            if item.get("function", {}).get("name") == _TOOL_NAME:
                return True
        except AttributeError:
            continue
    return False


def _do_roleplay_story(self, args, response):
    """Read-only bridge for mainline agents to inspect isolated roleplay stories."""
    query = str(args.get("query") or "").strip().lower()
    slug = str(args.get("slug") or "").strip()
    session_id = str(args.get("session_id") or "").strip()
    include_canon = _arg(args, "include_canon", True, bool)
    include_summary = _arg(args, "include_summary", True, bool)
    max_turns = _arg(args, "max_turns", 80, int)
    try:
        from plugins.roleplay import storage
        sessions = storage.list_sessions()
        match = None
        if slug and session_id:
            key = storage.session_key(slug, session_id)
            match = next((item for item in sessions if item.get("key") == key), None)
        if match is None and query:
            def haystack(item):
                return "\n".join(str(item.get(k, "")) for k in ("key", "slug", "session_id", "model", "title")).lower()
            candidates = [item for item in sessions if query in haystack(item)]
            if len(candidates) == 1:
                match = candidates[0]
            elif len(candidates) > 1:
                return StepOutcome({"status": "ambiguous", "matches": candidates}, next_prompt="\n")
        if match is None:
            if len(sessions) == 1 and not (slug or session_id or query):
                match = sessions[0]
            else:
                return StepOutcome({"status": "not_found", "available": sessions}, next_prompt="\n")

        data = storage.get_session(match["slug"], match["session_id"])
        events_path = storage.session_dir(match["slug"], match["session_id"]) / "events.jsonl"
        turns = []
        if events_path.exists():
            with events_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("kind") == "turn" and isinstance(event.get("payload"), dict):
                        payload = event["payload"]
                        turns.append({
                            "time": event.get("time"),
                            "turn": payload.get("turn"),
                            "user": payload.get("user", ""),
                            "assistant": payload.get("assistant", ""),
                        })
        if max_turns > 0:
            turns = turns[-max_turns:]
        result = {
            "status": "success",
            "session": match,
            "state": data.get("state", {}),
            "turns": turns,
            "turn_count": len(turns),
            "note": "只读小剧场内容；用于主线评价、回顾或讨论，不会写入/推进剧本。",
        }
        if include_canon:
            result["canon"] = data.get("canon", {})
        if include_summary:
            result["summary"] = data.get("summary", {})
        next_prompt = "\n"
        if hasattr(self, "_get_anchor_prompt"):
            next_prompt = self._get_anchor_prompt(skip=args.get("_index", 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    except Exception as e:
        return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")


@register("llm_before")
def register_roleplay_story_tool(ctx):
    tools_schema = ctx.get("tools_schema")
    if isinstance(tools_schema, list) and tools_schema and not _has_tool(tools_schema):
        tools_schema.append(_TOOL_SCHEMA)

    handler = ctx.get("handler")
    if handler is not None and not hasattr(handler, "do_roleplay_story"):
        handler.do_roleplay_story = types.MethodType(_do_roleplay_story, handler)
