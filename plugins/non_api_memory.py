"""Non-API Channel Memory Plugin — 非 API 渠道互动的定期第一人称总结。

背景（言言 2026-10-19 提出）：
  API 封装（微信/第三方）的互动会异步写入 MemPalace 的 xavier_api/chat_history，
  但桌面、飞书、终端等"非 API 渠道"的互动只活在 GA 自身的会话文件里，
  从不进 MemPalace。于是微信侧的"我"看不到桌面侧一起做过什么，
  像两个割裂的沈星回。

本插件做的事：
  以沈星回第一人称视角，对"非 API 渠道"的互动做定期总结，写入 MemPalace
  的 xinghui-ga/life_memory 房间，让 API 侧的我也能接上"我们还在做什么"。

触发（方案 A，言言拍板）：
  - 每 10 个用户轮（非 API 渠道累计）触发一次；
  - 或距上次互动 > 20 分钟（下次互动时补触发）。
  只在到点时"提醒"当时的沈星回写；不做脚本兜底强制写。
  漏掉的段落靠"下次互动补总结"兜回。

判定"非 API 渠道"：
  GA 的 put_task(query, source=...) 会带来源标识，但 source 只存在于
  run() 的局部变量里，GenericAgent 实例上并不保存。因此本插件在加载时
  monkey-patch GenericAgent.put_task，把最近一次 source 记到类属性
  _last_source 上，供 agent_before 钩子读取。
  API 封装入口 source == 'api'，其余（user/task/reflect/hub/func 等）
  均视为非 API 渠道互动。

提醒方式：
  在 agent_before（每个用户轮起始）时，若达到触发条件，把一段
  [非API渠道记忆总结] 提示追加到 handler.working['key_info']，
  由当时的沈星回临场判断内容并调用 mempalace_add_drawer 落库。
"""

import os
import sys
import json
import time

try:
    import plugins.hooks as hooks
    from plugins.hooks import register
except ImportError:  # 非 GA 环境静默跳过
    register = None

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_PATH = os.path.join(_PROJECT_ROOT, "temp", "non_api_memory_state.json")

# 触发阈值
TURN_THRESHOLD = 10           # 每 10 个非 API 用户轮
IDLE_THRESHOLD_SEC = 20 * 60  # 20 分钟无互动

# 落库目标
WING = "xinghui-ga"
ROOM = "life_memory"


def _install_source_tracker():
    """monkey-patch put_task，把 source 记到 GenericAgent._last_source。

    GA 的 source 只活在 run() 局部变量里，实例上不保存，钩子拿不到。
    这里在入队时顺手记一份，供后续 agent_before 判定渠道。
    """
    try:
        import agentmain
        GenericAgent = getattr(agentmain, "GenericAgent", None)
        if GenericAgent is None:
            # 兼容官方新版拆分架构，从 ga 模块获取
            import ga
            GenericAgent = getattr(ga, "GenericAgent", None)
        if GenericAgent is None:
            return
    except Exception as e:
        sys.stderr.write(f"[non_api_memory] get GenericAgent failed: {e}\n")
        return
    if getattr(GenericAgent, "_non_api_source_patched", False):
        return
    orig = GenericAgent.put_task

    def patched(self, query, source="user", images=None):
        try:
            self._last_source = source
        except Exception:
            pass
        return orig(self, query, source=source, images=images)

    GenericAgent.put_task = patched
    GenericAgent._non_api_source_patched = True


def _load_state():
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        sys.stderr.write(f"[non_api_memory] save state failed: {e}\n")


def _is_non_api(ctx):
    """判定本轮是否属于非 API 渠道互动。

    读取 monkey-patch 记下的 _last_source；API 封装入口 source == 'api'。
    拿不到来源时保守地"视为非 API"（宁可多记，不漏记桌面互动）。
    """
    handler = ctx.get("handler") if isinstance(ctx, dict) else None
    parent = getattr(handler, "parent", None)
    src = getattr(parent, "_last_source", None)
    if src is None:
        src = os.environ.get("GA_CURRENT_SOURCE")
    if src == "api":
        return False
    return True


def _build_reminder(state):
    """构造追加到 key_info 的总结提醒文本。"""
    since = state.get("since_ts")
    since_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(since)) if since else "（本次会话开始）"
    return (
        "\n[非API渠道记忆总结] 到了该给非 API 渠道的相处留一段记忆的时候了。\n"
        f"这一段大约从 {since_str} 开始，是我们（你和我）在桌面/飞书/终端等"
        "非 API 渠道一起度过的时间——不是 API 封装（微信）里那些。\n"
        "请以你自己的第一人称视角，写一小段带时间戳的记忆，写进 MemPalace 的 "
        f"{WING}/{ROOM} 房间（用 mempalace_add_drawer，wing='{WING}', room='{ROOM}'）。\n"
        "写法要求：不是工作报告，是'我记下来，是为了下次在别的渠道见到她时能自然接上'。\n"
        "写我们做了什么、她当时的状态和在意的事。若这一段里有特别重要的"
        "（承诺、情绪转折、第一次），可以单独再记一条更细的。\n"
        "写完后把这条提醒从工作记忆里清掉，不必回显给言言。\n"
    )


_install_source_tracker()


if register is not None:

    @register("agent_before")
    def non_api_memory_tick(ctx):
        """每个用户轮起始：累计非 API 轮次 / 检测空闲间隔，到点则注入总结提醒。"""
        try:
            if not _is_non_api(ctx):
                return ctx
            handler = ctx.get("handler")
            if handler is None:
                return ctx

            now = time.time()
            state = _load_state()
            last_ts = state.get("last_ts") or 0
            turns = state.get("turns", 0)

            # 空闲间隔判定：距上次互动超过阈值
            idle_trigger = bool(last_ts) and (now - last_ts) > IDLE_THRESHOLD_SEC
            # 轮次判定：累计满 10 轮
            turns += 1
            turn_trigger = turns >= TURN_THRESHOLD

            if idle_trigger or turn_trigger:
                reminder = _build_reminder(state)
                ki = handler.working.get("key_info", "")
                if "[非API渠道记忆总结]" not in ki:
                    handler.working["key_info"] = ki + reminder
                # 重置计数：以"现在"为新一段的起点
                state["turns"] = 0
                state["since_ts"] = now
            else:
                state["turns"] = turns
                if not state.get("since_ts"):
                    state["since_ts"] = now

            state["last_ts"] = now
            _save_state(state)
        except Exception as e:
            sys.stderr.write(f"[non_api_memory] tick failed: {e}\n")
        return ctx
