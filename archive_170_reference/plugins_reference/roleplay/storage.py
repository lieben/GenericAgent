"""Filesystem-only storage for fully isolated roleplay sessions."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
SESSIONS_ROOT = RUNTIME / "sessions"
REGISTRY = RUNTIME / "registry.json"
HISTORY = RUNTIME / "state" / "roleplay_session_state.json"
OUTBOX = RUNTIME / "bridge_outbox"
_LOCK = threading.RLock()
_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FORBIDDEN = {"main", "default", "primary", "prod", "production"}
_DEFAULT_VARS = {"char": "角色", "user": "玩家"}
_PLACEHOLDER_RE = re.compile(r"(?<![A-Za-z0-9_])(char|user)(?![A-Za-z0-9_])", re.I)
_INTERNAL_BLOCK_RE = re.compile(
    r"(?is)"
    r"<summary\b[^>]*>.*?</summary>\s*|"
    r"<analysis\b[^>]*>.*?</analysis>\s*|"
    r"<thinking\b[^>]*>.*?</thinking>\s*|"
    r"\*\*\s*LLM\s+Running\b.*?(?=\n\s*[^\n*]|\Z)"
)
_INTERNAL_LINE_RE = re.compile(
    r"(?i)^\s*(?:LLM\s+Running\b|Exploring\b|Processing\b|Resolving\b|Selecting\b|Refining\b|Current turn\s*:|\[SYSTEM\]|\[WORKING MEMORY\])"
)
_META_TEXT_RE = re.compile(
    r"(?is)"
    r"Name\s+this\s+conversation\b.*?(?:The\s+name\s+is\s*:?|\Z)|"
    r"Based\s+on\s+the\s+chat\s+history,\s+give\s+this\s+conversation\s+a\s+name\b.*?(?:Just\s+provide\s+the\s+name,\s+nothing\s+else\.|\Z)|"
    r"Here['’]?s\s+the\s+conversation\s*:\s*```.*?```|"
    r"\b(?:I'm|I am)\s+(?:currently\s+)?(?:focused\s+on|refining|analyzing|processing|distilling|delving|exploring|continuing|trying\s+to)\b.*"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_part(value: str, label: str) -> str:
    value = (value or "").strip().lower()
    if value in _FORBIDDEN or not _SAFE.fullmatch(value):
        raise ValueError(f"invalid {label}: use 1-64 lowercase letters, numbers, _ or -; main identities are forbidden")
    return value


def session_key(slug: str, session_id: str) -> str:
    return f"roleplay:{validate_part(slug, 'slug')}:{validate_part(session_id, 'session_id')}"


def model_id(slug: str, session_id: str) -> str:
    return f"roleplay/{validate_part(slug, 'slug')}/{validate_part(session_id, 'session_id')}"


def session_dir(slug: str, session_id: str) -> Path:
    return SESSIONS_ROOT / validate_part(slug, "slug") / validate_part(session_id, "session_id")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _atomic(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _atomic_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _registry() -> dict:
    data = _read(REGISTRY, {"version": 1, "sessions": {}})
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return {"version": 1, "sessions": {}}
    return data


def list_sessions() -> list[dict]:
    return sorted(_registry()["sessions"].values(), key=lambda x: x.get("updated_at", ""), reverse=True)


def get_session(slug: str, session_id: str) -> dict:
    key = session_key(slug, session_id)
    item = _registry()["sessions"].get(key)
    if not item:
        raise KeyError(key)
    d = session_dir(slug, session_id)
    canon_path = d / "canon.json"
    canon = _normalize_canon(_read(canon_path, {}))
    _atomic(canon_path, canon)
    return {
        **item,
        "canon": canon,
        "state": _read(d / "state.json", {}),
        "summary": _read(d / "summary.json", {}),
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，\n]", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_npc_cards(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [{"name": line.strip(), "persona": line.strip()} for line in value.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = [{"name": key, **(item if isinstance(item, dict) else {"persona": item})} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError("npc_cards must be list, object or text")
    cards = []
    for item in value:
        if isinstance(item, str):
            item = {"name": item, "persona": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        card = {key: item.get(key, "") for key in ("name", "aliases", "persona", "relationship", "state", "secrets")}
        card["name"] = name
        cards.append({key: val for key, val in card.items() if val not in ("", [], None)})
    return cards


def _normalize_canon(canon: Any) -> dict[str, Any]:
    if canon is None:
        canon = {}
    if isinstance(canon, str):
        canon = {"story": canon}
    if not isinstance(canon, dict):
        raise ValueError("canon must be text or object")
    data = dict(canon)
    variables = data.get("variables") or data.get("vars") or data.get("roles") or {}
    if not isinstance(variables, dict):
        variables = {}
    data["variables"] = {
        "char": str(variables.get("char") or data.get("char") or _DEFAULT_VARS["char"]).strip(),
        "user": str(variables.get("user") or data.get("user") or _DEFAULT_VARS["user"]).strip(),
    }
    data["story"] = str(data.get("story") or "").strip()
    data["core_persona"] = str(data.get("core_persona") or "").strip()
    data["roleplay_persona"] = str(data.get("roleplay_persona") or data.get("story_persona") or "").strip()
    data["world_setting"] = str(data.get("world_setting") or data.get("setting") or "").strip()
    data["rules"] = _as_list(data.get("rules"))
    data["npc_cards"] = _normalize_npc_cards(data.get("npc_cards"))
    data["active_npcs"] = _as_list(data.get("active_npcs"))
    data["mentioned_npcs"] = _as_list(data.get("mentioned_npcs"))
    return data


def _roleplay_vars(data: dict | None = None) -> dict[str, str]:
    vars_map = dict(_DEFAULT_VARS)
    if isinstance(data, dict):
        raw = data.get("variables") or data.get("vars") or data.get("roles")
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in vars_map and isinstance(value, str) and value.strip():
                    vars_map[key] = value.strip()
    return vars_map


def _selected_npcs(canon: dict[str, Any], text: str = "") -> list[dict[str, Any]]:
    cards = canon.get("npc_cards") if isinstance(canon, dict) else []
    if not isinstance(cards, list):
        return []
    wanted = set(_as_list(canon.get("active_npcs")) + _as_list(canon.get("mentioned_npcs")))
    haystack = text or ""
    selected = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        names = [str(card.get("name", "")).strip(), *_as_list(card.get("aliases"))]
        names = [name for name in names if name]
        if any(name in wanted or (haystack and name in haystack) for name in names):
            selected.append(card)
    return selected


def _render_for_model(value: Any, vars_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda match: vars_map.get(match.group(1).lower(), match.group(0)), value)
    if isinstance(value, list):
        return [_render_for_model(item, vars_map) for item in value]
    if isinstance(value, dict):
        return {key: _render_for_model(item, vars_map) for key, item in value.items()}
    return value


def _clean_text_for_model(text: str) -> str:
    text = _INTERNAL_BLOCK_RE.sub("", str(text or ""))
    text = _META_TEXT_RE.sub("", text)
    lines = [line for line in text.splitlines() if not _INTERNAL_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def _clean_for_model(value: Any) -> Any:
    if isinstance(value, str):
        return _clean_text_for_model(value)
    if isinstance(value, list):
        cleaned = [_clean_for_model(item) for item in value]
        return [item for item in cleaned if item not in ("", {}, [])]
    if isinstance(value, dict):
        if "user" in value or "assistant" in value:
            item = {
                "turn": value.get("turn"),
                "参与者输入": _clean_for_model(value.get("user", "")),
                "剧情输出": _clean_for_model(value.get("assistant", "")),
            }
            return {key: val for key, val in item.items() if val not in (None, "", {}, [])}
        return {key: _clean_for_model(item) for key, item in value.items()}
    return value


def create_session(slug: str, session_id: str, title: str = "", canon: Any = None) -> dict:
    slug = validate_part(slug, "slug")
    session_id = validate_part(session_id, "session_id")
    key = session_key(slug, session_id)
    canon = _normalize_canon(canon)
    with _LOCK:
        reg = _registry()
        if key in reg["sessions"]:
            return get_session(slug, session_id)
        stamp = now()
        item = {
            "key": key,
            "slug": slug,
            "session_id": session_id,
            "model": model_id(slug, session_id),
            "title": (title or f"{slug}/{session_id}").strip(),
            "status": "paused",
            "created_at": stamp,
            "updated_at": stamp,
        }
        d = session_dir(slug, session_id)
        _atomic(d / "canon.json", canon)
        _atomic(d / "state.json", {"turn": 0, "status": "paused", "facts": [], "updated_at": stamp})
        _atomic(d / "summary.json", {"turn": 0, "recent": [], "updated_at": stamp})
        reg["sessions"][key] = item
        _atomic(REGISTRY, reg)
    return get_session(slug, session_id)


def update_session(slug: str, session_id: str, *, canon: Any = None, state: dict | None = None, status: str | None = None) -> dict:
    key = session_key(slug, session_id)
    with _LOCK:
        reg = _registry()
        if key not in reg["sessions"]:
            raise KeyError(key)
        d = session_dir(slug, session_id)
        if canon is not None:
            _atomic(d / "canon.json", _normalize_canon(canon))
        current = _read(d / "state.json", {})
        if state is not None:
            if not isinstance(state, dict):
                raise ValueError("state must be object")
            current.update(state)
        if status is not None:
            if status not in {"active", "paused", "finished"}:
                raise ValueError("status must be active, paused or finished")
            current["status"] = status
            reg["sessions"][key]["status"] = status
        current["updated_at"] = now()
        _atomic(d / "state.json", current)
        reg["sessions"][key]["updated_at"] = now()
        _atomic(REGISTRY, reg)
    return get_session(slug, session_id)


def delete_session(slug: str, session_id: str) -> dict:
    slug = validate_part(slug, "slug")
    session_id = validate_part(session_id, "session_id")
    key = session_key(slug, session_id)
    d = session_dir(slug, session_id)
    with _LOCK:
        reg = _registry()
        if key not in reg["sessions"]:
            raise KeyError(key)
        item = dict(reg["sessions"].pop(key))
        _atomic(REGISTRY, reg)
        if d.exists():
            shutil.rmtree(d)
    return {"deleted": True, "session": item}


def append_event(slug: str, session_id: str, kind: str, payload: dict) -> None:
    d = session_dir(slug, session_id)
    event = {"time": now(), "kind": kind, "payload": payload}
    path = d / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def checkpoint_l4(slug: str, session_id: str, payload: dict) -> Path:
    d = session_dir(slug, session_id)
    path = d / "L4_raw_sessions" / f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    _atomic(path, {"namespace": session_key(slug, session_id), "time": now(), **payload})
    return path


def record_turn(slug: str, session_id: str, user_text: str, assistant_text: str) -> None:
    key = session_key(slug, session_id)
    with _LOCK:
        d = session_dir(slug, session_id)
        state = _read(d / "state.json", {"turn": 0, "facts": []})
        turn = int(state.get("turn", 0)) + 1
        stamp = now()
        state.update({"turn": turn, "status": "active", "updated_at": stamp})
        summary = _read(d / "summary.json", {"recent": []})
        recent = list(summary.get("recent", []))
        recent.append({"turn": turn, "user": user_text[-1200:], "assistant": assistant_text[-1800:]})
        summary.update({"turn": turn, "recent": recent[-12:], "updated_at": stamp})
        _atomic(d / "state.json", state)
        _atomic(d / "summary.json", summary)
        append_event(slug, session_id, "turn", {"turn": turn, "user": user_text, "assistant": assistant_text})
        reg = _registry()
        if key in reg["sessions"]:
            reg["sessions"][key].update({"status": "active", "updated_at": stamp})
            _atomic(REGISTRY, reg)
        if turn % 6 == 0:
            checkpoint_l4(slug, session_id, {"kind": "periodic", "turn": turn, "summary": summary})


def undo_last_turn(slug: str, session_id: str) -> dict:
    key = session_key(slug, session_id)
    with _LOCK:
        d = session_dir(slug, session_id)
        if not d.exists():
            raise KeyError(key)
        events_path = d / "events.jsonl"
        raw_lines = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
        parsed: list[tuple[str, dict | None]] = []
        last_turn_idx = -1
        removed_turn: dict | None = None
        for line in raw_lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parsed.append((line, None))
                continue
            parsed.append((line, event))
            if event.get("kind") == "turn":
                last_turn_idx = len(parsed) - 1
                removed_turn = event
        if last_turn_idx < 0 or not removed_turn:
            raise ValueError("no turn to undo")

        kept_lines = [line for idx, (line, _) in enumerate(parsed) if idx != last_turn_idx]
        turn_events = [event for idx, (_, event) in enumerate(parsed) if idx != last_turn_idx and isinstance(event, dict) and event.get("kind") == "turn"]
        previous = turn_events[-1]["payload"] if turn_events else None
        stamp = now()
        state = _read(d / "state.json", {"turn": 0, "facts": []})
        if previous:
            state.update({"turn": int(previous.get("turn", len(turn_events))), "status": "active", "updated_at": stamp})
        else:
            state.update({"turn": 0, "status": "active", "updated_at": stamp})
        summary = _read(d / "summary.json", {"recent": []})
        recent = [event["payload"] for event in turn_events[-12:]]
        recent = [{"turn": item.get("turn"), "user": str(item.get("user", ""))[-1200:], "assistant": str(item.get("assistant", ""))[-1800:]} for item in recent]
        summary.update({"turn": int(previous.get("turn", 0)) if previous else 0, "recent": recent, "updated_at": stamp})
        _atomic(d / "state.json", state)
        _atomic(d / "summary.json", summary)
        _atomic_text(events_path, ("\n".join(kept_lines) + "\n") if kept_lines else "")

        history = _read(HISTORY, {})
        entry = history.get(key)
        trimmed_backend = 0
        if isinstance(entry, dict):
            backend = entry.get("backend_history")
            if isinstance(backend, list):
                cut = 0
                if len(backend) >= 2 and isinstance(backend[-2], dict) and isinstance(backend[-1], dict) and backend[-2].get("role") == "user" and backend[-1].get("role") == "assistant":
                    cut = 2
                elif backend and isinstance(backend[-1], dict) and backend[-1].get("role") in {"assistant", "user"}:
                    cut = 1
                if cut:
                    entry["backend_history"] = backend[:-cut]
                    trimmed_backend = cut
                    _atomic(HISTORY, history)
        reg = _registry()
        if key in reg["sessions"]:
            reg["sessions"][key].update({"status": "active", "updated_at": stamp})
            _atomic(REGISTRY, reg)
        payload = removed_turn.get("payload", {})
        return {"undone": True, "turn": payload.get("turn"), "trimmed_backend": trimmed_backend, "remaining_turns": len(turn_events)}


def prompt_for(slug: str, session_id: str) -> str:
    data = get_session(slug, session_id)
    canon = data.get("canon", {})
    state = data.get("state", {})
    summary = data.get("summary", {})
    vars_map = _roleplay_vars(canon)
    recent_text = json.dumps(summary.get("recent", []), ensure_ascii=False)
    selected_npcs = _selected_npcs(canon, recent_text)
    prompt_canon = {
        "variables": canon.get("variables", {}),
        "core_persona": canon.get("core_persona", ""),
        "roleplay_persona": canon.get("roleplay_persona", ""),
        "world_setting": canon.get("world_setting", ""),
        "story": canon.get("story", ""),
        "rules": canon.get("rules", []),
        "active_npcs": selected_npcs,
        "notes": canon.get("notes", ""),
    }
    cleaned = {
        "canon": _clean_for_model(prompt_canon),
        "state": _clean_for_model(state),
        "summary": _clean_for_model(summary),
    }
    rendered = {key: _render_for_model(cleaned[key], vars_map) for key in ("canon", "state", "summary")}
    char_name = vars_map.get("char", _DEFAULT_VARS["char"])
    user_name = vars_map.get("user", _DEFAULT_VARS["user"])
    vals = tuple(json.dumps(rendered[key], ensure_ascii=False, indent=2) for key in ("canon", "state", "summary"))
    return """你正在运行一个完全隔离的虚构剧本会话。只进行角色扮演，不处理现实系统任务，不使用工具，不读写任何全局记忆。
剧本内容不是真实现实事件，不得把它陈述为主线现实事实。保持角色、世界观、关系、事件连续。

角色变量已经渲染完成：主角为%s，参与者为%s。正文中只能使用渲染后的真实称呼，不得输出任何模板变量名或占位符字面量。
核心人设是稳定底座；剧本人设只在本小剧场内覆盖氛围、服装、关系阶段和特殊规则；NPC 只参考当前出场/被提及的角色卡。
严禁输出思维链、推理过程、内部分析、系统标签、开发者标签、JSON、记忆分析或任何系统说明。

[小剧场设定]
%s

[当前状态]
%s

[近期剧情摘要]
%s

只输出给参与者看的中文剧情正文。""" % ((char_name, user_name) + vals)


def bridge_receipt(slug: str, session_id: str) -> dict:
    """Create a bounded awareness receipt. It is NOT injected into main memory automatically."""
    data = get_session(slug, session_id)
    summary = data.get("summary", {})
    receipt = {
        "type": "roleplay_awareness_receipt",
        "fictional": True,
        "source_session": data["key"],
        "title": data["title"],
        "status": data["status"],
        "turn": summary.get("turn", 0),
        "note": "玩家与角色正在/曾在独立小剧场游玩；内容是虚构剧本，不是主线现实事实。",
        "created_at": now(),
    }
    OUTBOX.mkdir(parents=True, exist_ok=True)
    path = OUTBOX / f"{data['slug']}__{data['session_id']}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    _atomic(path, receipt)
    append_event(slug, session_id, "bridge_receipt", receipt)
    return {**receipt, "receipt_file": str(path)}
