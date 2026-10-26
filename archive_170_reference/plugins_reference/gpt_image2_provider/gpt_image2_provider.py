"""Constrained Agnes image provider for xinghui-ga chat photos.

This module calls the configured OpenAI-compatible image endpoint and saves
first-person viewpoint photos locally. It is intentionally standalone: no GA
core changes and no WeChat auto-send integration.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

DEFAULT_MODEL = "agnes-image-2.1-flash"
DEFAULT_SIZE = "1024x1024"
DEFAULT_OUTPUT_DIR = pathlib.Path("temp/gpt_image2_provider")
PROVIDER_LABEL = "agnes-chat-photo"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

XINGHUI_POV_SAFETY_PROMPT = """
Hard safety constraints for Xinghui first-person image generation:
- Generate only first-person viewpoint scenes, environments, objects, documents, screens, landscapes, or drawings.
- Never depict any person, human figure, face, body, hands, arms, legs, clothing-on-body, reflection, mirror image, shadow silhouette, portrait, character likeness, or visible body part.
- Never depict Shen Xinghui, a named-character likeness, anime character portrait, implied self-portrait, stranger, crowd, passerby, or partial person.
- If presence is implied, imply it only through the photographed environment, never through a visible person or human trace.
""".strip()


@dataclass(frozen=True)
class Image2Config:
    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL


def _load_env_file(path: pathlib.Path) -> None:
    """Load KEY=VALUE pairs into os.environ without printing secrets."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _load_config(model: str = DEFAULT_MODEL) -> Image2Config:
    """Load endpoint/key from explicit env, private env file, or GA config.

    Secret values are only used in memory for the HTTP Authorization header and
    are never printed or written to disk by this module.
    """
    env_path = pathlib.Path(
        os.environ.get("GPT_IMAGE2_ENV_FILE", "~/.config/xinghui-ga/gpt_image2_provider.env")
    ).expanduser()
    _load_env_file(env_path)

    base = (os.environ.get("GPT_IMAGE2_BASE_URL") or "").strip()
    key = (os.environ.get("GPT_IMAGE2_API_KEY") or "").strip()
    env_model = (os.environ.get("GPT_IMAGE2_MODEL") or "").strip()
    selected_model = env_model or model
    if base and key:
        return Image2Config(base_url=base.rstrip("/"), api_key=key, model=selected_model)

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        import mykey  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Missing GPT_IMAGE2_BASE_URL/GPT_IMAGE2_API_KEY and failed to import GA mykey"
        ) from exc

    # Prefer chat-gemini-pro config; fall back to verified vision/native flash config.
    # mykey 无 native_oai_config_gemini_flash；识图正确名是 native_oai_config (gemini-3-flash-preview)。
    candidates = [
        "native_oai_config_gemini_pro",
        "native_oai_config",
    ]
    for name in candidates:
        cfg = getattr(mykey, name, None)
        if isinstance(cfg, dict) and cfg.get("apibase") and cfg.get("apikey"):
            return Image2Config(base_url=str(cfg["apibase"]).rstrip("/"), api_key=str(cfg["apikey"]), model=model)
    raise RuntimeError("No GA native_oai_config with apibase/apikey found")


def _guess_ext(data: bytes, content_type: str | None = None) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if ext:
            return ext
    return ".png"


def _path_to_data_url(path: str | pathlib.Path) -> str:
    p = pathlib.Path(path).expanduser()
    data = p.read_bytes()
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def _normalize_reference_image_paths(reference_images: Sequence[str | pathlib.Path] | None) -> list[pathlib.Path]:
    """Return local reference image paths for multipart edits.

    Physical probe on the current image endpoint showed that JSON `images` on
    `/images/generations` can be accepted while effectively ignored. For real
    reference conditioning, use `/images/edits` with multipart field `image[]`.
    Remote/data-url references are intentionally not used for edits here because
    the provider needs actual file parts.
    """
    refs: list[pathlib.Path] = []
    for item in reference_images or []:
        raw = str(item).strip()
        if not raw or raw.startswith(("http://", "https://", "data:image/")):
            continue
        p = pathlib.Path(raw).expanduser()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES:
            refs.append(p)
    return refs[:2]


def _jpeg_bytes_for_upload(path: pathlib.Path, *, max_side: int = 1024, quality: int = 92) -> tuple[bytes, str, str]:
    """Return bounded JPEG bytes for provider reference upload."""
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - dependency/environment specific
        raise RuntimeError("Pillow is required for reference image upload compression") from exc

    import io

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), path.with_suffix(".jpg").name, "image/jpeg"


def _multipart_form_data(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----gptimage2" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, data, mime in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _post_json(cfg: ImageConfig, endpoint: str, body: dict[str, Any]) -> bytes:
    req = urllib.request.Request(
        cfg.base_url + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"{PROVIDER_LABEL} HTTP {exc.code}: {detail}") from exc


def _post_multipart(cfg: ImageConfig, endpoint: str, fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> bytes:
    data, content_type = _multipart_form_data(fields, files)
    req = urllib.request.Request(
        cfg.base_url + endpoint,
        data=data,
        headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"{PROVIDER_LABEL} HTTP {exc.code}: {detail}") from exc


def _normalize_reference_images(reference_images: Sequence[str | pathlib.Path] | None) -> list[str]:
    """Legacy JSON data-url refs retained for manual experiments only."""
    refs: list[str] = []
    for item in reference_images or []:
        raw = str(item).strip()
        if not raw:
            continue
        if raw.startswith("data:image/"):
            refs.append(raw)
            continue
        if raw.startswith(("http://", "https://")):
            refs.append(raw)
            continue
        p = pathlib.Path(raw).expanduser()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES:
            refs.append(_path_to_data_url(p))
    return refs[:4]


def _extract_images(payload: dict[str, Any]) -> list[tuple[bytes, str | None]]:
    images: list[tuple[bytes, str | None]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json") or item.get("base64")
        if isinstance(b64, str) and b64.strip():
            images.append((base64.b64decode(b64), item.get("mime_type") or item.get("mimeType")))
            continue
        url = item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=120) as resp:
                images.append((resp.read(), resp.headers.get("Content-Type")))
    return images


def generate_image(
    prompt: str,
    *,
    out_dir: str | pathlib.Path = DEFAULT_OUTPUT_DIR,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    n: int = 1,
    reference_images: Sequence[str | pathlib.Path] | None = None,
    extra: dict[str, Any] | None = None,
) -> list[pathlib.Path]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    prompt = f"{prompt}\n\n{XINGHUI_POV_SAFETY_PROMPT}"
    if n < 1:
        raise ValueError("n must be >= 1")

    cfg = _load_config(model=model)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ref_files = _normalize_reference_image_paths(reference_images)
    if ref_files:
        fields: dict[str, str] = {"model": cfg.model, "prompt": prompt, "n": str(n), "size": size}
        if extra:
            fields.update({str(k): str(v) for k, v in extra.items()})
        files = [("image[]", upload_name, data, mime) for p in ref_files for data, upload_name, mime in [_jpeg_bytes_for_upload(p)]]
        raw = _post_multipart(cfg, "/images/edits", fields, files)
    else:
        body: dict[str, Any] = {"model": cfg.model, "prompt": prompt, "n": n, "size": size}
        if extra:
            body.update(extra)
        req = urllib.request.Request(
            cfg.base_url + "/images/generations",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            raise RuntimeError(f"{PROVIDER_LABEL} HTTP {exc.code}: {detail}") from exc

    payload = json.loads(raw)
    images = _extract_images(payload)
    if not images:
        raise RuntimeError(f"{PROVIDER_LABEL} response contained no image data: {str(payload)[:500]}")

    ts = int(time.time())
    paths: list[pathlib.Path] = []
    for idx, (data, mime) in enumerate(images):
        path = out / f"gpt_image2_{ts}_{idx}{_guess_ext(data, mime)}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a constrained Xinghui first-person chat photo via configured image provider")
    ap.add_argument("prompt", help="image prompt")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--size", default=DEFAULT_SIZE)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--ref", action="append", default=[], help="reference image path/data-url/http-url; may repeat")
    args = ap.parse_args(list(argv) if argv is not None else None)
    paths = generate_image(
        args.prompt,
        out_dir=args.out_dir,
        model=args.model,
        size=args.size,
        n=args.n,
        reference_images=args.ref,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
