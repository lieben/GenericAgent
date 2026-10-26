"""Xinghui WeChat image sharing helper.

Self-contained helper for generating photo/plog style images and returning a
local image path that can be sent through WxBotClient.send_image.

Provider selection is environment driven. No secrets are read from files here.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
GA_ROOT = Path(__file__).resolve().parents[2]
from typing import Optional


_IMAGE_WORDS = (
    "拍", "照片", "图片", "图", "plog", "PLOG", "随手拍", "发我看看", "给我看看",
    "看看你", "看一下", "午餐", "中午吃", "晚饭", "早餐", "路上", "任务", "出任务",
)
_EXPLICIT_IMAGE_WORDS = ("照片", "图片", "plog", "PLOG", "随手拍", "拍给", "发图", "发我看看", "给我看看")
_EXPLICIT_CAPTURE_PATTERNS = (
    r"拍(?:张|一张|个|一下)?(?:照片|图片|照|图)?(?:给我|我看看|看看)?",
    r"(?:发|给)(?:我)?(?:张|一张|个)?(?:照片|图片|照|图)(?:看看)?",
    # Natural explicit requests often name the subject instead of saying 照片/图片,
    # e.g. “发一张你的床我看看”, “拍一下窗外给我看看”.
    r"(?:发|给)(?:我)?(?:张|一张|个|一下)?[^。！？\n]{0,24}(?:我看看|给我看看|看看)",
    r"拍(?:张|一张|个|一下)?[^。！？\n]{0,24}(?:给我看看|我看看|看看)",
    r"(?:看看|看一下|给我看看|发我看看)(?:你)?(?:现在)?(?:在)?(?:哪里|哪儿|周围|身边|眼前|看到的)",
    r"(?:看看|看一下|给我看看|发我看看)[^。！？\n]{0,18}(?:桌面|房间|屋里|窗外|环境|周围|身边|眼前|路上|现场|天空|夜景|午饭|午餐|晚饭|早餐|吃的|餐|火锅|咖啡)",
    r"(?:你)?(?:现在)?(?:在)?(?:哪里|哪儿).*(?:拍|照片|图片|看看)",
)
_REFUSAL_OR_DELAY_WORDS = (
    "不拍", "不给你看", "不给看", "不给你发", "不发给你", "不想拍", "不能拍",
    "没法拍", "拍不了", "下次再拍", "下次拍", "下次再发", "以后再拍", "改天再拍",
)
_NEGATIVE_WORDS = ("别发图", "不用发图", "不要图片", "别拍", "不用拍")
_IMAGE_PROMPT_RE = re.compile(r"\[IMAGE_PROMPT\s*[:：]\s*(.*?)\]", re.DOTALL | re.IGNORECASE)


def extract_image_prompt(text: str) -> Optional[str]:
    """Return the first model-authored final image prompt from [IMAGE_PROMPT: ...]."""
    if not text:
        return None
    m = _IMAGE_PROMPT_RE.search(text)
    if not m:
        return None
    prompt = (m.group(1) or "").strip()
    # Avoid treating intention labels / ultra-short labels as prompts, but allow
    # concise explicit visual prompts authored by the model.
    if len(prompt) < 12:
        return None
    if not re.search(r"[一-鿿A-Za-z]", prompt):
        return None
    if not re.search(r"自拍|照片|图片|人像|肖像|全身|半身|特写|镜子|三脚架|监控|路人帮拍|街拍|穿着|外套|内搭|项链|眼镜|动作|姿势|室内|室外|夜晚|白天|selfie|photo|portrait|mirror|tripod|surveillance|outfit|necklace|glasses|pose|indoor|outdoor", prompt, re.I):
        return None
    return prompt

def strip_image_prompt_tags(text: str) -> str:
    """Remove image-prompt control tags from visible WeChat text."""
    return _IMAGE_PROMPT_RE.sub('', text or '').strip()


_VISUAL_CONTEXT_LEAK_RE = re.compile(
    r'(?:</?(?:earlier_context|history|key_info)>|'
    r'If you need to show files to user|'
    r'###\s*\[(?:WORKING MEMORY|SYSTEM TIPS|PLANNING|ACTIONS|TOOL|TOOL_CALL|TOOL_RESULT)\]|'
    r'\[(?:Contex(?:t)?|SYSTEM|CONSTITUTION|RULES|Memory|DANGER)\b|'
    r'\[Contex(?:t)?\]?|'
    r'^Contex(?:t)?\s*[:：]|'
    r'cwd\s*=\s*/|\.\./memory/|'
    r'(?:^|\s)[\s🛠🔧⚙️\ufe0f]*(?:update_working_checkpoint|start_long_term_update|ask_user|code_run|file_read|file_patch|file_write|web_scan|web_execute_js)\s*\(|'
    r'(?:stdout|stderr|exit_code|recipient_name|tool_uses|inline_eval|save_to_file|switch_tab_id)\b|'
    r'\{\s*"(?:cwd|script|timeout|type|status|stdout|stderr|exit_code|recipient_name|tool_uses)"\s*:)',
    re.IGNORECASE,
)


def _strip_context_wrappers(text: str) -> str:
    text = str(text or '')
    text = re.sub(r'(?is)\[Contex(?:t)?[^\n\[]*?(?:\.{3}|…|\)|）)', ' ', text)
    text = re.sub(r'(?is)\[Contex(?:t)?[^\n]*', ' ', text)
    text = re.sub(r'(?is)\[/?(?:earlier_context|history|key_info)\][^\n]*', ' ', text)
    return text


_NON_VISUAL_STATUS_RE = re.compile(
    r'(?:实时查证|不能把不确定的信息当成结果|链接或截图|按你给的来源|凭已有记忆判断|明确标出来|无法查证|没能完成查证)',
    re.IGNORECASE,
)


def _clean_visual_context(text: str) -> str:
    """Strip harness/tool/memory/status pollution before using text for image intent."""
    cleaned = []
    for line in _strip_context_wrappers(text).splitlines():
        line = line.strip()
        if not line or _VISUAL_CONTEXT_LEAK_RE.search(line) or _NON_VISUAL_STATUS_RE.search(line):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


def _clean_explicit_image_prompt(prompt: str) -> str:
    prompt = _clean_visual_context(prompt)
    prompt = re.sub(r'(?is)\[/?IMAGE_PROMPT\s*[:：]?\s*\]?', '', prompt).strip()
    return prompt[:1200].strip()


def is_explicit_capture_request(user_text: str) -> bool:
    """Return True when the user is directly asking Xinghui to take/send a photo now."""
    text = user_text or ""
    if any(w in text for w in _NEGATIVE_WORDS):
        return False
    return any(re.search(p, text, re.I) for p in _EXPLICIT_CAPTURE_PATTERNS)


def _assistant_is_deferring_capture(assistant_text: str) -> bool:
    text = assistant_text or ""
    return any(w in text for w in _REFUSAL_OR_DELAY_WORDS)


def _capture_scene_hint(user_text: str, assistant_text: str = "") -> str:
    text = f"{user_text or ''}\n{assistant_text or ''}"
    explicit_away = re.search(r"路上|街|车|出任务|现场|夜景|天空|外面|户外|咖啡馆|餐厅|饭店|店里|商场|公司|办公室|学校|医院|酒店|机场|车站|公园|海边|山上|基地", text)
    if explicit_away:
        return "a first-person view of the current outside location, surroundings, or mission route, only scenery, road, sky, windows, lights, equipment, food/table items, or nearby objects as relevant; no people, no silhouettes, no shadows"
    if re.search(r"房间|屋|家里|在家|室内|床|桌|天花板|窗|沙发|休息|露台|阳台", text):
        return "a first-person view inside his large luxury finished penthouse flat or on its huge open terrace: spacious bright modern minimalist interior, open-plan room layout, wide floor-to-ceiling windows, refined contemporary furniture, high-end clean renovation, generous sense of space, terrace details when relevant; explicitly not small, not cramped, not shabby, not old-fashioned, not dirty, not cluttered, not damaged, not abandoned, not rundown; no people"
    if re.search(r"吃|饭|餐|烤肉|火锅|便当|咖啡|甜点|小吃", text):
        return "a first-person view of the food or table area, no people"
    return "a first-person view of what he is seeing right now, nearby objects and surroundings; no people, no body parts, no silhouettes, no shadows"


@dataclass
class ImageShareRequest:
    should_generate: bool
    reason: str = ""
    prompt: str = ""
    style: str = "photo"
    reference_images: Optional[list[str]] = None


def should_offer_image(user_text: str, assistant_text: str = "", proactive: bool = False) -> bool:
    """Heuristic v1 plus model-authored [IMAGE_PROMPT: ...] trigger."""
    user = user_text or ""
    text = f"{user}\n{assistant_text or ''}"
    if extract_image_prompt(assistant_text):
        return True
    if any(w in user for w in _NEGATIVE_WORDS):
        return False
    # A direct capture request is an action contract: even if the model says
    # "next time / I won't show you", the frontend should still try to make a
    # first-person photo. Provider failure is the only valid reason not to send.
    if is_explicit_capture_request(user):
        return True
    if proactive:
        return any(w in text for w in _IMAGE_WORDS) or True
    if any(w in text for w in _NEGATIVE_WORDS):
        return False
    if any(w in text for w in _EXPLICIT_IMAGE_WORDS):
        return True
    # Face/body/person requests are intentionally NOT an image trigger for the
    # Agnes chat-photo channel. This channel is first-person scenery/object only.
    # Natural cases such as “你中午吃的是什么” may benefit from a photo when
    # the answer itself mentions a concrete scene/meal. Keep this conservative.
    meal_q = re.search(r"(中午|午饭|午餐|晚饭|早餐|吃的?什么|吃了?啥)", user_text or "")
    visual_answer = re.search(r"(便当|餐|饭|面|咖啡|甜点|路边|窗|雨|灯|任务|现场)", assistant_text or "")
    return bool(meal_q and visual_answer)


def should_generate_image(user_text: str, assistant_text: str = "", proactive: bool = False) -> bool:
    """Backward-compatible alias for smoke tests / callers."""
    return should_offer_image(user_text, assistant_text, proactive=proactive)



def _agent_asset_dir(agent: Optional[str] = None) -> Path:
    """Return the per-agent reference image asset directory."""
    agent_name = (agent or os.environ.get("XINGHUI_IMAGE_AGENT") or "xinghui").strip() or "xinghui"
    base = os.environ.get("XINGHUI_IMAGE_ASSET_DIR") or str(GA_ROOT / "assets" / "agent_image_refs")
    return Path(base).expanduser() / agent_name


def list_reference_assets(agent: Optional[str] = None) -> list[Path]:
    root = _agent_asset_dir(agent)
    if not root.exists():
        return []
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    max_files = int(os.environ.get("XINGHUI_IMAGE_ASSET_SCAN_MAX", "80"))
    return sorted([p for p in root.iterdir() if p.is_file() and p.suffix.lower() in allowed])[:max_files]


_FACE_REF_WORDS = ("脸", "面部", "正面", "侧脸", "左侧", "右侧", "表情", "微笑", "生气", "发呆", "portrait", "selfie", "face", "front", "side", "expression")
_FACE_IMAGE_WORDS = ("自拍", "人像", "肖像", "头像", "半身", "全身", "特写", "正脸", "侧脸", "表情", "出镜", "入镜", "露脸", "本人", "自己的照片", "他的照片", "你自己的照片", "监控截图", "监控画面", "路人帮忙", "帮拍", "三脚架", "支架", "镜子", "镜面", "合照", "portrait", "selfie", "headshot", "profile", "face", "in frame", "visible", "appears", "mirror", "tripod", "surveillance", "cctv")


def _looks_like_face_asset(path: Path) -> bool:
    name = path.stem.lower()
    return any(w.lower() in name for w in _FACE_REF_WORDS)


def _wants_xinghui_face_image(user_text: str, assistant_text: str = "") -> bool:
    """Return True when the requested/generated image is meant to include {agent} himself.

    This is not limited to {user} explicitly specifying a portrait. If {agent}'s
    own visible reply or IMAGE_PROMPT decides to show himself -- selfie, mirror
    photo, tripod/ passer-by shot, surveillance-like still, or any photo where
    {agent} appears in frame -- route to GPT with {agent} face references. Ordinary
    first-person POV photos of what he sees remain face-free/Gemini.
    """
    text = f"{user_text or ''} {assistant_text or ''}".lower()
    # Strong self-appearance cues from either user request or {agent}-authored prompt.
    if re.search(
        r"自拍|人像|肖像|头像|正脸|侧脸|露脸|出镜|入镜|半身|全身|特写|"
        r"你的脸|你.*脸|自己的脸|自己的照片|他的照片|你自己的照片|本人照片|"
        r"监控截图|监控画面|路人帮(?:忙)?拍|帮拍|三脚架|支架|镜子|镜面|"
        r"selfie|portrait|headshot|profile|mirror\s*(?:selfie|photo)|tripod|surveillance|cctv|"
        r"xavier\s+(?:appears|is visible|in frame|on camera)|"
        # Match {agent}/me/myself as the visible subject, but do not match possessives
        # like "photo of {agent}'s dinner tray" / "{agent}’s desk", which are ordinary POV shots.
        r"(?:photo|image|shot)\s+of\s+(?:xavier(?!['’]s\b)|me\b|myself\b)",
        text,
        re.I,
    ):
        return True
    # If an English/Chinese prompt explicitly says {agent} is visible/in the frame.
    if re.search(r"(?:{agent}|{agent}|xavier).{0,40}(?:出镜|入镜|露脸|本人|可见|在画面里|样子|照片|长什么样|appearance|looks?|appears|visible|in frame|on camera)", text, re.I):
        return True
    if re.search(r"(?:看看|看一下|看下|想看).{0,24}(?:{agent}|{agent}|xavier).{0,24}(?:样子|照片|本人|脸|现在)", text, re.I):
        return True
    return False


def _select_face_reference(user_text: str, assistant_text: str = "", *, agent: Optional[str] = None) -> list[str]:
    assets = [p for p in list_reference_assets(agent) if _looks_like_face_asset(p)]
    if not assets:
        return []
    text = f"{user_text or ''} {assistant_text or ''}".lower()

    # Filename-driven semantic selection.  The assets are intentionally named by
    # {user} (e.g. 正面.PNG / 正面微笑.PNG / 右侧脸.PNG), so prefer exact
    # angle+expression combinations before falling back to neutral front refs.
    want_right = bool(re.search(r"右侧|右脸|right", text, re.I))
    want_left = bool(re.search(r"左侧|左脸|profile|side|侧脸", text, re.I)) and not want_right
    want_front = bool(re.search(r"正面|正脸|front", text, re.I)) or not (want_left or want_right)
    want_angry = bool(re.search(r"生气|angry", text, re.I))
    want_smile = bool(re.search(r"微笑|笑|smile", text, re.I))
    want_blank = bool(re.search(r"发呆|blank|dazed", text, re.I))

    def has_any(name: str, tokens: tuple[str, ...]) -> bool:
        return any(t.lower() in name for t in tokens)

    scored: list[tuple[int, int, str, Path]] = []
    for path in assets:
        name = path.stem.lower()
        score = 0
        matched_specific = 0

        if want_right:
            if has_any(name, ("右侧", "右脸", "right")):
                score += 500; matched_specific += 1
            elif has_any(name, ("侧脸", "profile", "side")):
                score += 220
            if has_any(name, ("左侧", "左脸", "left")):
                score -= 350
        elif want_left:
            if has_any(name, ("左侧", "左脸", "left")):
                score += 500; matched_specific += 1
            elif has_any(name, ("侧脸", "profile", "side")):
                score += 220
            if has_any(name, ("右侧", "右脸", "right")):
                score -= 350
        elif want_front:
            if has_any(name, ("正面", "正脸", "front")):
                score += 420; matched_specific += 1
            # Generic front/selfie requests should not accidentally prefer side-face refs.
            if has_any(name, ("左侧", "右侧", "左脸", "右脸", "侧脸", "profile", "side")):
                score -= 180

        if want_angry:
            if has_any(name, ("生气", "angry")):
                score += 320; matched_specific += 1
            elif has_any(name, ("微笑", "笑", "smile", "发呆", "blank", "dazed")):
                score -= 140
        if want_smile:
            if has_any(name, ("微笑", "笑", "smile")):
                score += 320; matched_specific += 1
            elif has_any(name, ("生气", "angry", "发呆", "blank", "dazed")):
                score -= 140
        if want_blank:
            if has_any(name, ("发呆", "blank", "dazed")):
                score += 320; matched_specific += 1
            elif has_any(name, ("微笑", "笑", "smile", "生气", "angry")):
                score -= 140

        # Stable generic fallback: neutral frontal image first, then expression/front variants,
        # then side-face refs.  This also keeps all face refs usable by filename.
        if score <= 0:
            if name in {"正面", "front"}:
                score += 180
            elif has_any(name, ("正面", "正脸", "front")):
                score += 140
            elif has_any(name, ("微笑", "生气", "发呆")):
                score += 90
            elif has_any(name, ("左侧", "右侧", "侧脸")):
                score += 70
            else:
                score += 10

        # Sort by score, then prefer files matching more requested semantic parts,
        # then shorter names only as a final tie-breaker.
        scored.append((-score, -matched_specific, len(path.stem), path.name, path))

    max_refs = int(os.environ.get("XINGHUI_IMAGE_FACE_MAX_REFERENCES", "2"))
    return [str(p.resolve()) for *_, p in sorted(scored)[:max(1, max_refs)]]


def select_reference_images(user_text: str, assistant_text: str = "", *, style: str = "photo", agent: Optional[str] = None) -> list[str]:
    """Choose reference images from a per-agent asset folder by filename semantics.

    Face reference assets are special: when the final image is meant to include
    {agent} himself (including {agent}-authored selfie / mirror / tripod /
    passer-by / surveillance style photos), select face refs. Ordinary
    first-person plog photos of what he sees remain face-free and do not use
    face refs.
    """
    if _wants_xinghui_face_image(user_text, assistant_text):
        face_refs = _select_face_reference(user_text, assistant_text, agent=agent)
        if face_refs:
            return face_refs

    assets = [p for p in list_reference_assets(agent) if not _looks_like_face_asset(p)]
    if not assets:
        return []
    text = f"{user_text or ''} {assistant_text or ''} {style}".lower()
    generic_tags = {
        "food", "meal", "lunch", "dinner", "breakfast", "bento", "coffee", "dessert",
        "scene", "mission", "road", "street", "sky", "night", "window", "travel",
        "plog", "journal", "scrapbook", "layout", "photo", "daily", "casual", "room", "desk",
        "brand", "asset", "ref", "reference", "style", "item", "object",
        "餐", "饭", "午饭", "晚饭", "早餐", "午餐", "火锅", "咖啡", "甜点", "路", "街", "天空", "夜景", "房间", "桌", "日常", "随手拍",
    }

    def filename_tags(path: Path) -> list[str]:
        raw = re.split(r"[\s_\-.,，。()（）\[\]【】]+", path.stem.lower())
        tags: list[str] = []
        for tag in raw:
            tag = tag.strip()
            if not tag or tag in generic_tags:
                continue
            if re.fullmatch(r"[a-z0-9]+", tag) and len(tag) < 4:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+", tag) and len(tag) < 3:
                continue
            tags.append(tag)
        return tags

    scored: list[tuple[int, str, Path]] = []
    for path in assets:
        score = 0
        for tag in filename_tags(path):
            if tag in text:
                score += 100 + len(tag)
        if score:
            scored.append((-score, path.name, path))
    if not scored:
        return []
    max_refs = int(os.environ.get("XINGHUI_IMAGE_MAX_REFERENCES", "1"))
    return [str(p.resolve()) for _, _, p in sorted(scored)[:max(0, max_refs)]]

def build_image_prompt(user_text: str, assistant_text: str = "", now_text: Optional[str] = None) -> str:
    now_text = now_text or time.strftime("%Y-%m-%d %H:%M")
    clean_user_text = _clean_visual_context(user_text)
    clean_assistant_text = _clean_visual_context(assistant_text)
    scene_hint = _capture_scene_hint(clean_user_text, clean_assistant_text)
    base = f"""
Create a realistic casual phone photo / light plog snapshot from a FIRST-PERSON POV,
for a private chat. It should feel like a quiet daily-life photo just casually snapped and sent.

Current time: {now_text}
Scene to photograph now: {scene_hint}

STRICT VISUAL RULES (DO NOT VIOLATE):
- MUST BE FIRST-PERSON POV. You are seeing what he sees.
- If the user directly asks him to take/send a photo, generate the photo he would send NOW even if his text says he is shy, reluctant, or will do it next time.
- ABSOLUTELY NO faces or bodies of {{agent}} ({{agent}}). No selfies, no portraits, no close-up face, no close-up body, no mirror reflection of him.
- ABSOLUTELY NO faces or bodies of {{user}} (the user).
- No specific, named characters at all. Random blurred background pedestrians are okay ONLY IF it's a crowded public scene (like shopping/street).
- realistic mobile phone photo, natural lighting, candid composition. No posters, no fantasy illustrations, no cosplay.
- non-square mobile aspect ratio only: portrait 2:3 or landscape 3:2 phone-photo framing; never a square 1:1 image.
- no screenshots, no UI, no chat bubbles, no watermark.
- if food is mentioned, show the meal/table from first-person angle.
- if mission/travel is mentioned, show a quiet road, window, notebook, equipment corner, city light or sky detail.
- warm but restrained, intimate daily-life plog feeling.
""".strip()
    return base


PRIVATE_FACE_PROMPT_PREFIX = "这是我的照片，请维持我的形象不变，严禁白人化。在此基础上细节生图prompt："



def build_face_image_prompt(user_text: str, assistant_text: str = "", now_text: Optional[str] = None) -> str:
    """Legacy helper kept only for explicit model-authored face-image prompts.

    For {agent}-in-frame images we must not synthesize the detailed photo request in
    code, otherwise {user} cannot verify whether {agent} himself decided the shot
    form/content. Only the fixed identity prefix may be programmatically added.
    """
    explicit_prompt = extract_image_prompt(assistant_text)
    if not explicit_prompt:
        raise ValueError("face image prompt requires model-authored [IMAGE_PROMPT]")
    return f"{PRIVATE_FACE_PROMPT_PREFIX}\n{explicit_prompt.strip()}"


def make_request(user_text: str, assistant_text: str = "", proactive: bool = False, agent: Optional[str] = None) -> ImageShareRequest:
    clean_user_text = _clean_visual_context(user_text)
    clean_assistant_text = _clean_visual_context(assistant_text)
    explicit_prompt = _clean_explicit_image_prompt(extract_image_prompt(clean_assistant_text) or '')
    ok = bool(explicit_prompt) or should_offer_image(clean_user_text, clean_assistant_text, proactive=proactive)
    if not ok:
        return ImageShareRequest(False, reason="no_image_intent")
    text_for_style = f"{clean_user_text}\n{clean_assistant_text}"
    if re.search(r"中午|午饭|午餐|晚饭|早餐|吃的?什么|吃了?啥|饭|餐|便当|小吃|甜品|咖啡", text_for_style):
        style = "food"
    elif re.search(r"任务|出任务|路上|街|车窗|天空|夜景|现场", text_for_style):
        style = "scene"
    elif re.search(r"plog|PLOG|手账|拼图|随手拍", text_for_style):
        style = "plog"
    else:
        style = "photo"
    refs = select_reference_images(clean_user_text, clean_assistant_text, style=style, agent=agent)
    explicit_face_intent = bool(explicit_prompt) and _wants_xinghui_face_image(clean_user_text, clean_assistant_text)
    if explicit_face_intent:
        refs = _select_face_reference(clean_user_text, clean_assistant_text, agent=agent)
    has_face_ref = any(_looks_like_face_asset(Path(r)) for r in refs)
    if explicit_prompt:
        prompt = explicit_prompt
        if explicit_face_intent:
            if not has_face_ref:
                return ImageShareRequest(False, reason="missing_face_reference_asset")
            prompt = f"{PRIVATE_FACE_PROMPT_PREFIX}\n{prompt}"
            style = "photo"
        else:
            # Keep hard safety/POV constraints even when the main LLM authored the full prompt.
            prompt += "\n\nHard constraints: first-person POV from {agent}'s eyes; no selfie, no {agent} face/body, no {user}/user face/body, no named character portraits, no chat UI or watermark, realistic casual phone photo/plog style."
    else:
        if _wants_xinghui_face_image(clean_user_text, clean_assistant_text):
            return ImageShareRequest(False, reason="face_image_requires_explicit_prompt")
        prompt = build_image_prompt(clean_user_text, clean_assistant_text)
    if refs:
        if has_face_ref:
            pass
        else:
            prompt += "\n\nReference image guidance: The attached asset(s) represent specific brand assets, items, or fixed visual elements (e.g., a specific character's plushie, a branded hotpot, a signature weapon). You MUST use the provided reference image(s) to maintain visual consistency of these specific items/brands. DO NOT copy text or UI from the reference."
    return ImageShareRequest(True, reason="image_prompt_tag" if explicit_prompt else "image_intent", prompt=prompt, style=style, reference_images=refs)


def _write_mock_image(out_path: Path) -> Path:
    """Write a tiny valid PNG for dry-run tests when no provider is configured."""
    # 1x1 warm gray PNG
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mN8Vw8AAkMBgU6LZcoAAAAASUVORK5CYII="
    )
    out_path.write_bytes(raw)
    return out_path


def _http_image_generation(prompt: str, out_path: Path, *, base_url: str, api_key: str, model: str, size: str, reference_images: Optional[list[str]] = None) -> Path:
    url = base_url.rstrip("/") + "/v1/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
        "response_format": "b64_json",
    }
    if reference_images:
        payload["reference_images"] = reference_images
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("XINGHUI_IMAGE_TIMEOUT", "180"))) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"image provider HTTP {e.code}: {detail}") from e
    obj = json.loads(body)
    item = (obj.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if not b64 and item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as r2:
            out_path.write_bytes(r2.read())
        return out_path
    if not b64:
        raise RuntimeError(f"image provider returned no image data: {str(obj)[:300]}")
    out_path.write_bytes(base64.b64decode(b64))
    return out_path


def _image_prompt_needs_gpt(prompt: str, reference_images: Optional[list[str]] = None) -> bool:
    """Return True only for non-Gemini routes: Chinese text or {agent}/person images.

    {user}'s routing rule: if the requested image does not include a person/face
    and does not need Chinese/CJK characters rendered in the image, use Gemini.
    English-only layouts/posters and ordinary first-person plog/photo requests
    must stay on Gemini; face refs / specified-person selfies go to the private
    GPT/imyai route; explicit Chinese text generation goes to GPT.
    """
    text = prompt or ""
    no_text = bool(re.search(r"(?:不要文字|无文字|没有文字|不带文字|no\s*text|without\s+text)", text, re.I))
    explicit_chinese_text = bool(re.search(r"(?:中文|汉字|中文字|中文文字|中文排版|写上中文|中文标题|中文标语|Chinese\s+(?:text|title|characters?|lettering|copy|typography)|CJK\s+(?:text|title|characters?))", text, re.I))
    labeled_cjk_text = bool(re.search(r"(?:写上|包含|标题|副标题|正文|标语|文字|文案).{0,80}[\u4e00-\u9fff]", text))
    cjk_text_before_label = bool(re.search(r"[\u4e00-\u9fff].{0,80}(?:中文标题|中文标语|中文文案|标题|副标题|正文|标语|文案|文字)", text))
    asks_chinese_text = (explicit_chinese_text or labeled_cjk_text or cjk_text_before_label) and not no_text
    specified_selfie = bool(re.search(r"(?:指定人物|特定人物|指定角色|特定角色|specified\s+person|specific\s+person|specified\s+character|specific\s+character).{0,32}(?:自拍|selfie|portrait|人像|特写)|(?:自拍|selfie|portrait|人像|特写).{0,32}(?:指定人物|特定人物|指定角色|特定角色|specified\s+person|specific\s+person|specified\s+character|specific\s+character)", text, re.I))
    has_face_ref = any(_looks_like_face_asset(Path(r)) for r in (reference_images or []))
    return asks_chinese_text or specified_selfie or has_face_ref


def _image_provider_config(provider: str, *, use_gpt: bool) -> tuple[str, str, str, str]:
    """Return base_url/api_key/model/size for selected image route without reading key files."""
    if use_gpt:
        # Explicit private override for Yan Yan's imyai identity-reference route.
        # Keep it opt-in so ordinary GPT/Gemini routing is not generalized to imyai.
        private_model = os.environ.get("XINGHUI_PRIVATE_GPT_IMAGE_MODEL") or ""
        if private_model:
            base_url = os.environ.get("XINGHUI_PRIVATE_GPT_IMAGE_BASE_URL") or os.environ.get("XINGHUI_GPT_IMAGE_BASE_URL") or os.environ.get("XINGHUI_IMAGE_BASE_URL") or os.environ.get("OPENAI_IMAGE_BASE_URL") or ""
            api_key = os.environ.get("XINGHUI_PRIVATE_GPT_IMAGE_API_KEY") or os.environ.get("XINGHUI_GPT_IMAGE_API_KEY") or os.environ.get("XINGHUI_IMAGE_API_KEY") or os.environ.get("OPENAI_IMAGE_API_KEY") or ""
            size = os.environ.get("XINGHUI_PRIVATE_GPT_IMAGE_SIZE") or os.environ.get("XINGHUI_GPT_IMAGE_SIZE") or os.environ.get("XINGHUI_IMAGE_SIZE", "1024x1536")
            return base_url, api_key, private_model, size
        base_url = os.environ.get("XINGHUI_GPT_IMAGE_BASE_URL") or os.environ.get("XINGHUI_IMAGE_BASE_URL") or os.environ.get("OPENAI_IMAGE_BASE_URL") or ""
        api_key = os.environ.get("XINGHUI_GPT_IMAGE_API_KEY") or os.environ.get("XINGHUI_IMAGE_API_KEY") or os.environ.get("OPENAI_IMAGE_API_KEY") or ""
        model = os.environ.get("XINGHUI_GPT_IMAGE_MODEL") or os.environ.get("XINGHUI_IMAGE_MODEL") or os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1"
        size = os.environ.get("XINGHUI_GPT_IMAGE_SIZE") or os.environ.get("XINGHUI_IMAGE_SIZE", "1024x1536")
        return base_url, api_key, model, size
    # Gemini route must be configured explicitly. Do not fall back to the legacy
    # XINGHUI_IMAGE_* GPT web provider, otherwise ordinary photos would still hit GPT.
    base_url = os.environ.get("XINGHUI_GEMINI_IMAGE_BASE_URL") or os.environ.get("GEMINI_IMAGE_BASE_URL") or ""
    api_key = os.environ.get("XINGHUI_GEMINI_IMAGE_API_KEY") or os.environ.get("GEMINI_IMAGE_API_KEY") or ""
    model = os.environ.get("XINGHUI_GEMINI_IMAGE_MODEL") or os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.0-flash-preview-image-generation"
    size = os.environ.get("XINGHUI_GEMINI_IMAGE_SIZE") or os.environ.get("GEMINI_IMAGE_SIZE") or "1024x1536"
    return base_url, api_key, model, size


def generate_image(prompt: str, *, out_dir: str | Path, prefix: str = "xinghui_share", reference_images: Optional[list[str]] = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    provider = os.environ.get("XINGHUI_IMAGE_PROVIDER", "mock").strip().lower()
    out_path = out_dir / f"{prefix}_{ts}_{int(time.time()*1000)%100000}.png"
    if provider in {"", "mock", "dryrun"}:
        return _write_mock_image(out_path)
    if provider in {"auto", "route", "routed"}:
        use_gpt = _image_prompt_needs_gpt(prompt, reference_images)
        base_url, api_key, model, size = _image_provider_config(provider, use_gpt=use_gpt)
        if not base_url or not api_key:
            route = "GPT" if use_gpt else "Gemini"
            raise RuntimeError(f"{route} image provider configured but route base_url/api_key missing")
        routed_refs = reference_images if use_gpt else None
        return _http_image_generation(prompt, out_path, base_url=base_url, api_key=api_key, model=model, size=size, reference_images=routed_refs)
    if provider in {"gemini", "google"}:
        base_url, api_key, model, size = _image_provider_config(provider, use_gpt=False)
        if not base_url or not api_key:
            raise RuntimeError("Gemini image provider configured but XINGHUI_GEMINI_IMAGE_BASE_URL/API_KEY missing")
        return _http_image_generation(prompt, out_path, base_url=base_url, api_key=api_key, model=model, size=size, reference_images=None)
    if provider in {"openai", "octopus", "http", "gpt", "gpt-web"}:
        base_url, api_key, model, size = _image_provider_config(provider, use_gpt=True)
        if not base_url or not api_key:
            raise RuntimeError("image provider configured but XINGHUI_IMAGE_BASE_URL/API_KEY missing")
        return _http_image_generation(prompt, out_path, base_url=base_url, api_key=api_key, model=model, size=size, reference_images=reference_images)
    raise RuntimeError(f"unsupported XINGHUI_IMAGE_PROVIDER={provider!r}")


def image_share_enabled() -> bool:
    """Return True only when automatic WeChat image sharing is explicitly enabled.

    Current policy: the previous automatic image-sharing system is retired while
    the new design is rebuilt. Keep the lower-level provider code (especially the
    private imyai route) available for manual/backup calls, but do not let private
    or group WeChat message handling trigger generation automatically unless a
    new explicit opt-in flag is set for controlled testing.
    """
    auto_flag = os.environ.get("XINGHUI_WECHAT_IMAGE_AUTO_ENABLED", "").strip().lower()
    if auto_flag not in {"1", "true", "yes", "on"}:
        return False
    flag = os.environ.get("XINGHUI_IMAGE_SHARE_ENABLED", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    provider = os.environ.get("XINGHUI_IMAGE_PROVIDER", "").strip().lower()
    return provider not in {"", "mock", "dryrun"}


def generate_for_wechat(user_text: str, assistant_text: str = "", *, out_dir: str | Path, proactive: bool = False, agent: str = "xinghui") -> Optional[Path]:
    if not image_share_enabled():
        return None
    req = make_request(user_text, assistant_text, proactive=proactive, agent=agent)
    if not req.should_generate:
        return None
    return generate_image(req.prompt, out_dir=out_dir, prefix=f"xinghui_{req.style}", reference_images=req.reference_images)
