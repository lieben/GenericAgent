"""Staged Model Plugin — 按轮次类型切换模型（收尾轮/落档轮用 gemini-pro）。

言言 2026-10-19 需求：
  - 不改变 GA 原运作：飞书/桌面各用各的模型选择，工具轮照旧。
  - 唯一新规则：**最后一轮输出（不再调工具、给最终答复那轮）统一用 gemini-pro**。
  - 事实落档（obsidian md、小红书最终文案等 file_write/file_patch）也走 gemini-pro。
  - xavier session 的工具调用模型绑定成 deepseek-flash。

实现（纯插件，不动核心）：
  挂 llm_before / llm_after / tool_before / agent_after 钩子，在每轮开始前
  把 client.backend（MixinSession）的 _cur_idx 指向目标 session。

判定逻辑（预判 + 纠错）：
  - llm_before 预判：若上一轮是工具轮（本轮带着工具结果回来）→ 切 gemini-pro；
    否则用原模型（各前端自己的选择）。
  - llm_after 纠错：看本轮 response.tool_calls：
      * 本轮调了工具 → 是工具轮，标记"下轮切回原模型"（除非是落档轮，保持 gemini-pro）
      * 本轮没调工具 → 收尾轮，gemini-pro 正确
  - tool_before 识别 file_write/file_patch → 标记落档轮，强制 gemini-pro
  - xavier session：工具轮强制 deepseek-flash

切换方式：
  MixinSession._sessions 是 session 列表，_cur_idx 指向当前使用的下标。
  _pick() 有 spring_back（默认 300s 后回 0）。我们直接设 _cur_idx 并同步
  _switched_at=now，防止被 spring_back 拉回。
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugins.hooks import register

# ── 配置 ──────────────────────────────────────────────────────────────
OUTPUT_MODEL = "gemini-pro"        # 收尾轮 / 落档轮统一用这个
XAVIER_TOOL_MODEL = "deepseek-flash"  # xavier session 的工具轮用这个
XAVIER_SESSION_ID = os.environ.get("GA_XAVIER_SESSION_ID", "sess-53138a368967")
# 落档类工具（写文件 = 事实落档）
ARCHIVE_TOOLS = {"file_write", "file_patch"}

# 每轮状态挂在 handler 上（handler 每轮重建，但 parent 是持久的）
_STATE_ATTR = "_staged_model_state"


def _get_state(handler):
    """获取/初始化本轮对话的状态（挂在 handler.parent 上，跨轮持久）。"""
    parent = getattr(handler, "parent", None) or handler
    st = getattr(parent, _STATE_ATTR, None)
    if st is None:
        st = {"last_was_tool": False, "force_next_output": False, "archive_round": False}
        try:
            setattr(parent, _STATE_ATTR, st)
        except Exception:
            pass
    return st


def _find_backend(client):
    """从 client 拿到 MixinSession（或单 session）。"""
    return getattr(client, "backend", None)


def _session_names(backend):
    sess = getattr(backend, "_sessions", None)
    if sess is None:
        return [getattr(backend, "name", "")]
    return [getattr(s, "name", "") for s in sess]


def _switch_to(backend, target_name):
    """把 MixinSession 切到指定名字的 session。返回是否成功。"""
    sess = getattr(backend, "_sessions", None)
    if sess is None:
        return False
    for i, s in enumerate(sess):
        if getattr(s, "name", "") == target_name:
            try:
                backend._cur_idx = i
                backend._switched_at = time.time()
                return True
            except Exception:
                return False
    return False


def _is_xavier(handler):
    """判断当前会话是不是 xavier 那条线。"""
    parent = getattr(handler, "parent", None)
    if parent is None:
        return False
    # 方式1: task_dir 里含 session id
    td = getattr(parent, "task_dir", "") or ""
    if XAVIER_SESSION_ID in str(td):
        return True
    # 方式2: 环境变量
    if os.environ.get("GA_XAVIER_ACTIVE") == "1":
        return True
    return False


def llm_before(ctx):
    """每轮开始前：决定本轮用哪个模型。"""
    try:
        client = ctx.get("client")
        handler = ctx.get("handler")
        messages = ctx.get("messages") or []
        if client is None or handler is None:
            return ctx
        backend = _find_backend(client)
        if backend is None:
            return ctx

        st = _get_state(handler)
        names = _session_names(backend)
        xavier = _is_xavier(handler)

        # 判断本轮是否"带着工具结果回来"（上一轮是工具轮）
        last_msg = messages[-1] if messages else {}
        has_tool_results = bool(last_msg.get("tool_results"))

        # 决策本轮目标模型
        target = None
        if st.get("archive_round"):
            # 落档轮：强制 gemini-pro
            target = OUTPUT_MODEL
        elif has_tool_results:
            # 上一轮是工具轮 → 本轮可能是总结/收尾 → 用 gemini-pro
            target = OUTPUT_MODEL
        elif xavier:
            # xavier 的工具轮 → deepseek-flash
            target = XAVIER_TOOL_MODEL
        # 否则 target=None → 保持原模型（各前端自己的选择）

        if target and target in names:
            _switch_to(backend, target)
            print(f"[staged_model] turn {ctx.get('turn')} -> {target} "
                  f"(archive={st.get('archive_round')}, tool_results={has_tool_results}, xavier={xavier})")
        return ctx
    except Exception as e:
        sys.stderr.write(f"[staged_model] llm_before failed: {e}\n")
        return ctx


def llm_after(ctx):
    """每轮结束后：记录本轮是否工具轮，决定下轮是否切回原模型。"""
    try:
        handler = ctx.get("handler")
        response = ctx.get("response")
        if handler is None:
            return ctx
        st = _get_state(handler)
        tool_calls = getattr(response, "tool_calls", None) or []
        st["last_was_tool"] = bool(tool_calls)
        # 本轮调了工具：如果不是落档轮，下轮切回原模型
        if tool_calls:
            # 落档轮（file_write/file_patch）保持 gemini-pro 到收尾
            st["force_next_output"] = st.get("archive_round", False)
        else:
            st["force_next_output"] = False
        # 落档标记用完即清
        st["archive_round"] = False
        return ctx
    except Exception as e:
        sys.stderr.write(f"[staged_model] llm_after failed: {e}\n")
        return ctx


def tool_before(ctx):
    """工具执行前：识别落档类工具，标记落档轮。"""
    try:
        handler = ctx.get("handler")
        tool_name = ctx.get("tool_name")
        if handler is None:
            return ctx
        if tool_name in ARCHIVE_TOOLS:
            st = _get_state(handler)
            st["archive_round"] = True
        return ctx
    except Exception as e:
        sys.stderr.write(f"[staged_model] tool_before failed: {e}\n")
        return ctx


def agent_after(ctx):
    """对话结束：清理状态。"""
    try:
        handler = ctx.get("handler")
        if handler is None:
            return ctx
        parent = getattr(handler, "parent", None) or handler
        if hasattr(parent, _STATE_ATTR):
            try:
                delattr(parent, _STATE_ATTR)
            except Exception:
                pass
        return ctx
    except Exception as e:
        return ctx


register("llm_before")(llm_before)
register("llm_after")(llm_after)
register("tool_before")(tool_before)
register("agent_after")(agent_after)
