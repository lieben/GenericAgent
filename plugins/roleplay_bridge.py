"""Roleplay bridge plugin.

Exposes the isolated roleplay reader as a dynamic tool without modifying
GA core files (ga.py / assets/tools_schema.json).
Allows GA main loop, Xavier API, and Clawbot to query roleplay stories and MemPalace
wing_roleplay archives to discuss IF scenarios naturally without confusing them with real life.
"""
import json
import sys
from pathlib import Path
from agent_loop import StepOutcome
from plugins.hooks import register

ROOT = Path(__file__).resolve().parent.parent

def _get_mempalace_client():
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import ga_api_mempalace
        return ga_api_mempalace.MemPalaceMCPClient()
    except Exception:
        return None


_TOOL_NAME = "roleplay_story"
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "只读查看体验过的独立小剧场剧本或 MemPalace 虚构剧本归档。"
            "当用户在日常对话中提及小剧场、剧本、IF线故事时调用。"
            "注意：本工具返回的内容均为虚构故事演绎，绝非现实生活真实事件；你应跳出戏外自然探讨评价剧本，但绝不得将剧本中的设定与事件混淆为主线现实事实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "剧本名称或关键词搜索（例如：月宫线、校园AU、slug标识等）",
                },
                "slug": {
                    "type": "string",
                    "description": "剧本专属英文标识（可选）",
                },
                "session_id": {
                    "type": "string",
                    "description": "剧本存档标识（可选，默认最新）",
                },
            },
        },
    },
}


def _roleplay_story_impl(args: dict) -> str:
    query = str(args.get("query") or "").strip().lower()
    slug = str(args.get("slug") or "").strip()
    sid = str(args.get("session_id") or "").strip()

    output_sections = []

    # 1. Search MemPalace wing_roleplay first
    client = _get_mempalace_client()
    if client and query:
        try:
            mp_res = client.call("mempalace_search", {
                "query": query,
                "wing": "wing_roleplay",
                "limit": 3
            })
            if mp_res and isinstance(mp_res, list) and len(mp_res) > 0:
                mp_texts = []
                for d in mp_res:
                    content = d.get("content") or ""
                    mp_texts.append(content)
                output_sections.append("【MemPalace 剧本长期归档库检索结果】:\n" + "\n---\n".join(mp_texts))
        except Exception as e:
            pass

    # 2. Search local stories
    try:
        from plugins.roleplay import storage
        sessions = storage.list_sessions()
        matched = []
        for s in sessions:
            s_slug = s.get("slug", "")
            s_title = s.get("title", "")
            if slug and s_slug == slug:
                matched.append(s)
            elif query and (query in s_slug.lower() or query in s_title.lower()):
                matched.append(s)
        
        if matched:
            local_texts = []
            for item in matched[:2]:
                detail = storage.get_session(item["slug"], item["session_id"])
                c = detail.get("canon", {})
                sm = detail.get("summary", {})
                local_texts.append(
                    f"- 剧本: 《{detail.get('title')}》 ({detail.get('slug')}/{detail.get('session_id')})\n"
                    f"  设定: {c.get('world_setting', '')} | 扮演: {c.get('roleplay_persona', '')}\n"
                    f"  回合: {sm.get('turn', 0)} 轮 | 最新概要: {sm.get('summary', '')}"
                )
            output_sections.append("【本地剧本实时进度】:\n" + "\n".join(local_texts))
    except Exception as e:
        pass

    if not output_sections:
        return f"未找到与 '{query or slug}' 相关的虚构小剧场记录。用户可能是在提起一个新的剧本想法。"

    return (
        "【只读小剧场感知通知】\n" +
        "\n\n".join(output_sections) +
        "\n\n【提示】：以上均为虚构剧本故事，已知晓此剧本，可自然回应并探讨该剧本剧情或人设，绝不混淆现实日常。"
    )


@register("tools_schema_filter")
def on_tools_schema_filter(schemas: list, state=None) -> StepOutcome:
    if not any(s.get("function", {}).get("name") == _TOOL_NAME for s in schemas):
        schemas.append(_TOOL_SCHEMA)
    return StepOutcome.CONTINUE


@register("agent_tool_override")
def on_agent_tool_override(tool_name: str, args: dict, state=None) -> StepOutcome:
    if tool_name == _TOOL_NAME:
        result = _roleplay_story_impl(args)
        return StepOutcome.FINISHED(payload=result)
    return StepOutcome.CONTINUE
