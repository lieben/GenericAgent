"""Filesystem-only storage for fully isolated roleplay sessions with MemPalace bridging."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "runtime"
SESSIONS_ROOT = RUNTIME_ROOT / "sessions"
REGISTRY_PATH = RUNTIME_ROOT / "registry.json"
HISTORY = RUNTIME_ROOT / "history.json"

NPCS_PATH = RUNTIME_ROOT / "npcs.json"

_LOCK = threading.RLock()
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_DEFAULT_VARS = {"char": "Character", "user": "User"}

def list_npcs() -> list[dict]:
    with _LOCK:
        data = _read(NPCS_PATH, {"npcs": []})
        npcs = data.get("npcs", [])
        return sorted(npcs, key=lambda x: x.get("updated_at", ""), reverse=True)

def save_npc(npc_id: str, name: str, role: str, persona: str, style: str = "") -> dict:
    npc_id = validate_part(npc_id, "npc_id")
    with _LOCK:
        data = _read(NPCS_PATH, {"npcs": []})
        npcs = data.get("npcs", [])
        now_ts = now()
        existing = next((n for n in npcs if n.get("id") == npc_id), None)
        if existing:
            existing.update({
                "name": name,
                "role": role,
                "persona": persona,
                "style": style,
                "updated_at": now_ts,
            })
            entry = existing
        else:
            entry = {
                "id": npc_id,
                "name": name,
                "role": role,
                "persona": persona,
                "style": style,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
            npcs.append(entry)
        data["npcs"] = npcs
        _atomic(NPCS_PATH, data)
        return entry

def delete_npc(npc_id: str) -> bool:
    npc_id = validate_part(npc_id, "npc_id")
    with _LOCK:
        data = _read(NPCS_PATH, {"npcs": []})
        npcs = data.get("npcs", [])
        initial_len = len(npcs)
        npcs = [n for n in npcs if n.get("id") != npc_id]
        if len(npcs) < initial_len:
            data["npcs"] = npcs
            _atomic(NPCS_PATH, data)
            return True
        return False

OUTBOX = RUNTIME_ROOT / "outbox"

def _get_mempalace_client():
    try:
        app_root = ROOT.parent.parent
        for path_str in [str(app_root), str(app_root / "src")]:
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
        import agentmain
        return agentmain.client
    except Exception:
        return None

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

def _read(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def validate_part(value: str, name: str = "parameter") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    value = value.strip()
    if not _NAME_RE.match(value):
        raise ValueError(f"Invalid {name}: {value!r}. Only lowercase letters, numbers, _ or - allowed (max 64 chars).")
    if value in ("main", "system", "global"):
        raise ValueError(f"{name} cannot use reserved words: {value!r}")
    return value

def session_key(slug: str, session_id: str) -> str:
    return f"roleplay:{validate_part(slug, 'slug')}:{validate_part(session_id, 'session_id')}"

def model_id(slug: str, session_id: str) -> str:
    return f"rp-{validate_part(slug, 'slug')}-{validate_part(session_id, 'session_id')}"

def session_dir(slug: str, session_id: str) -> Path:
    return SESSIONS_ROOT / validate_part(slug, "slug") / validate_part(session_id, "session_id")

def _registry() -> dict:
    return _read(REGISTRY_PATH, {"sessions": {}, "version": 1})

def list_sessions() -> list[dict]:
    with _LOCK:
        reg = _registry()
        res = []
        for k, v in reg.get("sessions", {}).items():
            res.append(dict(v))
        return sorted(res, key=lambda x: x.get("updated_at", ""), reverse=True)

def get_session(slug: str, session_id: str) -> dict:
    sdir = session_dir(slug, session_id)
    with _LOCK:
        if not sdir.exists():
            raise FileNotFoundError(f"Session not found: {slug}/{session_id}")
        canon = _read(sdir / "canon.json", {})
        if "context_config" not in canon or not isinstance(canon["context_config"], dict):
            canon["context_config"] = {
                "mode": "dialogue",
                "dialogue_turns": 8,
                "summary_turns": 5,
                "macro_interval": 10
            }
        state = _read(sdir / "state.json", {})
        summary = _read(sdir / "summary.json", {})
        reg = _registry()
        k = session_key(slug, session_id)
        reg_item = reg.get("sessions", {}).get(k, {})
        return {
            "key": k,
            "model_id": model_id(slug, session_id),
            "slug": slug,
            "session_id": session_id,
            "title": canon.get("title", reg_item.get("title", f"{slug}/{session_id}")),
            "status": state.get("status", reg_item.get("status", "active")),
            "canon": canon,
            "state": state,
            "summary": summary,
            "created_at": reg_item.get("created_at"),
            "updated_at": reg_item.get("updated_at"),
        }

def create_session(slug: str, session_id: str, title: str, canon: dict) -> dict:
    slug = validate_part(slug, "slug")
    session_id = validate_part(session_id, "session_id")
    sdir = session_dir(slug, session_id)
    with _LOCK:
        if sdir.exists():
            raise FileExistsError(f"Session already exists: {slug}/{session_id}")
        now_ts = now()
        canon_data = dict(canon or {})
        canon_data.setdefault("variables", _DEFAULT_VARS.copy())
        canon_data.setdefault("title", title or f"{slug}/{session_id}")
        canon_data.setdefault("context_config", {
            "mode": "dialogue",
            "dialogue_turns": 8,
            "summary_turns": 5,
            "macro_interval": 10
        })
        state_data = {"status": "active", "updated_at": now_ts}
        summary_data = {
            "turn": 0,
            "summary": "",
            "macro_milestone": "",
            "recent_steps": [],
            "recent": [],
            "updated_at": now_ts
        }
        _atomic(sdir / "canon.json", canon_data)
        _atomic(sdir / "state.json", state_data)
        _atomic(sdir / "summary.json", summary_data)

        reg = _registry()
        k = session_key(slug, session_id)
        reg.setdefault("sessions", {})[k] = {
            "key": k,
            "model_id": model_id(slug, session_id),
            "slug": slug,
            "session_id": session_id,
            "title": canon_data["title"],
            "status": "active",
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        _atomic(REGISTRY_PATH, reg)
        return get_session(slug, session_id)

def update_session(slug: str, session_id: str, canon: dict | None = None, status: str | None = None) -> dict:
    sdir = session_dir(slug, session_id)
    with _LOCK:
        if not sdir.exists():
            raise FileNotFoundError(f"Session not found: {slug}/{session_id}")
        now_ts = now()
        if canon is not None:
            curr = _read(sdir / "canon.json", {})
            for k, v in canon.items():
                if k == "context_config" and isinstance(v, dict) and isinstance(curr.get("context_config"), dict):
                    curr["context_config"].update(v)
                else:
                    curr[k] = v
            _atomic(sdir / "canon.json", curr)
        if status is not None:
            curr_s = _read(sdir / "state.json", {})
            curr_s["status"] = status
            curr_s["updated_at"] = now_ts
            _atomic(sdir / "state.json", curr_s)
        reg = _registry()
        k = session_key(slug, session_id)
        if k in reg["sessions"]:
            if status is not None:
                reg["sessions"][k]["status"] = status
            if canon and "title" in canon:
                reg["sessions"][k]["title"] = canon["title"]
            reg["sessions"][k]["updated_at"] = now_ts
            _atomic(REGISTRY_PATH, reg)
        return get_session(slug, session_id)

def delete_session(slug: str, session_id: str) -> None:
    sdir = session_dir(slug, session_id)
    with _LOCK:
        if sdir.exists():
            shutil.rmtree(sdir)
        reg = _registry()
        k = session_key(slug, session_id)
        if k in reg.get("sessions", {}):
            del reg["sessions"][k]
            _atomic(REGISTRY_PATH, reg)

def append_turn(slug: str, session_id: str, user_text: str, model_text: str) -> dict:
    slug = validate_part(slug, "slug")
    session_id = validate_part(session_id, "session_id")
    sdir = session_dir(slug, session_id)
    with _LOCK:
        now_ts = now()
        summary_data = _read(sdir / "summary.json", {"turn": 0, "summary": "", "recent": []})
        new_turn = summary_data.get("turn", 0) + 1
        recent = summary_data.get("recent", [])
        recent.append({"user": user_text, "char": model_text})

        canon = _read(sdir / "canon.json", {})
        vars_map = canon.get("variables") or _DEFAULT_VARS
        c_name = vars_map.get("char", "Character")
        u_name = vars_map.get("user", "User")

        ctx_cfg = canon.get("context_config") or {}
        max_dialogue = int(ctx_cfg.get("dialogue_turns", 8))
        max_summary = int(ctx_cfg.get("summary_turns", 5))
        macro_interval = int(ctx_cfg.get("macro_interval", 10))

        keep_recent = max(max_dialogue, 15)
        if len(recent) > keep_recent:
            recent = recent[-keep_recent:]
        summary_data["turn"] = new_turn
        summary_data["recent"] = recent
        summary_data["updated_at"] = now_ts

        # 1. 微观单步推进提炼 (Single Step Note)
        recent_steps = summary_data.get("recent_steps") or []
        macro_milestone = summary_data.get("macro_milestone", "")
        if max_summary > 0:
            u_brief = user_text.strip().replace("\r\n", " ").replace("\n", " ")
            c_brief = model_text.strip().replace("\r\n", " ").replace("\n", " ")
            if len(u_brief) > 40:
                u_brief = u_brief[:40] + "…"
            if len(c_brief) > 60:
                c_brief = c_brief[:60] + "…"
            step_note = f"[第{new_turn}轮] {u_name}: {u_brief} | {c_name}: {c_brief}"

            recent_steps.append(step_note)

            # 2. 宏观全剧大纲聚合 (Macro Milestone Rolling Aggregation)
            if len(recent_steps) > max_summary:
                overflow_count = len(recent_steps) - max_summary
                overflow_steps = recent_steps[:overflow_count]
                recent_steps = recent_steps[overflow_count:]

                overflow_text = " ".join(overflow_steps)
                if macro_milestone:
                    macro_milestone = f"{macro_milestone}\n阶段进展: {overflow_text}"
                else:
                    macro_milestone = f"故事前置历程: {overflow_text}"

                m_lines = [l.strip() for l in macro_milestone.split("\n") if l.strip()]
                if len(m_lines) > 12:
                    macro_milestone = "\n".join(m_lines[-12:])
        else:
            recent_steps = []
            macro_milestone = ""

        summary_data["recent_steps"] = recent_steps
        summary_data["macro_milestone"] = macro_milestone
        summary_data["summary"] = "\n".join(recent_steps)

        _atomic(sdir / "summary.json", summary_data)

        event_entry = {
            "turn": new_turn,
            "timestamp": now_ts,
            "user": user_text,
            "char": model_text,
        }
        with open(sdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event_entry, ensure_ascii=False) + "\n")

        reg = _registry()
        k = session_key(slug, session_id)
        if k in reg.get("sessions", {}):
            reg["sessions"][k]["updated_at"] = now_ts
            _atomic(REGISTRY_PATH, reg)

        # 自动将本轮关键互动与精彩节点异步/安全沉淀至 MemPalace wing_roleplay
        try:
            client = _get_mempalace_client()
            if client:
                data = get_session(slug, session_id)
                title = data.get("title") or f"{slug}/{session_id}"
                drawer_content = (
                    f"【IF线剧本·节点记录】\n"
                    f"- 剧本: 《{title}》 (slug: {slug}, session: {session_id})\n"
                    f"- 轮次: 第{new_turn}回合\n"
                    f"- 互动原文:\n{u_name}: {user_text.strip()}\n{c_name}: {model_text.strip()}\n"
                )
                client.call_tool("mempalace_add_drawer", {
                    "wing": "wing_roleplay",
                    "room": f"story_{slug}",
                    "content": drawer_content,
                    "added_by": "roleplay_engine",
                    "source_file": f"roleplay/{slug}/{session_id}"
                })
        except Exception:
            pass

    return summary_data

record_turn = append_turn

def archive_to_mempalace(slug: str, session_id: str) -> dict:
    data = get_session(slug, session_id)
    canon = data.get("canon", {})
    summary = data.get("summary", {})
    vars_map = canon.get("variables") or _DEFAULT_VARS.copy()
    char_name = vars_map.get("char", "Character")
    user_name = vars_map.get("user", "User")

    selected_npc_ids = canon.get("selected_npc_ids") or []
    npc_details = []
    if selected_npc_ids:
        all_npcs = {n.get("id"): n for n in list_npcs()}
        for nid in selected_npc_ids:
            npc = all_npcs.get(nid)
            if npc:
                npc_details.append(f"{npc.get('name', nid)}({npc.get('role', '')}) - {npc.get('persona', '')}")

    client = _get_mempalace_client()
    if not client:
        raise RuntimeError("MemPalace client unavailable; cannot archive session.")

    title = data.get("title") or f"{slug}/{session_id}"
    target_wing = "wing_roleplay"
    target_room = f"story_{slug}"

    drawer_content = (
        f"【IF线剧本·虚拟小剧场存档】\n"
        f"- 剧本名称: 《{title}》\n"
        f"- 唯一标识: {slug}/{session_id}\n"
        f"- 核心主角: {char_name}\n"
        f"- 参与者: {user_name}\n"
        f"- 登场配角(NPC): {'; '.join(npc_details) if npc_details else '无'}\n"
        f"- 剧本世界观: {canon.get('world_setting', '')}\n"
        f"- 核心人设契约: {canon.get('core_persona', '')}\n"
        f"- 完结总轮数: {summary.get('turn', 0)}\n"
        f"- 剧情总梗概:\n{summary.get('summary', '')}\n"
        f"- 状态: {data.get('status', 'active')}\n"
    )

    results = {}
    try:
        drawer_res = client.call_tool("mempalace_add_drawer", {
            "wing": target_wing,
            "room": target_room,
            "content": drawer_content,
            "added_by": "roleplay_archiver",
            "source_file": f"roleplay/{slug}/{session_id}"
        })
        results["drawer"] = drawer_res
    except Exception as e:
        results["drawer_error"] = str(e)

    try:
        kg_res = client.call_tool("mempalace_kg_add", {
            "subject": f"{user_name}与{char_name}",
            "predicate": "体验虚构剧本",
            "object": title,
            "valid_from": data.get("created_at") or now(),
            "source_drawer_id": f"roleplay_{slug}_{session_id}",
            "source_file": f"roleplay/{slug}/{session_id}"
        })
        results["kg"] = kg_res
    except Exception as e:
        results["kg_error"] = str(e)

    receipt = bridge_receipt(slug, session_id)
    results["receipt"] = receipt
    return {"status": "success", "results": results, "wing": target_wing, "room": target_room}

def prompt_for(slug: str, session_id: str, current_query: str = "") -> str:
    data = get_session(slug, session_id)
    canon = data.get("canon", {})
    summary = data.get("summary", {})
    vars_map = canon.get("variables") or _DEFAULT_VARS.copy()
    char_name = vars_map.get("char", "Character")
    user_name = vars_map.get("user", "User")

    selected_npc_ids = canon.get("selected_npc_ids") or []
    npc_context = []
    if selected_npc_ids:
        all_npcs = {n.get("id"): n for n in list_npcs()}
        for nid in selected_npc_ids:
            npc = all_npcs.get(nid)
            if npc:
                npc_context.append({
                    "姓名": npc.get("name", nid),
                    "身份": npc.get("role", ""),
                    "性格特征": npc.get("persona", ""),
                    "语言风格": npc.get("style", ""),
                })

    contract_payload = {
        "剧本名称": data.get("title", f"{slug}/{session_id}"),
        "主要角色设定": {
            "主角": char_name,
            "主角核心人设": canon.get("core_persona", ""),
            "参与者": user_name,
        },
        "登场配角(NPC阵容)": npc_context,
        "世界与剧情前提": canon.get("world_setting", ""),
        "开场剧情": canon.get("story", ""),
        "扮演规则": canon.get("rules", []),
    }
    
    ctx_cfg = canon.get("context_config") or {}
    conf_dialogue_turns = int(ctx_cfg.get("dialogue_turns", 8))
    conf_summary_turns = int(ctx_cfg.get("summary_turns", 5))

    raw_recent = summary.get("recent", [])
    fresh_recent = raw_recent[-conf_dialogue_turns:] if conf_dialogue_turns > 0 else []

    macro_milestone = summary.get("macro_milestone") or "(剧本初启，尚未形成宏观阶段里程碑)"
    recent_steps = summary.get("recent_steps")
    if recent_steps is None:
        recent_steps_text = summary.get("summary") or "(暂无阶段梗概)"
    else:
        recent_steps_text = "\n".join(recent_steps) if recent_steps else "(暂无阶段梗概)"

    clean_summary_payload = {
        "当前总回合数": summary.get("turn", 0),
    }
    if conf_summary_turns > 0:
        clean_summary_payload["全剧宏观总括(开局至今核心里程碑)"] = macro_milestone
        clean_summary_payload["近期阶段梗概(承接微观脉络)"] = recent_steps_text

    if conf_dialogue_turns > 0:
        clean_summary_payload[f"最新近景交互(近{conf_dialogue_turns}轮完整细节)"] = fresh_recent
    else:
        clean_summary_payload["最新近景交互"] = "(不保留近景对白上下文)"

    recalled_memory_block = ""
    query_str = (current_query or "").strip()
    if query_str and len(query_str) >= 2:
        try:
            client = _get_mempalace_client()
            if client:
                search_res = client.call_tool("mempalace_search", {
                    "query": query_str,
                    "wing": "wing_roleplay",
                    "room": f"story_{slug}",
                    "limit": 2,
                    "max_distance": 1.25
                })
                if isinstance(search_res, str):
                    try:
                        res_obj = json.loads(search_res)
                    except Exception:
                        res_obj = {}
                else:
                    res_obj = search_res if isinstance(search_res, dict) else {}
                
                drawers = res_obj.get("results") or []
                if drawers:
                    mem_snippets = []
                    for d in drawers:
                        c = d.get("content", "").strip()
                        if c:
                            mem_snippets.append(c)
                    if mem_snippets:
                        recalled_memory_block = (
                            "\n【记忆宫殿相关剧情回捞 (往昔线索/信物/伏笔)】\n"
                            + "\n---\n".join(mem_snippets)
                            + "\n（以上记忆抽取自过往剧本剧情，仅作为剧情呼应与沉淀，请自然融入对话）\n"
                        )
        except Exception:
            pass

    return f"""你正在运行一个完全隔离的独立虚构剧本会话（IF线角色扮演）。
【绝对铁律】
1. 纯沉浸式剧情演绎：扮演主角【{char_name}】，与参与者【{user_name}】互动。只输出给参与者看的沉浸式剧情与中文对白。
2. 零工具与零现实干扰：本剧本不处理任何现实日常任务，不调用任何物理或系统工具，不读取或修改任何现实环境状态。
3. 纯净角色输出：禁止携带任何工具调用格式、禁止输出任何系统指令分析或思考前缀。

【剧本人设与世界观契约】
{json.dumps(contract_payload, ensure_ascii=False, indent=2)}

【当前剧情上下文记忆】
{json.dumps(clean_summary_payload, ensure_ascii=False, indent=2)}
{recalled_memory_block}
请自然承接剧情，推进故事发展。"""

def bridge_receipt(slug: str, session_id: str) -> dict:
    data = get_session(slug, session_id)
    summary = data.get("summary", {})
    vars_map = data.get("canon", {}).get("variables") or _DEFAULT_VARS
    c_name = vars_map.get("char", "Character")
    u_name = vars_map.get("user", "User")
    receipt = {
        "type": "roleplay_awareness_receipt",
        "fictional": True,
        "source_session": data["key"],
        "title": data["title"],
        "status": data["status"],
        "turn": summary.get("turn", 0),
        "note": f"{u_name}与{c_name}正在/曾在独立小剧场体验虚构剧本；内容属于IF线剧本创作，不是主线现实事实。",
        "created_at": now(),
    }
    OUTBOX.mkdir(parents=True, exist_ok=True)
    path = OUTBOX / f"{data['slug']}__{data['session_id']}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    _atomic(path, receipt)
    return {**receipt, "receipt_file": str(path)}
