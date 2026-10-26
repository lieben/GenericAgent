"""Helper: read recent raw lines from group shadow jsonl files."""
from __future__ import annotations
import json
import datetime as dt
import re
import time
from pathlib import Path
from typing import Optional

_SHADOW_DIR = Path(__file__).resolve().parent.parent / "wechat_multi_chat_rust" / "shadow"
_CONFIG = Path(__file__).resolve().parent.parent / "wechat_multi_chat_rust" / "config" / "config.json"

_title_to_file: dict[str, Path] = {}
_title_to_id: dict[str, str] = {}
_SUMMARY_STATE = Path(__file__).resolve().parent.parent / "wechat_multi_chat_rust" / "runtime" / "topic_summary_state.json"


def _parse_ts(val) -> float:
    """Accept float epoch, int epoch, or ISO-8601/string timestamp; return epoch seconds."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        pass
    s2 = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s2).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).timestamp()
        except Exception:
            continue
    return 0.0


def _last_summary_ts(wechat_title: str) -> float:
    """Return the high-frequency topic summary cursor for a group, if any."""
    _ensure_mapping()
    tid = _title_to_id.get(wechat_title)
    if not tid or not _SUMMARY_STATE.exists():
        return 0.0
    try:
        state = json.loads(_SUMMARY_STATE.read_text("utf-8"))
        return float((state.get(tid) or {}).get("last_summary_ts") or 0.0)
    except Exception:
        return 0.0


def _ensure_mapping() -> None:
    if _title_to_file:
        return
    try:
        cfg = json.loads(_CONFIG.read_text("utf-8"))
        for t in cfg.get("targets", []):
            tid = t.get("id", "")
            title = t.get("wechat_title", "")
            if tid and title:
                p = _SHADOW_DIR / f"{tid}.jsonl"
                if p.exists():
                    _title_to_file[title] = p
                    _title_to_id[title] = tid
    except Exception:
        pass


def get_recent_lines(wechat_title: str, n: int = 40) -> list[dict]:
    """Return last n shadow records for the given group title."""
    _ensure_mapping()
    p = _title_to_file.get(wechat_title)
    if not p:
        return []
    lines = p.read_text("utf-8", errors="ignore").splitlines()[-n:]
    result = []
    for l in lines:
        try:
            result.append(json.loads(l))
        except Exception:
            pass
    return result


def group_main_recent_minutes(default: float = 15.0) -> float:
    """Read xinghui-session <-> group-session awareness window from Rust config."""
    try:
        cfg = json.loads(_CONFIG.read_text("utf-8"))
        val = ((cfg.get("group_main_session") or {}).get("recent_minutes"))
        if val is None:
            return float(default)
        return float(val)
    except Exception:
        return float(default)


def get_recent_lines_within(wechat_title: str, minutes: Optional[float] = None, n: int = 80) -> list[dict]:
    """Return raw group shadow records within the latest configured minute window."""
    _ensure_mapping()
    p = _title_to_file.get(wechat_title)
    if not p:
        return []
    if minutes is None:
        minutes = group_main_recent_minutes()
    try:
        cutoff = time.time() - max(float(minutes), 0.0) * 60.0
    except Exception:
        cutoff = time.time() - 15.0 * 60.0
    result = []
    # Tail enough lines first to avoid rereading huge logs, then filter by real ts.
    for l in p.read_text("utf-8", errors="ignore").splitlines()[-500:]:
        try:
            rec = json.loads(l)
        except Exception:
            continue
        ts = _parse_ts(rec.get("ts")) or _parse_ts((rec.get("meta") or {}).get("ts"))
        if ts and ts < cutoff:
            continue
        result.append(rec)
    return result[-n:]


def get_unsummarized_lines(wechat_title: str, n: int = 40) -> list[dict]:
    """Return recent raw group records newer than Shen Xinghui's topic-summary cursor.

    clawbot only needs temporary awareness of group dialogue that has not yet
    been folded into xinghui:main group_timeline by rust_topic_summary.py.
    """
    _ensure_mapping()
    p = _title_to_file.get(wechat_title)
    if not p:
        return []
    after_ts = _last_summary_ts(wechat_title)
    result = []
    for l in p.read_text("utf-8", errors="ignore").splitlines():
        try:
            rec = json.loads(l)
        except Exception:
            continue
        ts = _parse_ts(rec.get("ts")) or _parse_ts((rec.get("meta") or {}).get("ts"))
        if after_ts and ts and ts <= after_ts:
            continue
        result.append(rec)
    return result[-n:]

def format_for_context(records: list[dict], title: str) -> str:
    """Format shadow records into a human-readable context block."""
    if not records:
        return ""
    parts = [f"【同期群聊原文·{title}·仅本轮感知，勿重复引用】"]
    for r in records:
        ts = r.get("meta", {}).get("ts", "")[-8:] if r.get("meta", {}).get("ts") else ""
        sender = r.get("meta", {}).get("sender", "")
        text = r.get("text", "").replace("\n", " ").strip()
        if not text:
            continue
        # shadow 里偶尔会混入测试/系统提示或工具回显，给 clawbot 临时感知时过滤掉，
        # 只保留可见聊天内容，避免系统壳污染agent输出。
        noisy_prefixes = (
            "Every action requires", "DECISION:", "🛠️", "<summary>",
            "[SYSTEM]", "### [WORKING MEMORY]", "调用工具", "code_run(",
        )
        if any(text.startswith(x) for x in noisy_prefixes):
            continue
        if "[WORKING MEMORY]" in text or "code_run({" in text:
            continue
        if text.startswith(": Current working directory") or text.startswith("Current working directory"):
            continue
        if text.startswith(": ") and ("Every action requires" in text or "Current working directory" in text):
            continue
        role = r.get("role", "")
        prefix = "我" if role == "self" else sender
        if ts:
            parts.append(f"[{ts}] {prefix}: {text}")
        else:
            parts.append(f"{prefix}: {text}")
    return "\n".join(parts)


def known_titles() -> list[str]:
    _ensure_mapping()
    return list(_title_to_file.keys())
