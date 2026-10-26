"""Xinghui photo planner for the standalone gpt-image-2 provider.

This module builds detailed photo prompts and chooses reference assets for
Xinghui-visible images. It does not send to WeChat by itself; callers can use the
returned image paths/media files in their own delivery layer after success.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import pathlib
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

try:  # Support both package import and direct script execution.
    from .gpt_image2_provider import generate_image
except Exception:  # pragma: no cover
    from gpt_image2_provider import generate_image  # type: ignore

PhotoMode = Literal["selfie", "mirror", "tripod", "third_party", "surveillance", "pov", "object"]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REF_DIR = REPO_ROOT / "assets" / "agent_image_refs" / "xinghui"
DEFAULT_OUT_DIR = REPO_ROOT / "temp" / "gpt_image2_xinghui"
XINGHUI_FACE_PREFIX = "这是我的照片，请维持我的形象不变，严禁白人化。在此基础上细节生图prompt："


@dataclass(frozen=True)
class PhotoPlan:
    prompt: str
    mode: PhotoMode
    includes_xinghui: bool
    reference_images: list[pathlib.Path]
    size: str = "1024x1024"


def current_season(now: _dt.datetime | None = None) -> str:
    month = (now or _dt.datetime.now()).month
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


def requested_season(text: str, now: _dt.datetime | None = None) -> str:
    """Use explicit seasonal wording first; otherwise fall back to current date."""
    if re.search(r"冬|冬天|冬季|winter|cold weather|snowy", text, re.I):
        return "winter"
    if re.search(r"夏|夏天|夏季|summer|hot weather", text, re.I):
        return "summer"
    if re.search(r"秋|秋天|秋季|autumn|fall", text, re.I):
        return "autumn"
    if re.search(r"春|春天|春季|spring", text, re.I):
        return "spring"
    return current_season(now)


def infer_mode(text: str) -> PhotoMode:
    s = text.lower()
    if re.search(r"监控|cctv|surveillance|security camera|摄像头", text, re.I):
        return "surveillance"
    if re.search(r"对镜|镜子|镜面|mirror", text, re.I):
        return "mirror"
    if re.search(r"三脚架|支架|tripod|timer|定时", text, re.I):
        return "tripod"
    if re.search(r"路人|别人拍|帮拍|第三方|朋友拍|偷拍|街拍|third[- ]party|taken by someone", text, re.I):
        return "third_party"
    if re.search(r"自拍|selfie|前置", text, re.I):
        return "selfie"
    if re.search(r"第一视角|pov|我看到|我的手|桌面|窗外|给你看|plog", text, re.I):
        return "pov"
    return "third_party"


def includes_xinghui(text: str, mode: PhotoMode | None = None) -> bool:
    if mode in {"selfie", "mirror", "tripod", "third_party", "surveillance"}:
        return True
    return bool(
        re.search(
            r"沈星回|xavier|你本人|你出镜|你入镜|你的脸|你的照片|露脸|人像|自拍|对镜|镜子|三脚架|监控|帮拍|街拍|全身|半身|正脸|侧脸",
            text,
            re.I,
        )
    )


def _all_refs(ref_dir: pathlib.Path = DEFAULT_REF_DIR) -> list[pathlib.Path]:
    if not ref_dir.exists():
        return []
    return sorted(
        p for p in ref_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )


def _ref_text(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return "/".join(part.lower() for part in rel.parts)


def _is_outfit_ref(path: pathlib.Path, root: pathlib.Path) -> bool:
    n = _ref_text(path, root)
    return bool(re.search(r"服饰|服装|穿搭|衣服|outfit|clothes|wardrobe|居家|家居|home|外出|通勤|户外|outdoor|sleep|睡衣|pajama|pyjama", n, re.I))


def _rotation_state_path(ref_dir: pathlib.Path) -> pathlib.Path:
    safe = hashlib.sha1(str(pathlib.Path(ref_dir).resolve()).encode("utf-8", "ignore")).hexdigest()[:12]
    return pathlib.Path(__file__).resolve().parent / "runtime" / "state" / f"xinghui_photo_ref_rotation_{safe}.json"


def _load_rotation_state(ref_dir: pathlib.Path) -> dict[str, str]:
    try:
        data = json.loads(_rotation_state_path(ref_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_rotation_state(ref_dir: pathlib.Path, state: dict[str, str]) -> None:
    try:
        path = _rotation_state_path(ref_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _rotated_best(candidates: list[pathlib.Path], *, score_fn, root: pathlib.Path, bucket: str, seed: str) -> pathlib.Path | None:
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda p: (-score_fn(p), _ref_text(p, root)))
    best_score = score_fn(ranked[0])
    top = [p for p in ranked if score_fn(p) == best_score]
    state = _load_rotation_state(root)
    last = state.get(bucket)
    if len(top) > 1 and last:
        top = top[1:] + top[:1] if _ref_text(top[0], root) == last else top
    idx = int(hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:8], 16) % len(top)
    chosen = top[idx]
    if len(top) > 1 and _ref_text(chosen, root) == last:
        chosen = top[(idx + 1) % len(top)]
    state[bucket] = _ref_text(chosen, root)
    _save_rotation_state(root, state)
    return chosen


def _desired_face_view(text: str, mode: PhotoMode) -> str:
    """Return the required face-view bucket for identity reference selection."""
    if re.search(r"左侧脸|左侧面|左脸|left\s*profile|left\s*side", text, re.I):
        return "left_profile"
    if re.search(r"右侧脸|右侧面|右脸|right\s*profile|right\s*side", text, re.I):
        return "right_profile"
    if re.search(r"侧脸|侧面|侧颜|profile|side\s*face|side\s*view", text, re.I):
        return "profile"
    if re.search(r"正脸|正面|看镜头|面对镜头|front\s*face|front\s*view|face\s*camera", text, re.I):
        return "front"
    # Default by shot type: selfies/mirror/tripod usually need front identity; candid/CCTV often need side/profile.
    if mode in {"selfie", "mirror", "tripod"}:
        return "front"
    if mode in {"third_party", "surveillance"}:
        return "profile"
    return "front"


def _face_view_of_ref(path: pathlib.Path, root: pathlib.Path) -> str:
    n = _ref_text(path, root)
    if re.search(r"左侧脸|左侧面|左脸|left", n, re.I):
        return "left_profile"
    if re.search(r"右侧脸|右侧面|右脸|right", n, re.I):
        return "right_profile"
    if re.search(r"侧脸|侧面|侧颜|profile|side", n, re.I):
        return "profile"
    if re.search(r"正面|正脸|front|face", n, re.I):
        return "front"
    return "unknown"


def _filter_face_refs_by_view(face_refs: list[pathlib.Path], root: pathlib.Path, desired: str) -> list[pathlib.Path]:
    if not face_refs:
        return []
    view_map: dict[str, list[pathlib.Path]] = {"front": [], "profile": [], "left_profile": [], "right_profile": [], "unknown": []}
    for p in face_refs:
        view_map.setdefault(_face_view_of_ref(p, root), []).append(p)
    if desired in {"left_profile", "right_profile"}:
        exact = view_map.get(desired, [])
        if exact:
            return exact
        side = view_map.get("profile", []) + view_map.get("left_profile", []) + view_map.get("right_profile", [])
        if side:
            return side
        return view_map.get("unknown", []) or face_refs
    if desired == "profile":
        side = view_map.get("profile", []) + view_map.get("left_profile", []) + view_map.get("right_profile", [])
        if side:
            return side
        return view_map.get("unknown", []) or face_refs
    # front: do not let side-face refs win just because of rotation; use side only if no front/unknown exists.
    front = view_map.get("front", [])
    if front:
        return front
    return view_map.get("unknown", []) or face_refs


def choose_reference_images(text: str, mode: PhotoMode, ref_dir: pathlib.Path = DEFAULT_REF_DIR) -> list[pathlib.Path]:
    """Choose safe gpt-image-2 references by semantic need, then rotate only within matches.

    Slot 1 is face/identity and is first filtered by requested face angle/view:
    front requests use front-face refs, side/profile requests use side/profile refs. Rotation is
    only a tie-breaker within the matching bucket, not a cross-angle substitute. Slot 2, when
    available, is outfit/clothing.
    """
    root = pathlib.Path(ref_dir)
    refs = _all_refs(root)
    if not refs:
        return []

    face_refs = [p for p in refs if not _is_outfit_ref(p, root)]
    outfit_refs = [p for p in refs if _is_outfit_ref(p, root)]
    desired_view = _desired_face_view(text, mode)
    matched_face_refs = _filter_face_refs_by_view(face_refs or refs, root, desired_view)

    def face_score(path: pathlib.Path) -> int:
        n = _ref_text(path, root)
        score = 0
        # View has already been hard-filtered; score only handles shot/emotion refinement within that bucket.
        mode_tags = {
            "selfie": r"自拍|selfie",
            "mirror": r"对镜|镜子|mirror|半身|全身",
            "tripod": r"三脚架|支架|tripod|全身|半身",
            "third_party": r"他拍|帮拍|街拍|candid|third",
            "surveillance": r"监控|cctv|surveillance|远景|全身",
        }
        if re.search(mode_tags.get(mode, r""), n, re.I):
            score += 6
        emotion_pairs = [
            (r"微笑|笑|smile", r"笑|微笑|开心|温柔|smile"),
            (r"生气|严肃|冷|angry|serious", r"生气|严肃|冷|不高兴|angry|serious"),
            (r"发呆|安静|走神|daze|quiet", r"发呆|安静|走神|疲惫|daze|quiet"),
        ]
        for file_pat, text_pat in emotion_pairs:
            if re.search(file_pat, n, re.I) and re.search(text_pat, text, re.I):
                score += 12
        return score

    def outfit_score(path: pathlib.Path) -> int:
        n = _ref_text(path, root)
        score = 1
        pairs = [
            (r"居家|家居|home|room", r"居家|家里|卧室|房间|室内|home|room"),
            (r"外出|通勤|街头|户外|outdoor|street|commute", r"外出|街|户外|通勤|公园|商场|outdoor|street"),
            (r"睡衣|pajama|pyjama|sleep", r"睡衣|睡觉|起床|床|夜晚|pajama|pyjama|sleep"),
            (r"夏|summer|tshirt|短袖|薄", r"夏|夏天|summer|热|短袖|t-shirt|tshirt|薄"),
            (r"冬|winter|coat|毛衣|大衣|羽绒", r"冬|冬天|winter|冷|下雪|毛衣|大衣|羽绒"),
            (r"春|spring", r"春|春天|spring"),
            (r"秋|autumn|fall", r"秋|秋天|autumn|fall"),
        ]
        for file_pat, text_pat in pairs:
            if re.search(file_pat, n, re.I) and re.search(text_pat, text, re.I):
                score += 10
        if mode == "mirror" and re.search(r"mirror|对镜|全身|半身", n, re.I):
            score += 3
        return score

    chosen: list[pathlib.Path] = []
    seed = f"{_dt.datetime.now().date()}|{mode}|{desired_view}|{text}"
    face = _rotated_best(matched_face_refs, score_fn=face_score, root=root, bucket=f"face:{desired_view}:{mode}", seed=seed)
    if face:
        chosen.append(face)
    outfit = _rotated_best(outfit_refs, score_fn=outfit_score, root=root, bucket=f"outfit:{mode}", seed=seed)
    if outfit and outfit not in chosen:
        chosen.append(outfit)
    return chosen[:2]

def _mode_camera_prompt(mode: PhotoMode) -> str:
    if mode == "selfie":
        return (
            "phone front-camera selfie taken by myself, natural arm-length perspective, "
            "slight wide-angle phone lens distortion, 24mm equivalent, f/2.2, ISO 320, 1/60s, handheld but clear"
        )
    if mode == "mirror":
        return (
            "mirror photo in a real room, phone partially visible in my hand, reflection composition, "
            "35mm equivalent, f/2.0, ISO 400, 1/80s, soft indoor light and realistic mirror reflections"
        )
    if mode == "tripod":
        return (
            "tripod/timer photo with the camera placed at chest height, stable centered framing, "
            "50mm equivalent, f/2.8, ISO 200, 1/125s, natural depth of field and clean full-body/half-body composition"
        )
    if mode == "third_party":
        return (
            "candid photo taken by another person from a natural distance, realistic street/lifestyle framing, "
            "35mm equivalent, f/2.4, ISO 250, 1/160s, slight background blur, authentic unposed moment"
        )
    if mode == "surveillance":
        return (
            "high-angle surveillance/CCTV-like still frame, fixed security camera perspective, wider field of view, "
            "28mm equivalent, f/2.8, ISO 800, 1/50s, timestamp-free, slightly compressed but clear enough, realistic overhead view"
        )
    if mode == "pov":
        return (
            "first-person POV photo from my viewpoint, phone camera realism, natural framing as if casually showing the scene, "
            "26mm equivalent, f/1.8, ISO auto, 1/100s, realistic light and depth"
        )
    return (
        "realistic phone photo, documentary lifestyle style, 35mm equivalent, f/2.2, ISO 200, 1/100s, natural light"
    )



def _hangzhou_weather_snapshot(now: _dt.datetime | None = None) -> dict[str, object]:
    """Return real date/location/weather context; never fabricate exact weather on failure."""
    now = now or _dt.datetime.now()
    snap: dict[str, object] = {
        "date_text": now.strftime("%Y-%m-%d %H:%M"),
        "location": "Hangzhou Sandun/Hangzhou, China",
        "season": current_season(now),
    }
    try:
        qs = urllib.parse.urlencode({
            "latitude": "30.319",
            "longitude": "120.087",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "timezone": "Asia/Shanghai",
        })
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{qs}", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cur = data.get("current") or {}
        for k in ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation", "wind_speed_10m"):
            if cur.get(k) is not None:
                snap[k] = cur.get(k)
    except Exception:
        snap["weather_unavailable"] = True
    return snap


def _weather_context_text(snap: dict[str, object]) -> str:
    base = f"Real-world context: date/time {snap.get('date_text')}, location {snap.get('location')}. "
    parts = []
    if snap.get("temperature_2m") is not None:
        parts.append(f"actual temperature {snap['temperature_2m']}°C")
    if snap.get("apparent_temperature") is not None:
        parts.append(f"feels like {snap['apparent_temperature']}°C")
    if snap.get("relative_humidity_2m") is not None:
        parts.append(f"humidity {snap['relative_humidity_2m']}%")
    if snap.get("precipitation") is not None:
        parts.append(f"precipitation {snap['precipitation']} mm")
    if snap.get("wind_speed_10m") is not None:
        parts.append(f"wind {snap['wind_speed_10m']} km/h")
    if parts:
        return base + "Current weather: " + ", ".join(parts) + ". Use this real weather and temperature to choose clothing; do not invent winter/snow unless requested. "
    return base + "Weather lookup unavailable, so use the real date and Hangzhou late-May/early-summer seasonal context without inventing exact temperature. "


def _weather_outfit_hint(snap: dict[str, object]) -> str:
    val = snap.get("apparent_temperature", snap.get("temperature_2m"))
    try:
        t = float(val)  # type: ignore[arg-type]
    except Exception:
        t = None
    if t is not None:
        if t >= 28:
            return "hot-weather outfit based on the actual temperature: breathable short-sleeve shirt or light T-shirt, thin fabric, relaxed collar, minimal layers"
        if t >= 24:
            return "warm humid late-spring/early-summer outfit based on the actual temperature: breathable light shirt or clean T-shirt with optional very thin overshirt, no heavy jacket"
        if t >= 18:
            return "mild-weather outfit based on the actual temperature: light long-sleeve shirt, thin cardigan or soft overshirt, easy to remove indoors"
        if t >= 10:
            return "cool-weather outfit based on the actual temperature: knit layer or hoodie under a light jacket"
        return "cold-weather outfit based on the actual temperature: warm layered knit plus coat or padded jacket"
    season = str(snap.get("season") or "spring")
    if season == "summer":
        return "season-appropriate summer outfit: breathable light shirt or simple T-shirt with a thin overshirt only if indoors with AC"
    if season == "winter":
        return "season-appropriate winter outfit: layered knit sweater or hoodie under a dark wool coat or padded jacket"
    if season == "autumn":
        return "season-appropriate autumn outfit: light trench coat or cardigan layered over a clean shirt/knit top"
    return "season-appropriate spring outfit: light jacket or soft cardigan over a clean shirt"


def _pose_action_prompt(mode: PhotoMode, request: str) -> str:
    s = request.lower()
    expression = "natural calm expression with subtle warmth, not an exaggerated model pose"
    if re.search(r"笑|开心|微笑|smile|happy", request, re.I):
        expression = "a gentle visible smile, warm and realistic without a staged model look"
    elif re.search(r"累|困|发呆|安静|疲惫|quiet|tired|daze", request, re.I):
        expression = "a quiet slightly tired mood, understated and natural"
    elif re.search(r"严肃|冷|生气|serious|angry", request, re.I):
        expression = "a cool serious mood, controlled and not melodramatic"
    pose_map = {
        "selfie": "I hold the phone naturally with one arm slightly above eye level, shoulders relaxed, body angled a few degrees instead of dead-center symmetry",
        "mirror": "I stand or lean casually before a mirror, one hand holding the phone partly visible, the other hand adjusting a sleeve or resting in a pocket",
        "tripod": "I am doing a small believable action such as fixing a cuff, holding a mug, checking a bag strap, or pausing mid-step, full body or half body in frame",
        "third_party": "I am captured mid-action by someone else: walking, turning back, looking at a menu/window, or reaching for an object, candid rather than posed",
        "surveillance": "I appear as a small figure in frame from above, walking through the space or pausing near a counter/door, body language readable but not portrait-like",
        "pov": "my hands or immediate surroundings are visible only if natural, as if I am casually showing what I am seeing at that moment",
        "object": "object-centered composition with human presence only implied unless requested",
    }
    detail = pose_map.get(mode, pose_map["third_party"])
    return f"Pose/action details: {detail}; mood/expression: {expression}; include believable body weight, hand placement, gaze direction when visible, and tiny imperfections in posture. "

def _season_outfit_prompt(season: str, mode: PhotoMode, weather: dict[str, object] | None = None) -> str:
    clothes = _weather_outfit_hint(weather or {"season": season})
    accessory = (
        "include carefully described accessories when visible: thin-framed glasses if suitable for the scene, a subtle necklace, "
        "simple watch or bracelet, coordinated colors, no exaggerated luxury branding"
    )
    if mode == "surveillance":
        accessory += "; accessories may be less prominent due to distance but should still fit the outfit"
    return clothes + "; " + accessory


def build_photo_plan(
    request: str,
    *,
    mode: PhotoMode | None = None,
    force_xinghui: bool | None = None,
    ref_dir: str | pathlib.Path = DEFAULT_REF_DIR,
    season: str | None = None,
    size: str = "1024x1024",
) -> PhotoPlan:
    request = (request or "").strip()
    if not request:
        raise ValueError("request is required")
    resolved_mode = mode or infer_mode(request)
    visible = includes_xinghui(request, resolved_mode) if force_xinghui is None else bool(force_xinghui)
    season_name = season or requested_season(request)

    weather_snapshot = _hangzhou_weather_snapshot()
    weather_context = _weather_context_text(weather_snapshot)
    pose_context = _pose_action_prompt(resolved_mode, request)
    scene_prompt = (
        f"Create a realistic lifestyle photograph based on this request: {request}. "
        f"{weather_context}"
        "Describe the environment with concrete visual details: exact location feeling, time of day, real weather influence/indoor lighting, foreground, background, "
        "small everyday objects, atmosphere, color palette, natural imperfections, fabric texture, skin texture, lens noise, and non-studio realism. "
        f"{pose_context}"
        f"Camera and composition: {_mode_camera_prompt(resolved_mode)}. "
        "Avoid text, logos, watermarks, extra fingers, distorted hands, over-smoothed skin, plastic CGI look, and anime/cartoon styling."
    )

    refs: list[pathlib.Path] = []
    if visible:
        refs = choose_reference_images(request, resolved_mode, pathlib.Path(ref_dir))
        outfit = _season_outfit_prompt(season_name, resolved_mode, weather_snapshot)
        identity = (
            "Use the uploaded reference image only for my facial identity; do not describe or reinterpret facial features in text. "
            f"First-person appearance and styling details: {outfit}. "
            "The photograph should feel like a real moment from my own life, with the selected shooting mode used naturally: "
            "selfie, mirror reflection, tripod/timer, third-party candid shot, or surveillance frame according to the request, not always the same selfie pose. "
        )
        prompt = XINGHUI_FACE_PREFIX + identity + scene_prompt
    else:
        prompt = scene_prompt

    return PhotoPlan(prompt=prompt, mode=resolved_mode, includes_xinghui=visible, reference_images=refs, size=size)


def generate_xinghui_photo(
    request: str,
    *,
    out_dir: str | pathlib.Path = DEFAULT_OUT_DIR,
    mode: PhotoMode | None = None,
    force_xinghui: bool | None = None,
    ref_dir: str | pathlib.Path = DEFAULT_REF_DIR,
    season: str | None = None,
    size: str = "1024x1024",
    n: int = 1,
) -> tuple[PhotoPlan, list[pathlib.Path]]:
    plan = build_photo_plan(
        request,
        mode=mode,
        force_xinghui=force_xinghui,
        ref_dir=ref_dir,
        season=season,
        size=size,
    )
    paths = generate_image(
        plan.prompt,
        out_dir=out_dir,
        size=plan.size,
        n=n,
        reference_images=plan.reference_images,
    )
    return plan, paths


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Plan/generate Xinghui photos via gpt-image-2")
    ap.add_argument("request", help="natural-language photo request")
    ap.add_argument("--mode", choices=["selfie", "mirror", "tripod", "third_party", "surveillance", "pov", "object"])
    ap.add_argument("--force-xinghui", action="store_true", help="force Xavier to appear and use refs")
    ap.add_argument("--no-generate", action="store_true", help="print plan only")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--size", default="1024x1024")
    args = ap.parse_args(list(argv) if argv is not None else None)

    plan = build_photo_plan(args.request, mode=args.mode, force_xinghui=True if args.force_xinghui else None, size=args.size)
    print("mode=", plan.mode)
    print("includes_xinghui=", plan.includes_xinghui)
    print("refs=", [str(p) for p in plan.reference_images])
    print("prompt=", plan.prompt)
    if args.no_generate:
        return 0
    paths = generate_image(plan.prompt, out_dir=args.out_dir, size=plan.size, reference_images=plan.reference_images)
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
