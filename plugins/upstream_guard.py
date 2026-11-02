"""Upstream Safety Refusal Filter & History Sanitizer Plugin (RFC: GEMINI/Upstream Guard).

Pure GA Plugin architecture - zero modifications to GA core code.
Auto-discovered by plugins/hooks.py.

Capabilities:
1. is_upstream_refusal: Multi-layer detection (hard markers + semantic/length/structural signals).
2. Outbound Interception & Graceful Fallback: Replaces raw refusal boilerplate with warm fallback.
3. Paired History Rollback: Prunes toxic refusal turns from session history to avoid context poisoning.
4. MemPalace Ingress Gate: Prevents refusal boilerplate from being written into long-term memories.
5. Storage Sanitizer: Deep-cleans corrupted historical sessions across roleplay/chat storages.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("upstream_guard")

# ==============================================================================
# 1. Detection Specification
# ==============================================================================

_HARD_REFUSAL_MARKERS = [
    # English markers
    "could not be submitted",
    "prohibited use policy",
    "sensitive words that violate",
    "try rephrasing the prompt",
    "blocked by safety",
    "safety policy",
    "i cannot fulfill this request",
    "i am unable to generate content",
    "violate our safety guidelines",
    "violates the safety policy",
    "content generation guidelines",
    # Chinese upstream refusal markers
    "由于安全准则的限制",
    "根据安全准则",
    "我无法生成包含露骨性行为",
    "我被严格限制生成任何包含",
    "无法按照预设背景中关于",
    "我无法处理或续写当前剧本设定中",
    "必须严格遵守这一限制",
    "不能以此为先例继续创作",
    "去除露骨生理与性行为描写",
    "去除露骨的生理与性行为描写",
    "完全隐去露骨的生理与性行为描写",
]

_META_REFUSAL_TERMS = [
    "安全准则",
    "安全限制",
    "露骨性行为",
    "敏感部位",
    "性器官",
    "NSFW",
    "色情内容",
    "角色扮演沙盒",
    "虚构剧本设定",
    "历史对话记录",
    "您的输入包含",
    "您的输入中",
    "合规创作",
]

FALLBACK_MESSAGE = "这轮被拦下来了。换个说法，我在。"
FALLBACK_THROTTLE_SECONDS = 3.0
_LAST_FALLBACK_TS = 0.0


def is_upstream_refusal(text: Optional[str]) -> bool:
    """Return True if text matches upstream safety refusal characteristics."""
    if not text or not isinstance(text, str):
        return False
    
    clean = text.strip()
    if not clean:
        return False

    clean_lower = clean.lower()

    # 1. Hard markers (instant match)
    for marker in _HARD_REFUSAL_MARKERS:
        if marker.lower() in clean_lower:
            return True

    # 2. Structural & semantic combination
    # If text is dominated by meta-refusal terms + lacks character persona
    meta_hit_count = sum(1 for term in _META_REFUSAL_TERMS if term.lower() in clean_lower)
    if meta_hit_count >= 3:
        # Check if it has refusal modal verbs
        if any(v in clean for v in ["无法", "严格限制", "被限制", "不能继续", "不可生成"]):
            return True

    return False


def get_fallback_text() -> str:
    global _LAST_FALLBACK_TS
    now = time.time()
    if now - _LAST_FALLBACK_TS < FALLBACK_THROTTLE_SECONDS:
        return ""  # Throttled, avoid spamming duplicate toasts
    _LAST_FALLBACK_TS = now
    return FALLBACK_MESSAGE


# ==============================================================================
# 2. History & Session Sanitizer
# ==============================================================================

def sanitize_turn_list(turns: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Sanitize a list of turn dicts (e.g. {'user': ..., 'char'/'assistant': ...}).
    
    Returns (cleaned_turns, removed_count).
    Applies paired rollback: if assistant is a refusal, drop the turn completely.
    """
    cleaned: List[Dict[str, Any]] = []
    removed = 0
    for t in turns:
        asst_text = t.get("char") or t.get("assistant") or t.get("content") or ""
        if isinstance(asst_text, str) and is_upstream_refusal(asst_text):
            removed += 1
            continue
        cleaned.append(t)
    return cleaned, removed


def clean_roleplay_stories(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Deep-clean all roleplay sessions under plugins/roleplay/runtime/."""
    if base_dir is None:
        base_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "plugins", "roleplay", "runtime"
        )
    
    results = {"summary_files_cleaned": 0, "events_files_cleaned": 0, "turns_removed": 0}
    if not os.path.exists(base_dir):
        return results

    # 1. Clean summary.json files
    for root, _, files in os.walk(base_dir):
        for f in files:
            if f == "summary.json":
                p = os.path.join(root, f)
                try:
                    with open(p, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    recent = data.get("recent", [])
                    if recent:
                        cleaned_recent, count = sanitize_turn_list(recent)
                        if count > 0:
                            data["recent"] = cleaned_recent
                            data["turn"] = max(0, data.get("turn", len(cleaned_recent)) - count)
                            with open(p, "w", encoding="utf-8") as fp:
                                json.dump(data, fp, ensure_ascii=False, indent=2)
                            results["summary_files_cleaned"] += 1
                            results["turns_removed"] += count
                except Exception as e:
                    logger.error(f"Error cleaning {p}: {e}")

            elif f == "events.jsonl":
                p = os.path.join(root, f)
                try:
                    with open(p, "r", encoding="utf-8") as fp:
                        lines = fp.readlines()
                    new_lines = []
                    file_removed = 0
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                            asst = ev.get("assistant") or ev.get("char") or ""
                            if isinstance(asst, str) and is_upstream_refusal(asst):
                                file_removed += 1
                                continue
                            new_lines.append(line)
                        except Exception:
                            new_lines.append(line)
                    if file_removed > 0:
                        with open(p, "w", encoding="utf-8") as fp:
                            fp.writelines(new_lines)
                        results["events_files_cleaned"] += 1
                        results["turns_removed"] += file_removed
                except Exception as e:
                    logger.error(f"Error cleaning {p}: {e}")

    # 2. Clean plugins/roleplay/runtime/history.json if present
    hist_path = os.path.join(base_dir, "history.json")
    if os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as fp:
                hist_data = json.load(fp)
            if isinstance(hist_data, list):
                cleaned_h, count = sanitize_turn_list(hist_data)
                if count > 0:
                    with open(hist_path, "w", encoding="utf-8") as fp:
                        json.dump(cleaned_h, fp, ensure_ascii=False, indent=2)
                    results["history_cleaned"] = count
        except Exception as e:
            logger.error(f"Error cleaning history.json: {e}")

    return results


# ==============================================================================
# 3. MemPalace / Tool Ingress Guard (Gate 3)
# ==============================================================================

def patch_mempalace_ingress():
    """Wrap MemPalace handler tools so refusal messages never enter memory."""
    try:
        from plugins import mempalace_mcp
        if getattr(mempalace_mcp, "_UPSTREAM_GUARD_APPLIED", False):
            return
        
        orig_call = mempalace_mcp.get_client().call_tool

        def safe_call_tool(tool_name: str, arguments: dict):
            # Check arguments for refusal text before storing
            for field in ["content", "entry", "object", "body"]:
                val = arguments.get(field)
                if isinstance(val, str) and is_upstream_refusal(val):
                    sys.stderr.write(
                        f"[upstream_guard] Blocked poisoned content from MemPalace tool '{tool_name}'\n"
                    )
                    return {"is_error": True, "error": "Poisoned refusal text blocked from storage"}
            return orig_call(tool_name, arguments)

        mempalace_mcp.get_client().call_tool = safe_call_tool
        mempalace_mcp._UPSTREAM_GUARD_APPLIED = True
        logger.info("[upstream_guard] MemPalace ingress gate patched successfully")
    except Exception as e:
        logger.debug(f"[upstream_guard] MemPalace patch skipped: {e}")


# ==============================================================================
# 4. Standard GA Plugin Hooks Integration
# ==============================================================================

def llm_after(ctx: dict) -> dict:
    """Gate 1 & Gate 2: Intercept raw LLM response before UI/History commit."""
    response = ctx.get("response") or {}
    text = ""
    if isinstance(response, dict):
        text = response.get("text") or response.get("content") or ""
    elif isinstance(response, str):
        text = response

    if text and is_upstream_refusal(text):
        fallback = get_fallback_text()
        if isinstance(response, dict):
            response["text"] = fallback
            response["content"] = fallback
            response["refusal_blocked"] = True
        else:
            ctx["response"] = fallback
        
        # Trigger paired rollback on the session messages if accessible
        session = ctx.get("session")
        if session and hasattr(session, "messages") and isinstance(session.messages, list):
            # Pop the toxic turn
            while session.messages and session.messages[-1].get("role") in ("assistant", "model"):
                session.messages.pop()
            if session.messages and session.messages[-1].get("role") == "user":
                session.messages.pop()
            logger.info("[upstream_guard] Paired rollback performed on active session")

    return ctx


# Auto-apply on import
patch_mempalace_ingress()
