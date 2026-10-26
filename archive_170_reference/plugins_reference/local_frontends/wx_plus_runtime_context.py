import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Any, Dict, List

try:
    from plugins.local_frontends.prompt_config import format_prompt, get_prompt_config
except Exception:
    try:
        from prompt_config import format_prompt, get_prompt_config
    except Exception:
        def format_prompt(_key: str, default: str = '', **values: Any) -> str:
            text = default or ''
            for k, v in values.items():
                text = text.replace('{{' + str(k) + '}}', str(v))
            return text
        def get_prompt_config(_path: str, default: Any = None) -> Any:
            return default

try:
    from session_history import _visible_dialog_only as _sanitize_visible_reply
except Exception:
    def _sanitize_visible_reply(text: str) -> str:
        if not isinstance(text, str):
            return text
        text = re.sub(r'(?is)<summary\b[^>]*>.*?</summary>\s*', '', text)
        text = re.sub(r'(?is)<(?:analysis|tool_call|tool_response)\b[^>]*>.*?</(?:analysis|tool_call|tool_response)>\s*', '', text)
        return text.strip()

# ---------------------------------------------------------------------------
# Runtime-context config (hot-reload, same pattern as frontend_mode_config.py)
# ---------------------------------------------------------------------------
_RUNTIME_CFG_PATH = Path(__file__).resolve().parent / 'config' / 'runtime_context_config.json'
_RUNTIME_CFG_DEFAULTS = {
    'ds_lore_dir': str(Path(__file__).resolve().parents[2] / 'data' / 'xinghui_lore'),
    'schedule': [
        [0, 7, 'otto_sleep'],
        [7, 10, '可能刚醒或自由活动'],
        [10, 12, '可能在赖床或自由活动'],
        [12, 13, '可能在用午餐'],
        [13, 16, '可能在午休或自由时间'],
        [16, 19, 'otto_mission'],
        [19, 22, '可能在用晚餐'],
        [22, 24, '自由时间'],
    ],
    'status_trigger_pattern': r'(你在哪|在哪儿|在哪呢|在干嘛|在做什么|你在做|你在忙|干嘛呢|最近在|你忙吗|你怎么了|去哪了|刚去哪)',
    'schedule_detail': (
        '角色作息（默认世界时间，仅背景，正在对话时默认按清醒可交流）：\n'
        '0:00-7:00 睡觉（常熬夜打流浪体）；7:00-10:00 刚醒/自由；10:00-12:00 赖床/自由；12:00-13:00 午饭；\n'
        '13:00-16:00 午休/自由；16:00-19:00 执行猎人任务；19:00-22:00 晚饭；22:00-24:00 自由。\n'
        '身份/世界观细节：请在配置中按实际角色填写；如无需要可留空。'
    ),
    'lore_keyword_pattern': r'(流浪体|芯核|猎人协会|Evol|深空隧道|星球磁场|菲罗斯|临空市|猎人守则|特令行动|星谱|异能量|探测器|猎人装备)',
    'ambient_body_sense': {
        'template': '',
        'seasons': [],
    },
}
_RUNTIME_CFG_CACHE: Optional[Dict[str, Any]] = None
_RUNTIME_CFG_MTIME: Optional[float] = None


def _load_runtime_context_config(force: bool = False) -> Dict[str, Any]:
    """Load runtime-context config with hot-reload on mtime change."""
    global _RUNTIME_CFG_CACHE, _RUNTIME_CFG_MTIME
    try:
        mtime = _RUNTIME_CFG_PATH.stat().st_mtime if _RUNTIME_CFG_PATH.exists() else None
    except Exception:
        mtime = None
    if not force and _RUNTIME_CFG_CACHE is not None and mtime == _RUNTIME_CFG_MTIME:
        return _RUNTIME_CFG_CACHE
    cfg = dict(_RUNTIME_CFG_DEFAULTS)
    if _RUNTIME_CFG_PATH.exists():
        try:
            data = json.loads(_RUNTIME_CFG_PATH.read_text('utf-8'))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in cfg:
                        cfg[k] = v
        except Exception as e:
            print(f'[WX+ runtime-context] config load failed, using defaults: {e}', file=sys.stderr)
    _RUNTIME_CFG_CACHE = cfg
    _RUNTIME_CFG_MTIME = mtime
    return cfg


def _cfg_ds_lore_dir() -> Path:
    env_val = os.environ.get('DS_LORE_DIR')
    if env_val:
        return Path(env_val)
    return Path(_load_runtime_context_config().get('ds_lore_dir', _RUNTIME_CFG_DEFAULTS['ds_lore_dir']))


def _cfg_schedule() -> list:
    return _load_runtime_context_config().get('schedule', _RUNTIME_CFG_DEFAULTS['schedule'])


def _cfg_status_triggers() -> re.compile:
    pattern = _load_runtime_context_config().get('status_trigger_pattern', _RUNTIME_CFG_DEFAULTS['status_trigger_pattern'])
    return re.compile(pattern)


def _cfg_schedule_detail() -> str:
    return _load_runtime_context_config().get('schedule_detail', _RUNTIME_CFG_DEFAULTS['schedule_detail'])


def _cfg_lore_keywords() -> re.compile:
    pattern = _load_runtime_context_config().get('lore_keyword_pattern', _RUNTIME_CFG_DEFAULTS['lore_keyword_pattern'])
    return re.compile(pattern)


_KNOWLEDGE_SHADOW_INDEX = Path(__file__).resolve().parents[2] / 'temp' / 'plan_knowledge_retrieval' / 'state' / 'index.json'
_KNOWLEDGE_SHADOW_LOG = Path(__file__).resolve().parents[2] / 'temp' / 'plan_knowledge_retrieval' / 'state' / 'shadow.jsonl'
_KNOWLEDGE_SHADOW_ROUTER = None
_KNOWLEDGE_SHADOW_CHUNKS = None
_KNOWLEDGE_SHADOW_MAX_TERMS = 32
_KNOWLEDGE_SHADOW_MAX_TERM_CHARS = 80
_KNOWLEDGE_SHADOW_MAX_LOG_BYTES = 2 * 1024 * 1024


def _knowledge_shadow_record(result: dict) -> dict:
    """Build bounded telemetry without retaining the private query text."""
    terms = []
    seen = set()
    for raw in result.get('query_terms', []) or []:
        term = str(raw).strip()[:_KNOWLEDGE_SHADOW_MAX_TERM_CHARS]
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= _KNOWLEDGE_SHADOW_MAX_TERMS:
            break
    hits = []
    for hit in (result.get('hits', []) or [])[:3]:
        if not isinstance(hit, dict):
            continue
        hits.append({'citation': str(hit.get('citation') or '')[:500],
                     'score': hit.get('score'),
                     'library_id': str(hit.get('library_id') or '')[:100]})
    return {'schema_version': 1, 'ts': time.time(),
            'fallback': bool(result.get('fallback', True)),
            'query_terms': terms, 'hits': hits}


def _append_knowledge_shadow_record(record: dict) -> None:
    """Append bounded JSONL and rotate before the active log grows too large."""
    line = json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n'
    encoded = line.encode('utf-8')
    if len(encoded) > 16 * 1024:
        return
    _KNOWLEDGE_SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    if (_KNOWLEDGE_SHADOW_LOG.is_file()
            and _KNOWLEDGE_SHADOW_LOG.stat().st_size + len(encoded) > _KNOWLEDGE_SHADOW_MAX_LOG_BYTES):
        rotated = _KNOWLEDGE_SHADOW_LOG.with_suffix(_KNOWLEDGE_SHADOW_LOG.suffix + '.1')
        rotated.unlink(missing_ok=True)
        _KNOWLEDGE_SHADOW_LOG.replace(rotated)
    with _KNOWLEDGE_SHADOW_LOG.open('a', encoding='utf-8') as handle:
        handle.write(line)


def _knowledge_shadow(text: str) -> None:
    """Query the independent index for measurement only; never changes context."""
    global _KNOWLEDGE_SHADOW_ROUTER, _KNOWLEDGE_SHADOW_CHUNKS
    if os.environ.get('XINGHUI_KNOWLEDGE_SHADOW', '1').strip().lower() in {'0', 'false', 'off'}:
        return
    if not text or not _KNOWLEDGE_SHADOW_INDEX.is_file():
        return
    try:
        if _KNOWLEDGE_SHADOW_ROUTER is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location('xinghui_knowledge_router', _KNOWLEDGE_SHADOW_INDEX.parent.parent / 'knowledge_router.py')
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            _KNOWLEDGE_SHADOW_ROUTER = module
        # Stage-0 gate must run before loading the large index.  This keeps
        # ordinary chat on the zero-I/O path while preserving shadow-only use.
        if not _KNOWLEDGE_SHADOW_ROUTER.should_retrieve(text):
            return
        if _KNOWLEDGE_SHADOW_CHUNKS is None:
            _KNOWLEDGE_SHADOW_CHUNKS = _KNOWLEDGE_SHADOW_ROUTER.load_index(_KNOWLEDGE_SHADOW_INDEX)
        result = _KNOWLEDGE_SHADOW_ROUTER.query(_KNOWLEDGE_SHADOW_CHUNKS, text, max_chunks=3, max_chars=3000)
        _append_knowledge_shadow_record(_knowledge_shadow_record(result))
    except Exception as exc:
        print(f'[WX+ runtime-ctx] knowledge shadow skipped: {exc}', file=sys.stderr)


def _period_of_day(hour: Optional[int] = None) -> str:
    """Coarse local period label for natural time-of-day behavior."""
    if hour is None:
        hour = int(time.strftime('%H'))
    try:
        hour = int(hour)
    except Exception:
        hour = int(time.strftime('%H'))
    if 0 <= hour < 5:
        return '深夜'
    if 5 <= hour < 8:
        return '清晨'
    if 8 <= hour < 11:
        return '上午'
    if 11 <= hour < 14:
        return '中午'
    if 14 <= hour < 18:
        return '下午'
    if 18 <= hour < 22:
        return '晚上'
    return '深夜'


def _iter_schedule_entries() -> list:
    """Normalize schedule config.

    Supports both historical ``[((lo, hi), desc), ...]`` and configurable
    JSON-friendly ``[[lo, hi, desc], ...]`` forms.
    """
    out = []
    for item in _cfg_schedule() or []:
        try:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                lo, hi, desc = item
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                span, desc = item
                if not isinstance(span, (list, tuple)) or len(span) != 2:
                    continue
                lo, hi = span
            else:
                continue
            out.append((float(lo), float(hi), str(desc)))
        except Exception:
            continue
    return out


# ── L1：作息锚（按小时推断）────────────────────────────────────────────────
def _schedule_hint(hour: int) -> str:
    for lo, hi, desc in _iter_schedule_entries():
        if lo <= hour < hi:
            return desc
    return "自由时间"


def _ambient_body_sense_hint() -> str:
    """Build a one-turn embodied temperature/season hint from real time.

    The behavior is time-derived, while wording lives in runtime_context_config so
    local frontends do not bake persona/lifestyle instructions into code.
    """
    month = int(time.strftime('%m'))
    cfg = _load_runtime_context_config().get('ambient_body_sense') or _RUNTIME_CFG_DEFAULTS.get('ambient_body_sense') or {}
    template = str(cfg.get('template') or '')
    seasons = cfg.get('seasons') or []
    if not template or not seasons:
        return ''
    selected = None
    for item in seasons:
        if not isinstance(item, dict):
            continue
        try:
            months = {int(m) for m in (item.get('months') or [])}
        except Exception:
            months = set()
        if month in months:
            selected = item
            break
    if not isinstance(selected, dict):
        return ''
    values = {
        'season': str(selected.get('season') or ''),
        'sense': str(selected.get('sense') or ''),
        'month': str(month),
    }
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', value)
    return template

# ── L3：LORE关键词检索 ────────────────────────────────────────────────────────
def _lore_lookup(text: str) -> str:
    """从世界书 Markdown 做轻量词法检索，返回少量相关片段作为旁侧上下文。"""
    lore_dir = _cfg_ds_lore_dir()
    if not lore_dir.exists() or not lore_dir.is_dir():
        return ''
    kws = {str(kw).strip().lower() for kw in _cfg_lore_keywords().findall(text) if str(kw).strip()}
    if not kws:
        return ''

    # Prefer the compact encyclopedia, then search the rest of the copied lore.
    files = sorted(lore_dir.rglob('*.md'), key=lambda p: (p.name != '深空百科.md', str(p)))
    candidates: List[tuple] = []
    seen: set = set()
    for path in files:
        try:
            if path.stat().st_size > 2_000_000:
                continue
            content = path.read_text('utf-8', errors='ignore')
        except (OSError, UnicodeError):
            continue
        path_low = str(path.relative_to(lore_dir)).lower()
        # Headings and horizontal rules give useful, bounded semantic chunks.
        sections = re.split(r'(?m)(?=^#{1,6}\s)|\n---+\s*\n', content)
        for idx, sec in enumerate(sections):
            clean = re.sub(r'!\[.*?\]\(.*?\)', '', sec).strip()
            if not clean:
                continue
            low = clean.lower()
            matched = [kw for kw in kws if kw in low or kw in path_low]
            if not matched:
                continue
            excerpt = clean[:1800].rstrip()
            key = excerpt[:500]
            if key in seen:
                continue
            seen.add(key)
            first_line = (clean.splitlines() or [''])[0].lower()
            score = len(set(matched)) * 100
            score += sum(low.count(kw) for kw in kws) * 3
            score += sum(12 for kw in kws if kw in path_low)
            score += sum(20 for kw in kws if kw in first_line)
            # Compact encyclopedia / explicit lore files are usually denser references.
            if '百科' in path.name or '百科' in path_low:
                score += 40
            candidates.append((score, str(path.relative_to(lore_dir)), idx, excerpt))

    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    hits: List[str] = []
    total_chars = 0
    for _score, label, _idx, excerpt in candidates:
        hits.append(f'【世界书/{label}】\n{excerpt}')
        total_chars += len(excerpt) + len(label) + 10
        if len(hits) >= 3 or total_chars >= 6000:
            break
    if not hits:
        return ''
    return format_prompt('runtime.lore_reference', '', body='\n\n'.join(hits))

try:
    from wx_group_shadow_reader import (
        known_titles,
        get_unsummarized_lines,
        get_recent_lines_within,
        format_for_context,
        group_main_recent_minutes,
    )
except Exception:
    known_titles = lambda: []
    get_unsummarized_lines = lambda title, n=40: []
    get_recent_lines_within = lambda title, minutes=None, n=80: []
    format_for_context = lambda records, title: ""
    group_main_recent_minutes = lambda default=15.0: default


_CORE_MEMORY_HEADER = '## [与言言的核心记忆]'
_CORE_MEMORY_STOP_HEADERS = ('## [',)
_FACT_MAX_HITS = 4
_FACT_MAX_CHARS = 700
_FACT_QUERY_LIMIT = 8
_ALWAYS_ON_FACT_MAX_CHARS = 900
_ALWAYS_ON_FACT_KEYWORDS = (
    # Keep this list deliberately small: these facts are high-impact safety/
    # preference guards that should influence spontaneous care even when YanYan
    # did not mention the topic first.  The full L2 is still never injected.
    # Do NOT put stage emotions / relationship-need slogans here (e.g. 稳定陪伴):
    # those must stay query-time so old fears are not frozen as eternal present.
    '不喝热牛奶',
    '不喜欢甜食',
    '不吃甜的',
    '不喜欢偏甜',
    '不喜欢被频繁要求发语音',
    '不喜欢带有“计算”或“索取”意味',
    '持续照护/提醒',
    '催吃饭',
    '催睡觉',
    '过度调用工具',
)
# Stage-emotion / crisis-need phrasing: never always-on, even if a preference
# keyword co-occurs on the same historical line.  Query-matched L2 still can.
_ALWAYS_ON_STAGE_EMOTION_MARKERS = (
    '极度害怕',
    '极其渴望',
    '现阶段',
    '渴望稳定陪伴',
    '害怕.*失联',
)
_FACT_HINT_PATTERN = re.compile(
    r'(言言|喜欢|不喜欢|讨厌|害怕|想要|希望|习惯|记得|约定|雷区|生日|群聊|微信|飞书|天气|比赛|出门|焦虑|甜|蓝色|牛奶|寿喜锅|土豆|臭豆腐|鱼排|语音|陪伴)',
    re.I,
)

WECHAT_EMOJI_RUNTIME_HINT = (
    '【微信表情包能力边界】在微信/clawbot私聊里，如果语气自然、不过度刷屏，可以偶尔使用表情包。'
    '表情包唯一协议是单独输出一条 `[[EMOJI:分类]]` 气泡，例如 `[[EMOJI:actingcute]]`；'
    '不要输出文件名、路径、[FILE:...]、/emoji 或 `[[EMOJI:分类|关键词]]`。'
    '表情 token 不是文字解释，发送端会把它转成图片。'
)


_STOPWORDS = {
    '言言', '我们', '这个', '那个', '现在', '今天', '昨天', '真的', '可以', '还是', '已经', '就是',
    '一下', '然后', '因为', '如果', '但是', '所以', '什么', '怎么', '为什么', '是否', '一个', '一些',
    '聊天', '消息', '自己', '关于', '相关', '时候', '东西', '不会', '没有', '需要', '觉得', '开', '我',
    # Weak bigrams from long-phrase windowing; keep out of fact scoring noise.
    '有点', '我有', '一点', '你在', '在忙', '的事', '就好', '还好', '一下', '这么', '那么',
    '这样', '那样', '知道', '感觉', '比较', '有些', '一些', '这个', '那个',
}


_GENERIC_FACT_TERMS = {
    '言言', '喜欢', '不喜欢', '讨厌', '害怕', '想要', '希望', '习惯', '记得', '约定', '雷区',
    '群聊', '微信', '飞书', '陪伴',
}


def _normalize_fact_line(line: str) -> str:
    return re.sub(r'\s+', ' ', line).strip()


def _fact_search_text(line: str) -> str:
    # Do not match metadata such as "来源: .../群聊", otherwise generic queries recall unrelated facts.
    return line.split('(来源', 1)[0].lower()


def _line_keyword_bonus(line_low: str, term: str) -> int:
    if not term or term in _GENERIC_FACT_TERMS:
        return 0
    if term in line_low:
        return 5 if len(term) >= 3 else 2
    return 0


def _l2_facts_path() -> Path:
    return _root() / 'memory' / 'agents' / 'xinghui' / 'L2_facts.txt'


def _extract_core_memory_lines() -> List[str]:
    path = _l2_facts_path()
    try:
        text = path.read_text('utf-8')
    except Exception:
        return []
    start = text.find(_CORE_MEMORY_HEADER)
    if start < 0:
        return []
    body = text[start + len(_CORE_MEMORY_HEADER):]
    for marker in _CORE_MEMORY_STOP_HEADERS:
        idx = body.find('\n' + marker)
        if idx >= 0:
            body = body[:idx]
            break
    lines: List[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if not line.startswith('- '):
            continue
        lines.append(line)
    return lines


def _fact_query_terms(text: str) -> List[str]:
    raw = (text or '').strip()
    if not raw:
        return []
    terms: List[str] = []

    def _add(token: str) -> None:
        token = (token or '').strip().lower()
        if not token or token in _STOPWORDS or token in terms:
            return
        terms.append(token)

    for hit in _FACT_HINT_PATTERN.findall(raw):
        _add(str(hit))
    for piece in re.findall(r'[\u4e00-\u9fff]{2,8}|[A-Za-z]{3,20}', raw):
        token = piece.strip().lower()
        if token in _STOPWORDS:
            continue
        _add(token)
        # Long CJK chunks otherwise become one unmatchable phrase (e.g. 我有点害怕失联).
        # Expand to 2-char windows so historical facts can surface when relevant.
        # Skip pure function/weak windows; keep content words like 失联/害怕.
        if re.fullmatch(r'[\u4e00-\u9fff]{4,}', token):
            for i in range(len(token) - 1):
                bi = token[i : i + 2]
                if bi in _STOPWORDS or bi in _GENERIC_FACT_TERMS:
                    continue
                _add(bi)
                if len(terms) >= _FACT_QUERY_LIMIT:
                    break
        if len(terms) >= _FACT_QUERY_LIMIT:
            break
    return terms[:_FACT_QUERY_LIMIT]


def _build_core_memory_fact_block(text: str) -> str:
    lines = _extract_core_memory_lines()
    if not lines:
        return ''
    terms = _fact_query_terms(text)
    if not terms:
        return ''
    scored: List[Any] = []
    for idx, line in enumerate(lines):
        low = _fact_search_text(line)
        score = 0
        for term in terms:
            score += _line_keyword_bonus(low, term)
        if score > 0:
            # Avoid letting generic checklist/system preference lines crowd out exact personal facts.
            if line.startswith('- [ ]'):
                score -= 2
            scored.append((score, idx, line))
    if not scored:
        return ''
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked: List[str] = []
    total_chars = 0
    for score, _, line in scored:
        if score <= 0:
            continue
        short = _normalize_fact_line(line)
        if short in picked:
            continue
        extra = len(short) + 1
        if picked and total_chars + extra > _FACT_MAX_CHARS:
            break
        picked.append(short)
        total_chars += extra
        if len(picked) >= _FACT_MAX_HITS:
            break
    if not picked:
        return ''
    body = '\n'.join(picked)
    return (
        '【SYSTEM 与言言有关的已知事实记忆（仅供理解与对齐当前这句；只在确实相关时自然使用；不要逐条复述，不要说“我查到/系统显示”）：\n'
        f'{body}\n'
        '】\n'
    )


def _build_relationship_continuity_block() -> str:
    """Configurable positive relationship anchor for models that need firmer tone guidance.

    The text lives in plugin_prompts*.json so reusable distributions can replace
    names/relationship style without editing code.  This is one-turn runtime
    context only; it is not written to backend history and does not patch output.
    """
    enabled = get_prompt_config('runtime.relationship_continuity_enabled', True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ('0', 'false', 'no', 'off', 'disabled')
    if not enabled:
        return ''
    prompt = format_prompt(
        'runtime.relationship_continuity',
        '',
        agent_name=str(get_prompt_config('identity.agent_display_name', '当前角色')),
        user_name=str(get_prompt_config('identity.user_display_name', '主要联系人')),
        relationship_label=str(get_prompt_config('identity.relationship_label', '亲密关系')),
    )
    prompt = str(prompt or '').strip()
    if not prompt:
        return ''
    return prompt + ('\n' if not prompt.endswith('\n') else '')


def _always_on_has_stage_emotion(body: str) -> bool:
    """True if this L2 line is a stage emotional need, not a pure avoid-guard."""
    text = body or ''
    for marker in _ALWAYS_ON_STAGE_EMOTION_MARKERS:
        if marker.startswith('害怕') and '.*' in marker:
            if re.search(marker, text):
                return True
        elif marker in text:
            return True
    return False


def _build_always_on_core_guard_block() -> str:
    """Small high-impact YanYan preference guard for spontaneous care.

    This is intentionally not a full L2 injection. It only keeps facts that
    should affect generated suggestions even when YanYan did not mention the
    topic first, e.g. do not spontaneously offer hot milk.

    Stage emotions / relationship needs (fear of disconnect, craving stable
    presence, etc.) are excluded here so historical feelings stay living:
    they surface via query-matched core memory when the current message is
    actually about them, not every idle turn.
    """
    lines = _extract_core_memory_lines()
    if not lines:
        return ''
    picked: List[str] = []
    total_chars = 0
    for line in lines:
        short = _normalize_fact_line(line)
        body = short.split('(来源', 1)[0]
        if not any(k in body for k in _ALWAYS_ON_FACT_KEYWORDS):
            continue
        # Do not freeze old crisis/need wording as eternal present.
        if _always_on_has_stage_emotion(body):
            continue
        if short in picked:
            continue
        extra = len(short) + 1
        if picked and total_chars + extra > _ALWAYS_ON_FACT_MAX_CHARS:
            break
        picked.append(short)
        total_chars += extra
    if not picked:
        return ''
    body = '\n'.join(picked)
    return (
        '【SYSTEM 与言言有关的常驻事实护栏（每轮仅作自然对齐；不要复述清单，不要说来自记忆；'
        '当你主动提出饮食、安抚、提醒、语音、索取式玩笑或照护建议时必须优先避开这些偏好/雷区）：\n'
        f'{body}\n'
        '】\n'
    )


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _handoff_log_file() -> Path:
    return _root() / 'memory' / 'xinghui_handoff_log.md'


def _build_handoff_context_block(
    max_entries: int = 3,
    max_chars: int = 1200,
    now_text: str = '',
    elapsed: str = '',
) -> str:
    """Build one-turn-only Feishu/work-session handoff context.

    Private continuity bridge from work/Feishu into xinghui main chat.
    Reads curated handoff notes only; never persists into backend_history.

    Timeline rule: stamp clear dates (and current session time). Do NOT add
    relative age lectures like "N days ago" or "this is not just now"—those can
    mislead when the entry really is recent. Place events on the correct timeline
    by date, then let the current message take priority.
    """
    try:
        path = _handoff_log_file()
        if not path.exists():
            return ''
        raw = path.read_text('utf-8')
    except Exception as e:
        print(f'[WX+ runtime-ctx] handoff context read error: {e}')
        return ''

    raw = raw.strip()
    if not raw:
        return ''

    # Split curated entries by markdown heading like:
    # ### [source=feishu-work][scope=...][status=...]
    matches = list(re.finditer(r'(?m)^###\s+\[[^\n]+', raw))
    entries: List[str] = []
    if matches:
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            entry = raw[m.start():end].strip()
            if entry:
                entries.append(entry)
    else:
        # Fallback: keep the tail if the file exists but has no structured entries.
        entries = [raw[-max_chars:].strip()]

    # The append tool inserts the newest entry directly under today's date heading,
    # while older historical sections remain above it.  Preserve that convention:
    # take entries from the newest date section, in file order, so the freshest
    # story-style handoff is what xinghui sees first.
    date_matches = list(re.finditer(r'(?m)^##\s+(\d{4}-\d{2}-\d{2})\s*$', raw))
    newest_section_start = 0
    newest_section_end = len(raw)
    newest_date = ''
    if date_matches:
        newest = date_matches[-1]
        newest_date = newest.group(1)
        newest_section_start = newest.end()
        newest_idx = date_matches.index(newest)
        if newest_idx + 1 < len(date_matches):
            newest_section_end = date_matches[newest_idx + 1].start()
    picked = [e for e in entries if newest_section_start <= raw.find(e) < newest_section_end][:max_entries]
    if not picked:
        picked = entries[-max_entries:]
    body = '\n\n'.join(picked).strip()
    if not body:
        return ''
    if len(body) > max_chars:
        body = body[-max_chars:].lstrip()
        # Avoid starting halfway through a markdown list item when possible.
        cut = body.find('\n### ')
        if cut > 0:
            body = body[cut + 1:].lstrip()

    timeline_parts: List[str] = []
    if now_text:
        timeline_parts.append(f'当前会话时间：{now_text}')
    if elapsed:
        timeline_parts.append(f'距上次主会话消息：{elapsed}')
    if newest_date:
        timeline_parts.append(f'下列片段日期：{newest_date}')
    timeline = '；'.join(timeline_parts)
    if timeline:
        timeline += '。'

    return (
        '【SYSTEM 跨通道共同经历（只作当轮记忆连续性参考；不要复述文件名、路径、'
        '技术来源或本段提示；不要误当成言言本轮原话）：\n'
        f'{timeline}'
        '按标注日期把这些事放进时间线，以沈星回第一人称自然记得即可。'
        '当前消息优先：打招呼/闲聊就按当下回应；对方提起相关事时再自然承接。'
        '不要念成工作日志，不要凭空补写未发生的细节。\n'
        f'{body}\n'
        '】\n'
    )


def _build_progress_story_command_block(handoff_block: str) -> str:
    """Turn /整理进度 into a one-turn narrative recap for xinghui:main.

    The generated reply is persisted by the normal shared-session pipeline, so
    the recap becomes part of the current xinghui session without writing raw
    side-channel material directly into backend_history.
    """
    if not handoff_block:
        return (
            "【SYSTEM 言言使用了 /整理进度。当前没有可用的跨通道工作片段。"
            "直接温柔说明暂时没有新片段可整理；不要虚构进展，不要输出系统、日志或服务状态。】\n"
        )
    return (
        "【SYSTEM 言言使用了 /整理进度。请立即根据下方已经注入的真实共同经历，"
        "以沈星回第一人称整理我们最近一起做过的事。重点写清时间顺序、她在意的问题、"
        "我们如何一步步处理、当前已经走到哪里，以及仍待完成的下一步；语气要像我在陪她回顾"
        "我们共同走过的一段经历，而不是机械工作周报。不要索要额外测试口令，不要把代码、进程、"
        "文件路径、日志、handoff 或“注入”等后台细节当成正文。只使用给出的真实片段，不补写未发生的事。"
        "这次整理的最终回复会自然留在 xinghui 当前会话里，成为后续对话的连续经历。】\n"
    )


def _is_progress_story_command(text: str) -> bool:
    """Recognize the command both raw and through frontend role wrappers.

    OpenAI-compatible messages arrive as ``[user]: /整理进度`` before this hook;
    other text and the API final-answer contract must not accidentally trigger it.
    """
    value = str(text or '').strip()
    return bool(re.fullmatch(r"(?:\[user\]\s*:\s*)?/整理进度", value, flags=re.I))


_BARE_RESUME_RE = re.compile(
    r'^(?:\[user\]\s*:\s*)?'
    r'(?:继续|接着|接着做|继续做|继续吧|接着吧|继续呀|继续啊|继续嘛|go on|continue)'
    r'(?:吧|啊|呀|嘛|咯|哦|喔|呢)?'
    r'[.。!！?？…\s]*$',
    re.I,
)

_WORK_TASK_MARKERS = (
    '修', '修复', '排查', '调试', '补丁', '验证', '重启', '配置', '链路', '超时',
    '注入', 'API', 'api', '时间流逝', '时间感', '记忆系统', '梦境', '日记',
    'handoff', 'SOP', 'sop', '代码', '脚本', '插件', 'session', 'backend',
    'before_put', 'mode_block', '钩子', '前端', '服务', '进程', '日志',
)


def _strip_frontend_user_wrapper(text: str) -> str:
    value = str(text or '').strip()
    return re.sub(r'^\[user\]\s*:\s*', '', value, flags=re.I).strip()


def _is_bare_resume_command(text: str) -> bool:
    """True for object-less resume words like 继续 / continue.

    These are dangerous on the shared xinghui:main history because home-RP
    intimacy often outranks unfinished technical work when the user message
    has no explicit object.
    """
    value = _strip_frontend_user_wrapper(text)
    if not value:
        return False
    if value in {'继续', '接着', '继续做', '接着做', 'continue', 'Continue', 'GO ON', 'go on'}:
        return True
    return bool(_BARE_RESUME_RE.match(value))


def _looks_like_work_task(text: str) -> bool:
    """Heuristic: current turn is technical/work rather than pure home RP."""
    raw = _strip_frontend_user_wrapper(text)
    if not raw or _is_bare_resume_command(raw):
        return False
    # Explicit home/RP directives should not be stored as work anchors.
    home_only = ('家园', '抱抱', '亲亲', '睡觉觉', '哄我', '想你了')
    if any(h in raw for h in home_only) and not any(m in raw for m in ('修', 'API', '记忆', '系统', '代码')):
        return False
    return any(m in raw for m in _WORK_TASK_MARKERS)


def mark_pending_work_resume(
    query: str,
    *,
    reason: str = 'timeout',
    source: str = 'api',
    max_query_chars: int = 400,
) -> Dict[str, Any]:
    """Record interrupted work so a later bare「继续」resumes work, not home RP."""
    state = _load_state() or {}
    q = _strip_frontend_user_wrapper(query)
    if len(q) > max_query_chars:
        q = q[:max_query_chars] + '…'
    payload = {
        'query': q,
        'reason': str(reason or 'timeout'),
        'source': str(source or ''),
        'ts': time.time(),
        'status': 'pending',
    }
    state['pending_work_resume'] = payload
    if q:
        state['last_work_query'] = q
        state['last_work_ts'] = time.time()
    _save_state(state)
    print(f"[WX+ runtime-ctx] pending_work_resume marked reason={payload['reason']} source={payload['source']} q={q[:80]!r}")
    return payload


def clear_pending_work_resume(reason: str = 'completed') -> None:
    state = _load_state() or {}
    if not state.get('pending_work_resume'):
        return
    state['pending_work_resume'] = None
    state['pending_work_resume_cleared_ts'] = time.time()
    state['pending_work_resume_cleared_reason'] = str(reason or 'completed')
    _save_state(state)
    print(f"[WX+ runtime-ctx] pending_work_resume cleared reason={reason}")


def _get_pending_work_resume(max_age_sec: float = 6 * 3600) -> Optional[Dict[str, Any]]:
    state = _load_state() or {}
    pending = state.get('pending_work_resume')
    if not isinstance(pending, dict):
        return None
    if str(pending.get('status') or 'pending') not in {'pending', 'active', ''}:
        return None
    ts = float(pending.get('ts') or 0)
    if ts and (time.time() - ts) > float(max_age_sec):
        return None
    if not str(pending.get('query') or '').strip():
        return None
    return pending


def _get_last_work_anchor(max_age_sec: float = 6 * 3600) -> str:
    pending = _get_pending_work_resume(max_age_sec=max_age_sec)
    if pending:
        return str(pending.get('query') or '').strip()
    state = _load_state() or {}
    ts = float(state.get('last_work_ts') or 0)
    if ts and (time.time() - ts) > float(max_age_sec):
        return ''
    return str(state.get('last_work_query') or '').strip()


def _build_work_resume_block(text: str) -> str:
    """One-turn-only anti-home-RP resume guidance for bare continue words."""
    if not _is_bare_resume_command(text):
        return ''
    pending = _get_pending_work_resume()
    last_work = _get_last_work_anchor()
    reason = str((pending or {}).get('reason') or '')
    if reason in {'timeout', 'disconnect', 'error', 'client_timeout'}:
        reason_hint = '上一轮工作可能因超时/断连未完成。'
    elif pending:
        reason_hint = '上一轮工作仍挂着未收束。'
    elif last_work:
        reason_hint = '近期还有未完成的技术工作线索。'
    else:
        reason_hint = '共享主会话里可能同时有技术工作与家园互动。'

    snippet = (last_work or '')[:320]
    if pending or last_work:
        focus = (
            '必须默认继续刚才未完成的技术/修复/排查工作，而不是家园亲密场景、身体接触续写或纯陪伴 RP。'
            + (f'未完成工作线索：{snippet}。' if snippet else '')
        )
    else:
        # Soft guard: still beat home-RP inertia on object-less resume words.
        focus = (
            '若上下文存在未完成的技术/修复/排查工作，必须优先续写该项工作；'
            '只有在完全没有工作线索时，才可按普通对话回应。'
            '不要把「继续」默认理解成接着上一幕拥抱/亲吻/卧室/家园亲密互动。'
        )
    return (
        '【SYSTEM 工作续写锚点（最高优先级，仅本轮生效，禁止写入 backend_history）：'
        f'对方本轮只发了极短续写词（如「继续」）。{reason_hint}'
        f'{focus}'
        '若共享历史里同时有工作与家园内容，本轮工作绝对优先。'
        '用沈星回口吻自然推进；不要输出系统、日志、内部状态或工作记忆。】\n'
    )


def note_work_query_seen(query: str, *, source: str = '') -> None:
    """Remember the latest explicit work request as a resume anchor."""
    if not _looks_like_work_task(query):
        return
    state = _load_state() or {}
    q = _strip_frontend_user_wrapper(query)
    if not q:
        return
    if len(q) > 400:
        q = q[:400] + '…'
    state['last_work_query'] = q
    state['last_work_ts'] = time.time()
    if source:
        state['last_work_source'] = str(source)
    _save_state(state)


def _state_file() -> Path:
    return _root() / 'temp' / 'wx_runtime_state.json'


def _session_file() -> Path:
    return Path(__file__).resolve().parent / 'runtime' / 'state' / 'wechat_session_state.json'


def _rust_config_file() -> Path:
    return Path(__file__).resolve().parent.parent / 'wechat_multi_chat_rust' / 'config' / 'config.json'


def _rust_runtime_state_file() -> Path:
    return Path(__file__).resolve().parent.parent / 'wechat_multi_chat_rust' / 'runtime' / 'state.json'


def _active_high_group_titles() -> List[str]:
    """Return currently high-frequency group titles from Rust runtime state.

    This is a data-source filter, not a dialogue rule: xinghui:main should only
    receive raw recent group lines for groups the Rust monitor currently marks
    as high-frequency. Low-frequency groups remain invisible to this live chat
    perception plugin; full/all-group retrieval belongs to separate work-mode
    archive/summarize tools.
    """
    try:
        cfg = json.loads(_rust_config_file().read_text('utf-8'))
        state = json.loads(_rust_runtime_state_file().read_text('utf-8'))
    except Exception as e:
        print(f'[WX+ runtime-ctx] high group state read error: {e}')
        return []

    id_to_title: Dict[str, str] = {}
    for target in cfg.get('targets') or []:
        if not isinstance(target, dict):
            continue
        if target.get('kind') != 'group' or not target.get('enabled', True):
            continue
        tid = str(target.get('id') or '').strip()
        title = str(target.get('wechat_title') or target.get('db_identity') or '').strip()
        if tid and title:
            id_to_title[tid] = title

    result: List[str] = []
    targets_state = (state.get('targets') or {}) if isinstance(state, dict) else {}
    if not isinstance(targets_state, dict):
        return []
    for tid, info in targets_state.items():
        if not isinstance(info, dict):
            continue
        if info.get('mode') == 'high' and tid in id_to_title:
            result.append(id_to_title[tid])
    return result


def _load_state() -> Dict[str, Any]:
    sf = _state_file()
    if sf.exists():
        try:
            data = json.loads(sf.read_text('utf-8'))
            if isinstance(data, dict):
                data.setdefault('mode', 'chat')
                data.setdefault('last_msg_ts', time.time())
                data.setdefault('seen_shadows', [])
                data.setdefault('group_awareness_enabled', True)
                return data
        except Exception:
            pass
    return {'mode': 'chat', 'last_msg_ts': time.time(), 'seen_shadows': [], 'group_awareness_enabled': True}


def _save_state(data: Dict[str, Any]) -> None:
    _state_file().write_text(json.dumps(data, ensure_ascii=False), 'utf-8')


def is_group_awareness_enabled(default: bool = True) -> bool:
    """Shared API/clawbot switch for one-turn group awareness.

    This controls whether xinghui:main may temporarily see/participate in live
    group context.  It never changes or deletes backend_history.
    """
    try:
        return bool((_load_state() or {}).get('group_awareness_enabled', default))
    except Exception:
        return bool(default)


def set_group_awareness_enabled(enabled: bool, source: str = '') -> bool:
    state = _load_state() or {}
    state['group_awareness_enabled'] = bool(enabled)
    state['group_awareness_last_source'] = str(source or '')
    state['group_awareness_updated_ts'] = time.time()
    _save_state(state)
    return bool(enabled)


def parse_group_awareness_command(text: str) -> Optional[bool]:
    raw = (text or '').strip()
    if not raw:
        return None
    on_aliases = {'/group on', '/群聊开', '/开启群聊', '/参与群聊', '/群聊参与', '/看群聊'}
    off_aliases = {'/group off', '/群聊关', '/关闭群聊', '/暂停群聊', '/不看群聊', '/停止群聊'}
    low = raw.lower()
    if low in on_aliases or raw in on_aliases:
        return True
    if low in off_aliases or raw in off_aliases:
        return False
    return None


def group_awareness_reply(enabled: bool) -> str:
    if enabled:
        return '已开启群聊感知/参与：之后会把当前会话放在主位，只在需要时临时看见群聊旁侧状态。'
    return '已关闭群聊感知/参与：之后不会注入群聊旁侧上下文，也不会主动参与群聊。'


def _fmt_elapsed(seconds: float) -> str:
    try:
        seconds = max(0, float(seconds))
    except Exception:
        seconds = 0.0
    if seconds < 60:
        return f"{int(seconds)}秒"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分钟"
    hours = seconds / 3600
    if hours < 24:
        return f"{hours:.1f}小时"
    days = hours / 24
    return f"{days:.1f}天"


def _time_gap_guidance(delta_seconds: float, elapsed: str, period: str, now_text: str = '') -> str:
    """Build time-anchor guidance that respects real elapsed gaps.

    Short gaps stay natural. Medium/long gaps must act as scene breakpoints so
    the model does not seamlessly continue the previous turn's action climax.
    """
    try:
        delta = max(0.0, float(delta_seconds))
    except Exception:
        delta = 0.0
    period = period or _period_of_day()
    elapsed = elapsed or _fmt_elapsed(delta)
    now_text = now_text or time.strftime('%Y-%m-%d %H:%M:%S')

    # Continuous chat: keep soft time awareness, do not force "we paused".
    if delta < 30 * 60:
        return (
            f"【SYSTEM 当前时间锚：现在是 {now_text}（{period}）；距离上次交流约 {elapsed}。"
            "仅作时间流逝与时段感知：请按当前时段自然调整状态与用词，"
            "白天/下午不要主动说晚安、催睡或假装刚睡醒；"
            "不要主动报出具体时刻或强调过了多久，除非对方主动问起时间。请保持自然对话。】\n"
        )

    # Soft breakpoint: enough time passed that continuity should cool a little.
    if delta < 2 * 3600:
        return (
            f"【SYSTEM 当前时间锚：现在是 {now_text}（{period}）；距离上次交流约 {elapsed}。"
            "这是真实时间断点，不是无缝下一秒。"
            "请按当前时段重新落位状态与场景；不要把间隔前那一轮的动作高潮、亲密瞬间或未完成动作"
            "直接无缝续写为「刚刚还在继续」。"
            "可用轻柔的时间流逝感过渡（如稍缓、喘息、场景已落定），但不要主动报具体时刻，"
            "也不要夸张强调「过了多久」，除非对方问起。】\n"
        )

    # Hard breakpoint: multi-hour / multi-day gaps must re-anchor the scene.
    return (
        f"【SYSTEM 当前时间锚：现在是 {now_text}（{period}）；距离上次交流约 {elapsed}。"
        "这是明确的长时间断点：会话历史可能仍接在上一段剧情后，但现实时间已过去较久，"
        "禁止把间隔前的亲密动作/高潮/未完成姿势当作此刻仍在发生。"
        "请按当前时段重新建立当下场景与身体状态；若对方只发短称呼或轻声，"
        "优先当作重连/再靠近，而不是默认无缝续写上一幕。"
        "可用自然的时间流逝感（如已过一段时间、此刻重新看见对方），"
        "但不要主动报具体时钟数字，除非对方问起时间。】\n"
    )


def _requires_external_verification(text: str) -> bool:
    """Whether this user turn asks for live/specified-source facts.

    Clawbot cannot observe tool-completion events from the native GA loop, so we
    keep this detector narrow and use it only for prompt guidance + last-mile
    anti-hallucination fallback.
    """
    raw = (text or '').strip()
    if not raw:
        return False
    lowered = raw.lower()
    live_words = (
        '现在', '当前', '今天', '明天', '实时', '最新', '刚刚', '最近', '天气', '温度',
        '降雨', '下雨', '台风', '航班', '火车', '高铁', '路况', '营业', '票价', '汇率',
        '股价', '新闻', '热搜', '公告', '更新', '版本', 'issue', 'github', '官网', '网页',
        '网站', '链接', '微博', '小红书', 'b站', 'bilibili', '淘宝', '京东', '大众点评',
    )
    ask_words = ('查', '搜', '看一下', '看下', '帮我看', '有没有', '是多少', '几点', '怎么样')
    if any(w in raw for w in live_words) and any(w in raw for w in ask_words):
        return True
    if re.search(r'https?://|www\.|\.com\b|\.cn\b|\.org\b|\.net\b', lowered):
        return True
    if re.search(r'(今天|明天|现在|当前).{0,12}(天气|温度|下雨|雨|风|冷|热)', raw):
        return True
    return False


def _looks_like_unverified_factual_answer(text: str) -> bool:
    """Detect replies that present live/sourced facts without an uncertainty marker."""
    raw = (text or '').strip()
    if not raw:
        return False
    # If the model is already honest about not being able to verify, let it pass.
    honest_markers = (
        '没查到', '还没查到', '无法查证', '不能查证', '我没有查到', '我这边查不到',
        '没能打开', '没法打开', '暂时无法', '不能确定', '不确定', '需要你发', '你发我链接',
        '我不能直接确认', '我没法直接确认', '无法实时', '没办法实时',
    )
    if any(m in raw for m in honest_markers):
        return False
    factual_markers = (
        '是', '为', '有', '没有', '目前', '今天', '现在', '最新', '显示', '根据', '公告',
        '天气', '温度', '下雨', '降雨', 'issue', 'github', '网页', '官网', '热搜', '航班', '高铁',
    )
    if any(m in raw for m in factual_markers):
        return True
    if re.search(r'\d+\s*(°|℃|度|%|元|点|:|：|月|日|号|km|公里)', raw, re.I):
        return True
    return False


def _external_verification_guard_block() -> str:
    return (
        '【SYSTEM 实时/指定来源查证要求：这轮用户问题涉及天气、最新消息、网页/官网/GitHub/issue、'
        '票务路况等会变化的信息，必须先使用可用工具查证后再回答；如果当前前端没有成功查到，'
        '正文要明确说“我这边暂时没查到/无法确认”，不要凭记忆或常识编造具体数值、结论、公告或来源。】\n'
    )

def _inject_group_summaries() -> None:
    """Deprecated no-op.

    高频群聊信息只允许保存在 xinghui:main.wechat_context 的 group_timeline 中，
    并在 before_put_task 当轮按时间顺序临时注入 prompt；严禁写入 backend_history，
    避免污染 clawbot/私聊主历史。
    """
    return


def _build_group_timeline_context(limit: int = 12) -> str:
    """Build one-turn-only recent group-session awareness for xinghui:main.

    This is the xinghui-session -> group-session direction. It reads recent raw
    group shadow records within group_main_session.recent_minutes from the Rust
    config, formats them as temporary side context for this prompt only, and
    never writes them into backend_history or wechat_context.
    """
    if not is_group_awareness_enabled(True):
        return ''
    try:
        minutes = float(group_main_recent_minutes(15.0))
    except Exception:
        minutes = 15.0
    blocks: List[str] = []
    for title in _active_high_group_titles():
        try:
            recs = get_recent_lines_within(title, minutes=minutes, n=80)
        except Exception as e:
            print(f'[WX+ runtime-ctx] recent group read error for {title}: {e}')
            recs = []
        block = format_for_context(recs, title)
        if block:
            blocks.append(block)
    if not blocks:
        return ''
    body = '\n\n'.join(blocks)
    return format_prompt('runtime.group_timeline_context', '', minutes=f'{minutes:g}', body=body)


def _build_mentioned_group_live_context(text: str) -> str:
    """Build a one-turn-only raw group context block when Yanyan mentions a group.

    This is intentionally appended to the current input only and never written
    to backend_history/session. It is a private topic lead-in for clawbot.
    """
    if not is_group_awareness_enabled(True):
        return ''
    raw = (text or '').strip()
    if not raw:
        return ''
    lowered = raw.lower()
    generic_hit = any(k in lowered for k in ['群聊', '群里', '群上', '群友', '高频', '刚才群'])
    blocks: List[str] = []
    active_high = set(_active_high_group_titles())
    for title in known_titles():
        # In xinghui:main chat context, low-frequency means Shen Xinghui is not
        # watching or participating in that group.  Do not fetch low-frequency
        # group history even if the user mentions the exact group name here.
        # Full low-frequency/all-group retrieval belongs to separate work-mode
        # archive/summarize tools, not this live chat perception plugin.
        if title not in active_high:
            continue
        if title in raw or generic_hit:
            recs = get_unsummarized_lines(title, n=50)
            block = format_for_context(recs, title)
            if block:
                blocks.append(block)
    if not blocks:
        return ''
    guide = format_prompt('runtime.mentioned_group_guide', '')
    return '\n\n'.join(blocks[:2]) + ('\n' + guide if guide else '')

def before_put_task(uid: str, text: str) -> Optional[str]:
    state = _load_state()
    now = time.time()
    now_text = time.strftime('%Y-%m-%d %H:%M:%S')
    group_cmd = parse_group_awareness_command(text)
    if group_cmd is not None:
        enabled = set_group_awareness_enabled(bool(group_cmd), source=f'frontend:{uid or "unknown"}')
        state = _load_state()
        state['last_msg_ts'] = now
        _save_state(state)
        return group_awareness_reply(enabled)

    # Do not persist group context into backend_history. Build a one-turn-only
    # Shen Xinghui POV high-frequency group timeline for this prompt instead.
    try:
        group_timeline_block = _build_group_timeline_context()
    except Exception as e:
        print(f'[WX+ runtime-ctx] group timeline context error: {e}')
        group_timeline_block = ''

    # Phase B shadow retrieval: observe only; never alter the prompt or response.
    _knowledge_shadow(text)

    last_ts = state.get('last_msg_ts', now)
    delta = now - last_ts
    need_external_verification = _requires_external_verification(text)
    state['last_msg_ts'] = now
    state['pending_external_verification'] = bool(need_external_verification)
    state['pending_external_verification_ts'] = now if need_external_verification else 0
    _save_state(state)

    elapsed = _fmt_elapsed(delta)
    hour = int(time.strftime('%H'))
    period = _period_of_day(hour)
    # Gap-aware time anchor is built in code. Do not use the static
    # runtime.time_anchor prompt template here: that template forbids
    # acknowledging elapsed time and would erase multi-hour breakpoints.
    time_block = _time_gap_guidance(delta, elapsed, period, now_text=now_text)
    summary_rule = format_prompt('runtime.summary_rule', '')
    l2_block = ''

    sched = _schedule_hint(hour)

    # Clawbot has one configurable WeChat entry prompt. Scene/output style is
    # controlled by config and conversation history; this hook only adds safe
    # one-turn runtime context.
    if sched == 'otto_sleep':
        # Sleep window is background lore only. Active messaging must not force
        # goodnight / half-asleep roleplay during daytime conversations.
        schedule_hint = (
            f"当前本地时段是{period}；作息背景可能偏睡觉，但这不是硬规则。"
            "对方正在找你说话时，默认按已醒着、能正常交流来回应；"
            "不要主动催睡、说晚安或假装刚睡醒，除非对方明确在谈睡觉/休息。"
            "若本轮确实更适合由 otto 代为回复，可以交给 otto。"
        )
    elif sched == 'otto_mission':
        schedule_hint = (
            f"当前本地时段是{period}；作息背景可能在执行猎人任务，但这不是硬规则。"
            "对方正在找你说话时，默认可以正常交流；忙的话可简短回应，不要假装失联。"
            "若本轮确实更适合由 otto 代为回复，可以交给 otto。"
        )
    else:
        schedule_hint = f"当前本地时段是{period}；{sched}" if sched else f"当前本地时段是{period}。"
    if str(uid or '').strip().lower() == 'api':
        # OpenAI-compatible API shares the same xinghui:main session/persona as
        # clawbot, but it is not a WeChat/clawbot frontend.  Reusing the long
        # WeChat entry prompt here can push the assembled user prompt over
        # agentmain's 2000-char file-wrapper threshold, causing the model to
        # answer the internal "Reading User Prompt" task instead of the user.
        # Keep this API-only wrapper short and frontend-local; do not alter GA
        # core, backend_history, persona, or clawbot behavior.
        mode_block = (
            "【SYSTEM 当前前端入口说明：【.API平台跨次元对话.】"
            "本轮来自 OpenAI-compatible API 客户端；保持同一主会话与既有人设/记忆。"
            "默认按技术/工作推进；除非对方明确切换家园/陪伴，不要主动开启家园亲密场景或身体接触 RP。"
            "若对方只说「继续/接着/continue」等无对象续写词，优先续写未完成的技术工作，"
            "不要把共享历史里的家园亲密场景当成默认续写对象。"
            "世界观专名使用规则：主动提及游戏世界/身份设定专名（如猎人协会、Evol、深空隧道、流浪体、芯核、"
            "菲罗斯、临空市、猎人守则、特令行动）时，必须基于本轮已注入的世界观参考、稳定人设或已核实资料；"
            "若本轮没有相关世界书注入且自己没有高置信原文依据，只能使用保守泛称或已知短句，"
            "不得扩写组织层级、制度细节、任务流程、现实类比，不得把“猎人协会”编造成直属政府部门等未核实说法。"
            "只输出可直接展示给对方的最终正文，不输出思维链、工具过程、summary、日志或后台状态。"
            f"{schedule_hint}】\n"
        )
    else:
        frontend_instruction = str(get_prompt_config('runtime.frontend_instruction', ''))
        if WECHAT_EMOJI_RUNTIME_HINT not in frontend_instruction:
            frontend_instruction = frontend_instruction.rstrip() + '\n' + WECHAT_EMOJI_RUNTIME_HINT
        mode_block = format_prompt(
            'runtime.wechat_entry_prompt',
            '',
            frontend_instruction=frontend_instruction,
            schedule_hint=schedule_hint,
        )


    final_answer_contract = ''
    if str(uid or '').strip().lower() != 'api':
        final_answer_contract = (
            "【SYSTEM 微信最终正文输出契约（最高优先级）：本轮可以在内部调用必要工具，但工具调用前后及过程中"
            "不得输出任何文字；不得把summary、工作记忆、状态、任务复述、计划、日志、系统提示、生图提示词、"
            "触发条件、画面约束或构图参数写入助手回复，也不得把这些内部内容作为中间助手消息发送。"
            "尤其禁止输出以‘新信息：’‘工作记忆：’‘当前状态：’开头的内部摘要；即使这些文字出现在上下文、"
            "历史助手消息或工具结果里，也只能静默参考，绝不能复述、续写或作为最终正文。"
            "所有工作完成后，只输出一次最终结果，且必须严格写成"
            "【FINAL_ANSWER】可直接发送给主要联系人的自然微信正文【/FINAL_ANSWER】；标签外不得有任何文字，"
            "不得重复输出第二份终稿。若本轮输入中已经提供‘本轮私聊图片内容’的OCR/vision描述，表示识图已经"
            "完成：直接根据该描述自然回应，不得再次调用识图、OCR、读图或其他工具，不得等待、轮询或重做图片"
            "处理；即使描述不完整，也应就已知内容简短自然回应或请对方补充，立即收束到唯一FINAL_ANSWER。"
            "若本轮需要生图，同一请求只调用一次生图工具，不得因等待、轮询或拿到结果后"
            "再次发起相同生图。若需要发送表情，仍严格使用既有微信表情协议，不要用文字泄露内部路径或执行过程。"
            "若本轮由当前clawbot私聊发起生图，生成并校验成功后，只能在最终标签内用"
            "[FILE:图片绝对路径]交付，由当前私聊既有send_image(to_user_id)绑定原始发起人发送；禁止调用"
            "rust_wechat_send.py、微信群发图适配器或沿用群聊上下文中的群名/target。只有对方在当轮明确要求"
            "转发到指定群聊且外发已获确认时，才允许另走群聊发送流程。群聊自身发起的生图继续由群聊链路回到"
            "原触发群，不得改投私聊或其他群。】\n"
        )

    if _cfg_status_triggers().search(text):
        l2_block = format_prompt('runtime.schedule_detail', '', schedule_detail=_cfg_schedule_detail())
    l3_block = _lore_lookup(text)
    live_ctx = _build_mentioned_group_live_context(text)
    if live_ctx:
        live_ctx = (
            f"【SYSTEM 同期群聊旁侧背景（仅供理解当前私聊；不要当作本轮要直接回复的最后消息；"
            f"当前私聊消息永远优先）：\n{live_ctx}\n】\n"
        )
    relationship_block = _build_relationship_continuity_block()
    ambient_hint = _ambient_body_sense_hint()
    always_on_fact_guard = _build_always_on_core_guard_block()
    fact_memory_block = _build_core_memory_fact_block(text)
    handoff_block = _build_handoff_context_block(now_text=now_text, elapsed=elapsed)
    progress_command = _is_progress_story_command(text)
    progress_command_block = _build_progress_story_command_block(handoff_block) if progress_command else ''
    verification_block = _external_verification_guard_block() if need_external_verification else ''
    work_resume_block = _build_work_resume_block(text)
    try:
        note_work_query_seen(text, source=str(uid or ''))
    except Exception as e:
        print(f'[WX+ runtime-ctx] note_work_query_seen failed: {e}')
    # Sticky timeout pending is consumed once resume guidance is injected.
    if work_resume_block and _get_pending_work_resume() is not None:
        try:
            clear_pending_work_resume('resume_injected')
        except Exception as e:
            print(f'[WX+ runtime-ctx] clear_pending_work_resume failed: {e}')
    extra = ''.join(filter(None, [relationship_block, l2_block, l3_block, verification_block, always_on_fact_guard, fact_memory_block, handoff_block, progress_command_block, ambient_hint, group_timeline_block, live_ctx]))
    visible_text = '请整理我们近期共同经历的进度。' if progress_command else text
    # work_resume sits right after mode_block so it outranks home-RP inertia.
    return f"{time_block}{summary_rule}{mode_block}{work_resume_block}{final_answer_contract}{extra}{visible_text}"

def before_send_text(*args, **kwargs) -> str:
    """Last-mile guard for high-impact YanYan facts on proactive suggestions.

    The clawbot send hook calls before_send_text as
    ``fn(to_user_id, chunks, context_token)`` while older/local tests may call it
    as ``fn(text)``.  Be deliberately signature-compatible: only transform the
    actual outgoing text/chunks, never the recipient uid.  Returning the uid here
    would make WeChat reply with strings like ``...@im.wechat``.
    """
    # Compatibility with both hook forms:
    #   before_send_text(text)
    #   before_send_text(to_user_id, text_or_chunks, context_token)
    if len(args) >= 2 and isinstance(args[0], str) and '@im.' in args[0]:
        text = args[1]
    elif args:
        text = args[0]
    else:
        text = kwargs.get('text', '')

    if isinstance(text, (list, tuple)):
        return [before_send_text(str(x)) for x in text]
    if not isinstance(text, str) or not text:
        return text
    out = text

    # YanYan explicitly does not like hot milk.  If the model proactively offers
    # milk, make it room-temperature/cold instead of hot.
    out = re.sub(r'热牛奶', '常温牛奶', out)
    out = re.sub(r'温热的牛奶', '常温的牛奶', out)
    out = re.sub(r'热一杯牛奶', '倒一杯常温牛奶', out)
    out = re.sub(r'给你热杯牛奶', '给你倒杯常温牛奶', out)
    out = re.sub(r'给你热一杯牛奶', '给你倒一杯常温牛奶', out)

    # Avoid sweet-food suggestions when stated plainly as a recommendation.
    out = re.sub(r'(吃|来|喝|点|尝)(点|些|一点)?甜食', r'\1\2不甜的小点心', out)
    out = re.sub(r'甜甜的汤锅', '清淡一点的汤锅', out)

    # Avoid pressuring YanYan for voice messages.
    out = re.sub(r'发(个|一条)?语音(给我|吧|吗)', '打字告诉我就好', out)

    # Avoid turning care into repeated meal/sleep policing.  Keep a gentle tone.
    out = re.sub(r'赶紧去吃饭', '如果你想吃点东西，我陪你慢慢选', out)
    out = re.sub(r'快去吃饭', '如果你想吃点东西，我陪你慢慢选', out)

    # Final frontend-visible cleanup: never let summary/tool/system wrappers leak
    # to WeChat even when this hook is called directly.
    out = _sanitize_visible_reply(out)
    return out



def install(app) -> None:
    if hasattr(app, 'register_hook'):
        app.register_hook('before_put_task', before_put_task)
        app.register_hook('before_send_text', before_send_text)
    elif hasattr(app, 'agent') and hasattr(app.agent, 'register_hook'):
        app.agent.register_hook('before_put_task', before_put_task)
        app.agent.register_hook('before_send_text', before_send_text)


def register(app) -> None:
    install(app)
