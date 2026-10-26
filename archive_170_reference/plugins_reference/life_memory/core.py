from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "agents"
GA_COMMON_ROOT = Path(
    os.environ.get("GA_COMMON_ROOT")
    or os.environ.get("GA_COMMON")
    or (PROJECT_ROOT.parent / "ga-common")
)

CATEGORY_PRIORITY = {
    "correction": 100,
    "relationship_need": 95,
    "boundary": 92,
    "health": 88,
    "health_recovery": 45,  # 短时效，不抢正常健康/边界条
    "trip": 82,
    "location": 78,
    "preference": 72,
    "habit": 70,
    "meal": 60,
    "event": 55,
    "note": 40,
    "stable_fact": 90,
}

# 仅在明确纠错意图时触发，避免“不是狼/这不是又要…”误伤
NEGATION_PATTERNS = [
    (re.compile(r"(?:记错了|之前说错|纠正一下|更正一下|不是这样的|我说错了|我改口|以这个为准)"), "correction"),
    (re.compile(r"(?:其实不(?:是|在|去|喜欢|吃)|我不是.{0,12}(?:意思|那个|说)|已经不在|已经离开|离开了|回来了|回到)"), "correction"),
]

RULES: List[Tuple[str, str, List[re.Pattern[str]], Optional[int]]] = [
    (
        "relationship_need",
        "情感需求/雷区",
        [
            re.compile(
                r"(?:我(?:会|有点|很|好)?(?:害怕|担心|难过|不安)|没有安全感|不被在乎|别忘(?:记)?我|要记得我|你应该记住|为什么.*才记|希望你(?:能)?(?:多|在乎|陪))"
            )
        ],
        None,
    ),
    (
        "boundary",
        "沟通/关系边界",
        [
            re.compile(r"(?:不喜欢你|别再|不要再|不许|雷区).{0,30}(?:说|叫|要求|提|做|弄)"),
            # 持久沟通边界：例如「以后不要用带计算和索取意味的话表达爱」
            re.compile(r"(?:以后)?(?:不要|别|不许).{0,40}(?:表达爱|表达喜欢|说爱|说喜欢|沟通|安慰|哄我|对我)"),
        ],
        None,
    ),
    (
        "preference",
        "生活偏好",
        [
            re.compile(r"(?:我喜欢|我爱吃|我想要|我偏好|我最喜欢)(.{1,40})"),
            re.compile(r"(?:我(?:不喜欢|不爱吃|不吃|讨厌))(.{1,40})"),
        ],
        None,
    ),
    (
        "habit",
        "生活习惯",
        [re.compile(r"(?:我习惯|我一般会|我通常|我每天|我经常|我总是|我容易焦虑)(.{1,50})")],
        None,
    ),
    (
        "health",
        "身体状态",
        [
            re.compile(
                r"(?:我(?:现在|今天|这几天)?.{0,40}(?:疼|痛|不舒服|生理期|发烧|咳嗽|胃疼|头疼|腰痛|腰酸|酸痛|失眠|生病|麻|无力|按下去))"
            ),
            # 续痛/还疼：刷新 TTL
            re.compile(
                r"(?:我)?(?:这几天|今天|现在)?.{0,12}(?:腰|腹|肚子|腿|头|胃).{0,8}(?:还是|仍然|依旧|还).{0,4}(?:疼|痛|酸|麻|不舒服)"
            ),
            re.compile(
                r"(?:我)?(?:腰|腹|肚子|腿|头|胃)(?:还是|仍然|依旧|还)(?:疼|痛|酸|麻)"
            ),
            # 夸张修辞也要能识别，再由 objectify 剥掉修辞只留客观状态
            re.compile(
                r"(?:做了?\d+个?(?:仰卧起坐|俯卧撑|深蹲).{0,20}(?:一样|似的).{0,40}(?:疼|痛|酸|麻))"
            ),
        ],
        14,
    ),
    (
        "trip",
        "近期行程",
        [
            re.compile(
                r"(?:我|咱们|我们).{0,12}(?:去|到|飞|坐|赶).{0,20}(?:北京|上海|杭州|广州|深圳|机场|比赛|出差|旅行|高铁|火车|航班)"
            )
        ],
        10,
    ),
    (
        "location",
        "现实状态",
        [
            re.compile(r"(?:我住(?:在)?|我家(?:在)?|我住址(?:是|在)?)(.{1,30})"),
            re.compile(r"(?:我现在|我今天|我这几天|我明天|我后天).{0,12}(?:在|到|去|离开|回)(.{1,20})"),
        ],
        None,
    ),
    (
        "meal",
        "饮食偏好",
        [re.compile(r"(?:我想吃|我吃了|我准备吃|我晚饭|我午饭|我不吃甜|我不喝)(.{0,40})")],
        30,
    ),
]

STOP_CONTEXT_RE = re.compile(r"<summary>.*?</summary>|### \[WORKING MEMORY\].*", re.S)

# 纯寒暄/问句/指令，不应入库
NOISE_EXACT = {
    "嗯", "哦", "好", "好的", "好吧", "哈哈", "哈哈哈", "嗯嗯", "行", "可以",
    "没生气", "不生气了", "开心", "累了", "算了", "算了算了", "好了好了", "哭",
}
NOISE_RE = re.compile(
    r"^(?:"
    r"哈哈+|呵呵+|嘿嘿+|嗯+|哦+|啊+|哎+"
    r"|好了好了|算了算了|没生气|不生气了|开心|哭|哼"
    r")$"
)
# 问句信号：问号任意位置 / 句末语气助词 / 典型疑问起句
QUESTION_ONLY_RE = re.compile(
    r"[?？]|[吗呢嘛]$|^(?:你|您|请问|能不能|可不可以|要不要|有没有|是不是|什么是|.*是什么)"
)
SECOND_PERSON_QUERY_RE = re.compile(
    r"^(?:你|您).{0,40}(?:吗|呢|啥|什么|怎么|怎样|干嘛|在哪|哪里|去不去|有没有|拍照)"
)
COMMAND_RE = re.compile(r"^(?:帮我|你再帮我|麻烦|请你|发一张|看看|查一下)")
# 第一人称事实锚点：排除「帮我/给我/让我」等使役结构里的“我”
FACT_ANCHOR_RE = re.compile(
    r"(?<![帮给让找陪对替把])(?:我|我们|咱们)"
    r"(?:的|现在|今天|这几天|明天|后天|喜欢|不喜欢|不爱|讨厌|害怕|担心|在|去|到|离开|回来|疼|痛|"
    r"不舒服|习惯|想要|想吃|准备|一般|通常|每天|经常|总是|容易|比赛|出差|生理期|发烧|会|有点|很)?"
)
# 耐久生活事实信号（scrub/写门共用）
DURABLE_SIGNAL_RE = re.compile(
    r"(?:喜欢|不喜欢|不爱吃|讨厌|害怕|担心|安全感|雷区|别再|不要再|不许|表达爱|表达喜欢|"
    r"生理期|发烧|咳嗽|胃疼|头疼|腰痛|腰酸|腹部?酸痛|酸痛|失眠|生病|不舒服|"
    r"出差|比赛|旅行|高铁|航班|机场|"
    r"我住|我家在|我现在在|我今天在|我这几天在|已经离开|回到|记错了|纠正一下|更正一下|"
    r"我习惯|我一般会|我通常|我每天|我经常)"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def clean_text(text: str) -> str:
    text = STOP_CONTEXT_RE.sub("", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


# 夸张/修辞比拟：入库时剥掉，只留客观状态
_RHETORIC_SIMILE_RE = re.compile(
    r"(?:现在)?和?做了?\d+个?(?:仰卧起坐|俯卧撑|深蹲|开合跳)一样[，,、]?"
    r"|好像?被(?:车|卡车|卡车碾过|车撞)(?:过|了)?[，,、]?"
    r"|像(?:被)?(?:刀|针)(?:割|扎)一样[，,、]?"
    r"|痛得要死|疼死了|要命地"
)

# 健康恢复/好转：应撤回旧健康条，不能再记成「还疼」
# 覆盖：不疼了 / 不腰疼了 / 腰不疼了 / 已经不腰疼了 / 好多了 / 缓解了
_HEALTH_RECOVERY_RE = re.compile(
    r"(?:"
    r"(?:已经|现在|今天|这几天)?"
    r"(?:"
    r"(?:不|没|没有)(?:再)?(?:那么|这么|很|特别|太)?(?:疼|痛|酸|麻|不舒服)了?"
    r"|(?:不|没|没有)(?:再)?(?:那么|这么|很|特别|太)?(?:腰|腹|肚子|腿|头|胃).{0,4}(?:疼|痛|酸|麻|不舒服)了?"
    r"|(?:腰|腹|肚子|腿|头|胃).{0,6}(?:不|没)(?:再)?(?:那么|这么|很|特别|太)?(?:疼|痛|酸|麻)了?"
    r"|(?:腰|腹|肚子|腿|头|胃).{0,6}(?:好多了|好很多了|好一些了|缓解了|好转了|好了)"
    r"|(?:好多了|好很多了|好一些了|缓解了|好转了|好了|恢复了)"
    r")"
    r")"
)

# 从恢复句里抽部位，用于匹配要撤回的旧条
_HEALTH_PART_RE = re.compile(r"(腰|腹|肚子|腿|头|胃|生理期|腰椎)")


def is_health_recovery(text: str) -> bool:
    t = clean_text(text)
    if not t:
        return False
    # 明确还在疼/还麻：不算恢复
    if re.search(r"(?:还是|仍然|依旧|还).{0,4}(?:疼|痛|酸|麻|不舒服)", t):
        return False
    if re.search(r"(?:还疼|还痛|还酸|还麻|还是疼|还是痛)", t):
        return False
    return bool(_HEALTH_RECOVERY_RE.search(t))


def _health_parts_from_text(text: str) -> List[str]:
    t = clean_text(text)
    parts = _HEALTH_PART_RE.findall(t)
    # 别名归一
    norm = []
    for p in parts:
        if p == "肚子":
            p = "腹"
        if p not in norm:
            norm.append(p)
    return norm


def objectify_fact_text(text: str, category: str = "") -> str:
    """把口语修辞压成客观状态；健康类尤其不要逐句存比喻。"""
    t = clean_text(text)
    if not t:
        return t
    # system/家园噪声直接丢
    if re.search(r"\[system\]|【\.?家园留言\.?】|家园留言", t):
        return ""
    # 恢复句：记成「已缓解/已恢复」，绝不能抽成「还疼」
    if is_health_recovery(t) or category == "health_recovery":
        parts = _health_parts_from_text(t)
        if parts:
            labels = []
            for p in parts:
                if p == "腰":
                    labels.append("腰部不适已缓解")
                elif p == "腹":
                    labels.append("腹部不适已缓解")
                elif p == "腿":
                    labels.append("腿部不适已缓解")
                elif p == "头":
                    labels.append("头痛已缓解")
                elif p == "胃":
                    labels.append("胃部不适已缓解")
                elif p == "生理期":
                    labels.append("生理期不适已缓解")
                elif p == "腰椎":
                    labels.append("腰椎不适已缓解")
                else:
                    labels.append(f"{p}不适已缓解")
            return f"言言{'、'.join(labels)}"
        return "言言身体不适已缓解"
    # 健康/短期状态：去修辞，抽症状
    if category in ("health", "short_term_state", "") or any(
        k in t for k in ("疼", "痛", "酸", "麻", "不舒服", "发烧", "生理期", "MRI", "腰椎")
    ):
        stripped = _RHETORIC_SIMILE_RE.sub("", t)
        # 若否定疼痛（不疼/没痛），不要抽症状
        if re.search(r"(?:不|没|没有)(?:再)?(?:那么|这么|很|特别|太)?(?:疼|痛|酸|麻)", stripped):
            return ""
        symptoms: List[str] = []
        if re.search(r"腰.{0,6}(?:疼|痛|酸)|(?:疼|痛|酸).{0,4}腰", stripped) or "腰痛" in stripped or "腰酸" in stripped:
            symptoms.append("腰部疼痛")
        if re.search(r"(?:腹|肚子).{0,4}(?:酸|痛|疼)|(?:酸|痛|疼).{0,4}(?:腹|肚子)", stripped):
            symptoms.append("腹部酸痛")
        if re.search(r"(?:腿|右腿|左腿).{0,6}(?:麻|无力|软)", stripped) or "腿麻" in stripped:
            symptoms.append("腿部麻木无力")
        if re.search(r"MRI|核磁|腰椎", stripped):
            symptoms.append("曾就医检查腰椎")
        if re.search(r"生理期", stripped):
            symptoms.append("生理期相关不适")
        if re.search(r"发烧|发热", stripped):
            symptoms.append("发烧")
        if re.search(r"头(?:疼|痛)", stripped):
            symptoms.append("头痛")
        if symptoms:
            # 保留「按压加重」等客观修饰
            extra = []
            if re.search(r"按(?:下|压).{0,4}(?:也)?(?:疼|痛)", stripped):
                extra.append("按压加重")
            base = "、".join(symptoms)
            if extra:
                base = f"{base}（{'；'.join(extra)}）"
            return f"言言近期{base}"
        # 没抽到症状但剥掉修辞后仍有内容
        t = stripped
    # 通用：去掉「我现在」口语前缀
    t = re.sub(r"^(?:我现在|我这几天|我今天|我)", "", t).strip(" ，,。")
    return clean_text(t)[:200]


def normalize_key(text: str) -> str:
    t = re.sub(r"[\s，。！？、,.!?;；:：'\"“”‘’（）()\[\]【】]+", "", text.lower())
    t = re.sub(r"\d{4}[-年]\d{1,2}[-月]\d{0,2}日?", "<date>", t)
    for w in ["我", "言言", "用户", "说", "表示", "提到", "明确", "近期"]:
        t = t.replace(w, "")
    return t[:80]


def overlap(a: str, b: str) -> float:
    sa, sb = set(normalize_key(a)), set(normalize_key(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def has_fact_anchor(text: str) -> bool:
    return bool(FACT_ANCHOR_RE.search(text or ""))


def is_noise_text(text: str) -> bool:
    """Write-path quality gate: reject chat noise that is not a durable life fact."""
    t = clean_text(text)
    if not t or len(t) < 4:
        return True
    compact = re.sub(r"[\s\[\]【】Emmem\.。！!，,、~～…]+", "", t)
    if compact in NOISE_EXACT or NOISE_RE.match(compact):
        return True

    anchored = has_fact_anchor(t)
    durable = bool(DURABLE_SIGNAL_RE.search(t))
    # 问句中夹带的「我昨天/我今天」若无耐久信号，不算有效事实锚
    has_question = bool(QUESTION_ONLY_RE.search(t))
    strong_anchor = anchored and durable

    # 纯对“你”的提问/状态盘问，不是言言自身事实
    if SECOND_PERSON_QUERY_RE.search(t) and not strong_anchor:
        return True
    # 纯指令/请求
    if COMMAND_RE.search(t) and not strong_anchor:
        return True
    # 问句且无第一人称耐久事实（含「是什么？我昨天…」类）
    if has_question and not strong_anchor:
        return True
    # 过短且无事实锚点的情绪反应 / 语气词
    if len(compact) <= 8 and not anchored:
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z哈哈呵嗯哦啊哎哼哭]+", compact or ""):
            return True
    # 无耐久信号且无锚点的短闲聊
    if not durable and not anchored and len(compact) < 18:
        return True
    # 临时指令 / 玩笑口吻 / 残句占位，无耐久信号则拒
    if re.search(
        r"(?:只是玩笑|开个玩笑|逗你的|随便说说|临时记一下|先记着|残句|无新增|等等再说|"
        r"^(?:临时|残句|无|等等|我帮你|你再帮我))",
        t,
    ) and not strong_anchor:
        return True
    # 以语气词起头的闲聊短句，且无耐久信号
    if re.match(r"^(?:嗯|哦|啊|哎|好了|算了|哈哈)", t) and not durable:
        return True
    # 对“你/游戏世界”的盘问（流浪体/拍照/出任务）无自身事实
    if re.search(r"(?:流浪体|拍照了吗|出任务|小剧场)", t) and not strong_anchor:
        return True
    return False


@dataclass
class MemoryEvent:
    id: str
    category: str
    label: str
    text: str
    status: str = "active"  # active|superseded|expired|retracted
    confidence: float = 0.7
    salience: int = 50
    created_at: str = ""
    updated_at: str = ""
    last_seen_at: str = ""
    expires_at: Optional[str] = None
    source: str = ""
    subject: str = "user"
    key: str = ""
    supersedes: List[str] = None
    evidence: List[Dict[str, Any]] = None
    retract_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["supersedes"] is None:
            d["supersedes"] = []
        if d["evidence"] is None:
            d["evidence"] = []
        return d


class LifeMemoryStore:
    def __init__(self, agent: str = "default", data_root: Optional[Path | str] = None):
        self.agent = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent or "default")
        root = Path(data_root) if data_root else DEFAULT_DATA_ROOT
        self.base = root / self.agent / "life_memory"
        self.path = self.base / "memories.json"
        self.base.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> Dict[str, Any]:
        return {"version": 1, "agent": self.agent, "updated_at": now_iso(), "memories": []}

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._empty()
            data.setdefault("memories", [])
            return data
        except Exception:
            bak = self.path.with_suffix(".json.bak_unreadable")
            try:
                self.path.replace(bak)
            except Exception:
                pass
            return self._empty()

    def save(self, data: Dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def expire_due(self, data: Optional[Dict[str, Any]] = None, at: Optional[datetime] = None) -> Dict[str, Any]:
        data = data or self.load()
        at = at or datetime.now(timezone.utc).astimezone()
        changed = False
        for m in data.get("memories", []):
            if m.get("status") != "active" or not m.get("expires_at"):
                continue
            dt = parse_dt(m.get("expires_at"))
            if dt and dt <= at:
                m["status"] = "expired"
                m["updated_at"] = now_iso()
                changed = True
        if changed:
            self.save(data)
        return data

    def remember(
        self,
        text: str,
        source: str = "",
        *,
        category: Optional[str] = None,
        label: Optional[str] = None,
        salience: Optional[int] = None,
        ttl_days: Optional[int] = None,
        confidence: Optional[float] = None,
        at: Optional[datetime] = None,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        raw_text = clean_text(text)
        if not raw_text or len(raw_text) < 3:
            return None
        # system/家园等非用户事实
        if re.search(r"\[system\]|【\.?家园留言\.?】|家园留言", raw_text) and not force:
            return None
        # 恢复句优先：哪怕短句也要进撤回逻辑，不能被噪声门挡掉
        recovery_hint = is_health_recovery(raw_text)
        # 候选晋升/人工写入可 force 跳过噪声门
        if not force and not recovery_hint and is_noise_text(raw_text):
            return None
        at = at or datetime.now(timezone.utc).astimezone()
        detected = detect_memory(raw_text)
        if not detected and not category and not recovery_hint:
            return None
        category = category or (detected or {}).get("category") or ("health_recovery" if recovery_hint else None)
        if not category:
            return None
        label = label or (detected or {}).get("label") or category
        recovery = category == "health_recovery" or recovery_hint
        # 客观化：健康/短期状态不存修辞原文；恢复句单独处理
        text = objectify_fact_text(raw_text, "health_recovery" if recovery else category)
        if not text or len(text) < 3:
            return None
        ttl_days = ttl_days if ttl_days is not None else (detected or {}).get("ttl_days")
        if recovery:
            category = "health_recovery"
            label = label or "身体恢复"
            ttl_days = 3 if ttl_days is None else ttl_days
            salience = salience if salience is not None else 40
        else:
            salience = salience if salience is not None else CATEGORY_PRIORITY.get(category, 50)
        conf0 = float(confidence) if confidence is not None else 0.72
        is_correction = category == "correction" or any(p.search(raw_text) for p, _ in NEGATION_PATTERNS)
        # 纠错类仍要求可识别的事实锚点，避免口语否定入库
        if is_correction and not force and not FACT_ANCHOR_RE.search(raw_text) and category == "correction":
            if not any(k in raw_text for k in ("记错", "纠正", "更正", "说错", "改口", "以这个为准", "已经离开", "回来了")):
                return None
        data = self.expire_due(self.load(), at)
        key = normalize_key(text)
        candidates = [m for m in data["memories"] if m.get("status") == "active"]

        # 恢复句：撤回相关健康条，并写一条短时效「已缓解」
        if recovery:
            parts = _health_parts_from_text(raw_text)
            retracted_ids: List[str] = []
            for m in candidates:
                if m.get("category") not in ("health", "short_term_state"):
                    continue
                mt = m.get("text", "")
                # 有部位时按部位匹配；无部位则撤回全部 active health
                if parts:
                    hit = False
                    for p in parts:
                        if p in mt or (p == "腰" and "腰" in mt) or (p == "腹" and ("腹" in mt or "肚子" in mt)):
                            hit = True
                            break
                    if not hit:
                        continue
                m["status"] = "retracted"
                m["retract_reason"] = "user_reported_recovery"
                m["updated_at"] = now_iso()
                retracted_ids.append(m["id"])
            expires_at = (at + timedelta(days=ttl_days or 3)).isoformat(timespec="seconds")
            ev = {"at": at.isoformat(timespec="seconds"), "source": source, "text": raw_text[:120]}
            new = MemoryEvent(
                id=str(uuid.uuid4()),
                category="health_recovery",
                label=label,
                text=text,
                confidence=max(0.8, conf0),
                salience=int(salience),
                created_at=at.isoformat(timespec="seconds"),
                updated_at=now_iso(),
                last_seen_at=at.isoformat(timespec="seconds"),
                expires_at=expires_at,
                source=source,
                key=key,
                supersedes=retracted_ids,
                evidence=[ev],
            ).to_dict()
            data["memories"].append(new)
            self.save(data)
            return new

        matched = None
        for m in candidates:
            if m.get("category") == category and (m.get("key") == key or overlap(m.get("text", ""), text) >= 0.62):
                matched = m
                break
        # 同类健康症状（部位重叠）也算同一条，便于续期
        if not matched and category in ("health", "short_term_state"):
            parts = _health_parts_from_text(raw_text + " " + text)
            for m in candidates:
                if m.get("category") not in ("health", "short_term_state"):
                    continue
                mt = m.get("text", "")
                if parts and any(p in mt for p in parts):
                    matched = m
                    break
                if overlap(mt, text) >= 0.45:
                    matched = m
                    break
        if not matched and is_correction:
            best = (0.0, None)
            for m in candidates:
                sc = overlap(m.get("text", ""), text)
                if sc > best[0]:
                    best = (sc, m)
            if best[0] >= 0.45:
                matched = best[1]
        expires_at = (at + timedelta(days=ttl_days)).isoformat(timespec="seconds") if ttl_days else None
        ev = {"at": at.isoformat(timespec="seconds"), "source": source, "text": text}
        if matched:
            if is_correction and matched.get("text") != text:
                old_id = matched["id"]
                matched["status"] = "superseded"
                matched["updated_at"] = now_iso()
                new = MemoryEvent(
                    id=str(uuid.uuid4()),
                    category="correction" if category == "correction" else category,
                    label=label,
                    text=text,
                    confidence=max(0.8, conf0),
                    salience=max(salience, matched.get("salience", 50)),
                    created_at=at.isoformat(timespec="seconds"),
                    updated_at=now_iso(),
                    last_seen_at=at.isoformat(timespec="seconds"),
                    expires_at=expires_at,
                    source=source,
                    key=key,
                    supersedes=[old_id],
                    evidence=[ev],
                ).to_dict()
                data["memories"].append(new)
                self.save(data)
                return new
            # 续期/刷新：同部位还疼时更新文本、刷新 expires_at
            matched["text"] = merge_text(matched.get("text", ""), text) if category not in ("health", "short_term_state") else text
            matched["salience"] = max(int(matched.get("salience", 0)), salience)
            matched["confidence"] = min(0.95, float(matched.get("confidence", 0.7)) + 0.05)
            matched["updated_at"] = now_iso()
            matched["last_seen_at"] = at.isoformat(timespec="seconds")
            if expires_at:
                matched["expires_at"] = expires_at  # 还疼 → 从今天重新计 TTL
            matched.setdefault("evidence", []).append(ev)
            matched["evidence"] = matched["evidence"][-5:]
            self.save(data)
            return matched
        mem = MemoryEvent(
            id=str(uuid.uuid4()),
            category=category,
            label=label,
            text=text,
            confidence=conf0,
            salience=salience,
            created_at=at.isoformat(timespec="seconds"),
            updated_at=now_iso(),
            last_seen_at=at.isoformat(timespec="seconds"),
            expires_at=expires_at,
            source=source,
            key=key,
            supersedes=[],
            evidence=[ev],
        ).to_dict()
        data["memories"].append(mem)
        self.save(data)
        return mem

    def query(self, query: str = "", limit: int = 8, include_expired: bool = False) -> List[Dict[str, Any]]:
        data = self.expire_due(self.load())
        q = clean_text(query)
        now = datetime.now(timezone.utc).astimezone()
        # 健康相关触发词：只有对话沾边时才抬健康条
        health_trigger = bool(
            re.search(
                r"(?:腰|背|腹|腿|疼|痛|酸|麻|不舒服|身体|病|医院|MRI|核磁|生理期|发烧|头)",
                q,
            )
        ) if q else False
        scored = []
        for m in data.get("memories", []):
            if not include_expired and m.get("status") != "active":
                continue
            cat = str(m.get("category") or "")
            score = int(m.get("salience", 0))
            # 年龄衰退：有 expires 的短期/健康类按剩余寿命衰减；无 expires 按 last_seen 慢衰减
            age_days = 0.0
            ref = parse_dt(m.get("last_seen_at") or m.get("updated_at") or m.get("created_at") or "")
            if ref:
                age_days = max(0.0, (now - ref).total_seconds() / 86400.0)
            exp_dt = parse_dt(m.get("expires_at") or "")
            if exp_dt:
                total_life = max(1.0, (exp_dt - (parse_dt(m.get("created_at") or "") or exp_dt)).total_seconds() / 86400.0)
                remaining = max(0.0, (exp_dt - now).total_seconds() / 86400.0)
                # 越临近过期分越低；过半寿命后明显掉
                life_ratio = remaining / total_life
                score = int(score * (0.35 + 0.65 * life_ratio))
                # 超过一半寿命且对话未提健康 → 健康条几乎不注入
                if cat in ("health", "short_term_state", "schedule") and not health_trigger and life_ratio < 0.55:
                    score -= 60
            else:
                # 稳定事实：超过 7/30 天未再确认，缓慢掉分
                if age_days > 30:
                    score -= min(40, int((age_days - 30) * 0.5))
                elif age_days > 7 and cat in ("health", "short_term_state"):
                    score -= int((age_days - 7) * 4)
            # 健康/短期状态/恢复条：无相关话题时直接不注入（可关心，但禁止闲聊硬塞）
            if cat in ("health", "short_term_state", "health_recovery") and not health_trigger:
                continue
            if q:
                score += int(overlap(m.get("text", ""), q) * 100)
                if q in m.get("text", ""):
                    score += 80
                # 关键词命中健康条
                if cat in ("health", "short_term_state", "health_recovery") and health_trigger:
                    score += 40
            # 置信度加权
            score += int(float(m.get("confidence", 0.7)) * 20)
            if score < 20:
                continue
            scored.append((score, m))
        scored.sort(key=lambda x: (x[0], x[1].get("updated_at", "")), reverse=True)
        return [m for _, m in scored[:limit]]

    def build_context(self, incoming_text: str = "", limit: int = 8) -> str:
        items = self.query(incoming_text, limit=limit)
        if not items:
            return ""
        lines = [
            "[生活记忆旁侧参考｜只作背景；不要逐字复述，不要每句/每天主动提同一条；"
            "短期健康状态仅在她提起身体/相关话题或明显需要关心时轻点一下，平时当背景；"
            "这些内容不写入聊天history]"
        ]
        for m in items:
            exp = f"，有效至{m['expires_at'][:10]}" if m.get("expires_at") else ""
            age_hint = ""
            ref = parse_dt(m.get("last_seen_at") or m.get("created_at") or "")
            if ref:
                age_hint = f"，记于{ref.date().isoformat()}"
            lines.append(
                f"- {m.get('label') or m.get('category')}: {m.get('text')}"
                f"（置信{m.get('confidence', 0):.2f}{exp}{age_hint}）"
            )
        return "\n".join(lines)

    def retract_noise(self, dry_run: bool = False) -> Dict[str, Any]:
        """把现有库中明显噪声标为 retracted，不物理删除。"""
        data = self.load()
        retracted = []
        for m in data.get("memories", []):
            if m.get("status") != "active":
                continue
            text = m.get("text") or ""
            if is_noise_text(text) or _legacy_noise_heuristic(m):
                retracted.append({"id": m.get("id"), "text": text, "category": m.get("category")})
                if not dry_run:
                    m["status"] = "retracted"
                    m["retract_reason"] = "noise_gate_v2"
                    m["updated_at"] = now_iso()
        if retracted and not dry_run:
            self.save(data)
        return {"retracted_count": len(retracted), "items": retracted, "dry_run": dry_run}


def _legacy_noise_heuristic(m: Dict[str, Any]) -> bool:
    """对旧规则误伤条目的补充判定。"""
    text = str(m.get("text") or "")
    cat = m.get("category")
    # 旧 correction 大多是口语否定
    if cat == "correction" and not any(k in text for k in ("记错", "纠正", "更正", "说错", "改口", "以这个为准")):
        if not FACT_ANCHOR_RE.search(text):
            return True
    # 旧 location 常是对“你”的盘问 / 闲聊承接
    if cat == "location" and text.startswith("你") and "我" not in text[:4]:
        return True
    if cat == "location" and is_noise_text(text):
        return True
    # 健康类若其实是知识问答/闲聊
    if cat == "health" and is_noise_text(text):
        return True
    # 情绪短句
    if cat == "relationship_need" and re.search(r"(?:没生气|不生气|哈哈哈|开心|害怕\[)", text) and len(text) < 30:
        if "没有安全感" not in text and "希望你" not in text and "别忘" not in text:
            return True
    return False


def merge_text(old: str, new: str) -> str:
    if normalize_key(old) == normalize_key(new) or new in old:
        return old
    if old in new:
        return new
    return new if len(new) >= len(old) * 0.7 else old


def detect_memory(text: str) -> Optional[Dict[str, Any]]:
    text = clean_text(text)
    if not text:
        return None
    for pat, cat in NEGATION_PATTERNS:
        if pat.search(text):
            return {"category": "correction", "label": "纠错/更新", "ttl_days": None}
    # 身体好转/不疼了：优先于普通 health，避免误记成还疼
    if is_health_recovery(text):
        return {"category": "health_recovery", "label": "身体恢复", "ttl_days": 3}
    for category, label, patterns, ttl in RULES:
        for pat in patterns:
            if pat.search(text):
                return {"category": category, "label": label, "ttl_days": ttl}
    return None


# ---------------------------------------------------------------------------
# 统一读入口：life_memory + journal recall + dream candidates（只读聚合，不合并库）
# ---------------------------------------------------------------------------

def _journal_recall_path(agent: str) -> Path:
    return GA_COMMON_ROOT / "journal_system" / "agents" / agent / "indexes" / "recall_cards.jsonl"


def _dream_candidates_path(agent: str) -> Path:
    return GA_COMMON_ROOT / "dream_system" / "agents" / agent / "user_fact_candidates.md"


def _dream_stable_path(agent: str) -> Path:
    return GA_COMMON_ROOT / "dream_system" / "agents" / agent / "user_fact_memory.md"


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _score_journal_card(card: Dict[str, Any], query: str) -> int:
    q = (query or "").lower()
    s = 0
    for t in card.get("trigger", []) or []:
        if str(t).lower() in q or str(t) in (query or ""):
            s += 3
    content = str(card.get("content", ""))
    for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,6}", query or ""):
        if token and token in content:
            s += 2
    s += int(card.get("importance", 1) or 1)
    # 无 query 时按重要性保底
    if not (query or "").strip():
        s = int(card.get("importance", 1) or 1) + 1
    return s


def load_journal_recalls(agent: str, query: str = "", limit: int = 4) -> List[Dict[str, Any]]:
    cards = _load_jsonl(_journal_recall_path(agent))
    cards = [c for c in cards if not c.get("technical_memory")]
    ranked = sorted(cards, key=lambda c: _score_journal_card(c, query), reverse=True)
    if query.strip():
        ranked = [c for c in ranked if _score_journal_card(c, query) > 0]
    out = []
    for c in ranked[:limit]:
        content = clean_text(str(c.get("content") or ""))
        if not content:
            continue
        # 截断防 prompt 膨胀
        if len(content) > 220:
            content = content[:220] + "…"
        out.append(
            {
                "source_kind": "journal",
                "category": "emotional_memory",
                "label": "日记/情感记忆",
                "text": content,
                "salience": 70 + int(c.get("importance", 1) or 1) * 5,
                "confidence": 0.8,
                "id": c.get("card_id") or c.get("entry_id") or "",
            }
        )
    return out


def parse_dream_candidates(path: Optional[Path] = None, agent: str = "xinghui") -> List[Dict[str, Any]]:
    path = path or _dream_candidates_path(agent)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    current_date = ""
    cur: Optional[Dict[str, Any]] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        m_date = re.match(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$", line)
        if m_date:
            current_date = m_date.group(1)
            cur = None
            continue
        m_item = re.match(r"^-\s+\[([^\]]+)\]\s+(.+)$", line)
        if m_item:
            if cur:
                items.append(cur)
            cur = {
                "type": m_item.group(1).strip(),
                "text": m_item.group(2).strip(),
                "date": current_date,
                "source": "",
                "confidence": "medium",
                "sensitivity": "low",
                "action": "candidate",
            }
            continue
        if cur and line.strip().startswith("- "):
            meta = line.strip()[2:].strip()
            if meta.startswith("source:"):
                cur["source"] = meta[len("source:") :].strip()
            elif "confidence:" in meta:
                # confidence: high; sensitivity: low; action: candidate
                parts = [p.strip() for p in meta.split(";")]
                for p in parts:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        cur[k.strip()] = v.strip()
    if cur:
        items.append(cur)
    # 仅保留仍是 candidate 的
    return [x for x in items if str(x.get("action", "candidate")).lower() in ("candidate", "pending", "")]


def load_dream_candidates(agent: str, query: str = "", limit: int = 3) -> List[Dict[str, Any]]:
    raw = parse_dream_candidates(agent=agent)
    q = clean_text(query)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for i, c in enumerate(raw):
        text = str(c.get("text") or "")
        score = 40
        conf = str(c.get("confidence") or "medium").lower()
        score += {"high": 30, "medium": 15, "low": 5}.get(conf, 10)
        if q:
            score += int(overlap(text, q) * 80)
            if any(tok in text for tok in re.findall(r"[\u4e00-\u9fff]{2,6}", q)):
                score += 20
        else:
            # 无 query 时偏近期
            score += max(0, 20 - i)  # 文件顺序大致按日期块
        scored.append((score, {**c, "_idx": i, "_score": score}))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, c in scored[:limit]:
        text = clean_text(str(c.get("text") or ""))
        if not text or text == "无新增候选。":
            continue
        if len(text) > 180:
            text = text[:180] + "…"
        ctype = str(c.get("type") or "note")
        cat_map = {
            "preference": "preference",
            "boundary": "boundary",
            "relationship": "relationship_need",
            "health": "health",
            "habit": "habit",
            "fact": "stable_fact",
            "stable": "stable_fact",
        }
        out.append(
            {
                "source_kind": "dream_candidate",
                "category": cat_map.get(ctype, "note"),
                "label": f"梦候选/{ctype}",
                "text": text,
                "salience": min(90, 50 + score // 5),
                "confidence": 0.55 if str(c.get("confidence")).lower() != "high" else 0.68,
                "id": f"dream-{c.get('date', '')}-{c.get('_idx', 0)}",
                "candidate_index": c.get("_idx"),
                "date": c.get("date"),
            }
        )
    return out


def parse_dream_stable(agent: str = "xinghui", path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """解析梦系统积分晋升后的稳定事实文件 user_fact_memory.md。"""
    path = path or _dream_stable_path(agent)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line.startswith("- ["):
            continue
        m = re.match(r"^- \[([^\]]+)\]\s*(.+)$", line)
        if not m:
            continue
        ctype = m.group(1).strip()
        body = m.group(2).strip()
        meta: Dict[str, str] = {}
        # 尾部元数据：（来源：…；置信度：…；score:…；…）
        mm = re.search(r"（([^）]*)）\s*$", body)
        text = body
        if mm:
            text = body[: mm.start()].strip()
            for part in re.split(r"[；;]", mm.group(1)):
                part = part.strip()
                if "：" in part:
                    k, v = part.split("：", 1)
                    meta[k.strip()] = v.strip()
                elif ":" in part:
                    k, v = part.split(":", 1)
                    meta[k.strip()] = v.strip()
        if not text:
            continue
        items.append(
            {
                "type": ctype,
                "text": text,
                "source": meta.get("来源") or meta.get("source") or "dream_stable",
                "confidence": meta.get("置信度") or meta.get("confidence") or "high",
                "score": meta.get("score") or "",
                "dates": meta.get("dates") or "",
                "mode": meta.get("mode") or "score",
            }
        )
    return items


def load_dream_stable_facts(agent: str, query: str = "", limit: int = 3) -> List[Dict[str, Any]]:
    """读取已积分晋升的梦稳定事实，作为高置信旁侧上下文。"""
    raw = parse_dream_stable(agent=agent)
    q = clean_text(query)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for i, c in enumerate(raw):
        text = str(c.get("text") or "")
        if not text:
            continue
        score = 70
        conf = str(c.get("confidence") or "high").lower()
        score += {"high": 25, "medium": 12, "low": 4}.get(conf, 10)
        ctype = str(c.get("type") or "")
        score += {"boundary": 12, "preference": 10, "interaction_style": 10, "relationship": 8}.get(ctype, 0)
        if q:
            score += int(overlap(text, q) * 90)
            if any(tok in text for tok in re.findall(r"[\u4e00-\u9fff]{2,6}", q)):
                score += 25
        else:
            score += max(0, 10 - i)
        scored.append((score, {**c, "_idx": i, "_score": score}))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, c in scored[:limit]:
        text = clean_text(str(c.get("text") or ""))
        if not text:
            continue
        if len(text) > 180:
            text = text[:180] + "…"
        ctype = str(c.get("type") or "note")
        cat_map = {
            "preference": "preference",
            "boundary": "boundary",
            "relationship": "relationship_need",
            "interaction_style": "interaction_style",
            "habit": "habit",
            "fact": "stable_fact",
        }
        conf = str(c.get("confidence") or "high").lower()
        out.append(
            {
                "source_kind": "dream_stable",
                "category": cat_map.get(ctype, "stable_fact"),
                "label": f"梦稳定/{ctype}",
                "text": text,
                "salience": min(95, 70 + score // 6),
                "confidence": 0.82 if conf == "high" else 0.72 if conf == "medium" else 0.62,
                "id": f"dream-stable-{c.get('_idx', 0)}",
                "dates": c.get("dates") or "",
                "score_meta": c.get("score") or "",
            }
        )
    return out


def build_unified_context(
    agent: str,
    incoming_text: str = "",
    data_root: Optional[str | Path] = None,
    *,
    life_limit: int = 5,
    journal_limit: int = 3,
    dream_limit: int = 2,
    dream_stable_limit: int = 3,
    total_limit: int = 8,
) -> str:
    """
    统一读入口：聚合多源，只生成 side_context，不写 history，不合并底层库。
    优先级大致：life_memory 事实 ≈ 梦稳定事实 > journal 情感卡 > dream 未晋升候选（低置信提示）。
    """
    store = get_store(agent, data_root)
    life_items = store.query(incoming_text, limit=life_limit)
    journal_items = load_journal_recalls(agent, incoming_text, limit=journal_limit)
    dream_stable_items = load_dream_stable_facts(agent, incoming_text, limit=dream_stable_limit)
    dream_items = load_dream_candidates(agent, incoming_text, limit=dream_limit)

    merged: List[Tuple[int, str, Dict[str, Any]]] = []
    for m in life_items:
        score = int(m.get("salience", 50)) + int(float(m.get("confidence", 0.7)) * 30) + 40
        merged.append((score, "life", m))
    for m in dream_stable_items:
        # 已积分晋升：接近 life 事实，略低于主动 life 写入
        score = int(m.get("salience", 80)) + int(float(m.get("confidence", 0.8)) * 25) + 30
        merged.append((score, "dream_stable", m))
    for m in journal_items:
        score = int(m.get("salience", 60)) + 20
        merged.append((score, "journal", m))
    for m in dream_items:
        score = int(m.get("salience", 40))  # 候选默认更低
        merged.append((score, "dream", m))

    merged.sort(key=lambda x: x[0], reverse=True)
    merged = merged[:total_limit]
    if not merged:
        return ""

    lines = [
        "[连续人格记忆门控｜旁侧参考，勿逐字复述，不写入聊天history]",
        "使用原则：优先已确认生活事实与梦稳定事实；日记情感卡只在情绪/关系相关时轻触；梦候选未晋升前仅作弱提示。",
        "短期健康/状态：只作背景，不要每句/每天主动提同一条；仅在她提起身体或明显需要关心时轻点一下。",
    ]
    life_lines, stable_lines, journal_lines, dream_lines = [], [], [], []
    for score, kind, m in merged:
        text = clean_text(str(m.get("text") or ""))
        if not text:
            continue
        if kind == "life":
            exp = f"，有效至{m['expires_at'][:10]}" if m.get("expires_at") else ""
            life_lines.append(
                f"- {m.get('label') or m.get('category')}: {text}（置信{float(m.get('confidence', 0)):.2f}{exp}）"
            )
        elif kind == "dream_stable":
            stable_lines.append(
                f"- {m.get('label') or '梦稳定'}: {text}（置信{float(m.get('confidence', 0.8)):.2f}，已积分晋升）"
            )
        elif kind == "journal":
            journal_lines.append(f"- {text}")
        else:
            dream_lines.append(
                f"- [{m.get('label') or '候选'}] {text}（未晋升，置信{float(m.get('confidence', 0.55)):.2f}）"
            )

    if life_lines:
        lines.append("## 已确认生活事实")
        lines.extend(life_lines)
    if stable_lines:
        lines.append("## 梦系统稳定事实（积分晋升）")
        lines.extend(stable_lines)
    if journal_lines:
        lines.append("## 情感/关系日记卡")
        lines.extend(journal_lines)
    if dream_lines:
        lines.append("## 梦系统候选（弱提示，勿当既定事实）")
        lines.extend(dream_lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 候选晋升管道（人机共治：默认只列/建议，promote 才写入 life_memory）
# ---------------------------------------------------------------------------

def list_candidates(agent: str = "xinghui") -> List[Dict[str, Any]]:
    items = parse_dream_candidates(agent=agent)
    out = []
    for i, c in enumerate(items):
        out.append(
            {
                "index": i,
                "date": c.get("date"),
                "type": c.get("type"),
                "text": c.get("text"),
                "confidence": c.get("confidence"),
                "sensitivity": c.get("sensitivity"),
                "action": c.get("action"),
                "source": c.get("source"),
            }
        )
    return out


def promote_candidate(
    agent: str,
    index: int,
    *,
    data_root: Optional[str | Path] = None,
    source: str = "dream_promote",
) -> Optional[Dict[str, Any]]:
    items = parse_dream_candidates(agent=agent)
    if index < 0 or index >= len(items):
        return None
    c = items[index]
    text = clean_text(str(c.get("text") or ""))
    if not text or text == "无新增候选。":
        return None
    ctype = str(c.get("type") or "note")
    cat_map = {
        "preference": ("preference", "生活偏好"),
        "boundary": ("boundary", "沟通/关系边界"),
        "relationship": ("relationship_need", "情感需求/雷区"),
        "health": ("health", "身体状态"),
        "habit": ("habit", "生活习惯"),
        "fact": ("stable_fact", "稳定事实"),
        "stable": ("stable_fact", "稳定事实"),
        "meal": ("meal", "饮食偏好"),
        "location": ("location", "现实状态"),
        "trip": ("trip", "近期行程"),
    }
    category, label = cat_map.get(ctype, ("note", f"梦候选/{ctype}"))
    conf = {"high": 0.8, "medium": 0.7, "low": 0.6}.get(str(c.get("confidence") or "medium").lower(), 0.7)
    # 晋升也走噪声门：临时指令/玩笑/残句不得因 force 绕过写门
    # 已判类型的候选可带 category 写入；噪声则 remember 返回 None
    mem = get_store(agent, data_root).remember(
        text,
        source=f"{source}:{c.get('date') or ''}",
        category=category,
        label=label,
        confidence=conf,
        salience=CATEGORY_PRIORITY.get(category, 70),
        force=False,
    )
    return mem


def get_store(agent: str = "default", data_root: Optional[str | Path] = None) -> LifeMemoryStore:
    return LifeMemoryStore(agent=agent, data_root=data_root)


def remember_text(agent: str, text: str, source: str = "", data_root: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
    return get_store(agent, data_root).remember(text, source=source)


def build_context(agent: str, incoming_text: str = "", data_root: Optional[str | Path] = None, limit: int = 8) -> str:
    """向后兼容：默认走统一门控读入口。"""
    return build_unified_context(agent, incoming_text, data_root=data_root, total_limit=limit)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Life memory CLI (gated)")
    ap.add_argument(
        "cmd",
        choices=[
            "remember",
            "context",
            "list",
            "expire",
            "scrub",
            "candidates",
            "promote",
            "unified",
        ],
    )
    ap.add_argument("--agent", default=os.environ.get("GA_AGENT", "default"))
    ap.add_argument("--text", default="")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--index", type=int, default=-1)
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)
    store = get_store(ns.agent, ns.data_root)
    if ns.cmd == "remember":
        print(json.dumps(store.remember(ns.text, source=ns.source), ensure_ascii=False, indent=2))
    elif ns.cmd in ("context", "unified"):
        print(build_unified_context(ns.agent, ns.text, data_root=ns.data_root, total_limit=ns.limit))
    elif ns.cmd == "list":
        print(json.dumps(store.query(ns.text, limit=ns.limit, include_expired=True), ensure_ascii=False, indent=2))
    elif ns.cmd == "expire":
        store.expire_due()
        print(json.dumps(store.load(), ensure_ascii=False, indent=2))
    elif ns.cmd == "scrub":
        print(json.dumps(store.retract_noise(dry_run=ns.dry_run), ensure_ascii=False, indent=2))
    elif ns.cmd == "candidates":
        print(json.dumps(list_candidates(ns.agent), ensure_ascii=False, indent=2))
    elif ns.cmd == "promote":
        print(json.dumps(promote_candidate(ns.agent, ns.index, data_root=ns.data_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
