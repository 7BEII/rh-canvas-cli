from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif", ".tif", ".tiff", ".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp", ".ts"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".wma", ".aiff", ".aif", ".amr"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MEDIA_MIN_DURATION_SEC = 2.0
MEDIA_MAX_DURATION_SEC = 14.8

MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".3gp": "video/3gpp",
    ".ts": "video/mp2t",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".amr": "audio/amr",
}

EXT_BY_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/opus": ".opus",
}


def kind_for_suffix(suffix: str, mimetype: str = "") -> str:
    ext = suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    mt = (mimetype or "").split(";")[0].strip().lower()
    if mt.startswith("video/"):
        return "video"
    if mt.startswith("audio/"):
        return "audio"
    return "image"


def mime_for_suffix(suffix: str, fallback_kind: str = "image") -> str:
    ext = suffix.lower()
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(f"file{ext}")
    if guessed:
        return guessed.lower().replace("image/jpg", "image/jpeg")
    return {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mpeg",
    }.get(fallback_kind, "application/octet-stream")


def find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def probe_media_duration(file_path: Path, ffmpeg_exe: str | None = None) -> float | None:
    exe = ffmpeg_exe or find_ffmpeg()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-i", str(file_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def normalize_media_duration_file(
    file_path: Path,
    kind: str,
    *,
    min_sec: float = MEDIA_MIN_DURATION_SEC,
    max_sec: float = MEDIA_MAX_DURATION_SEC,
    output_dir: Path | None = None,
) -> tuple[Path, bool, float | None]:
    """Ensure video/audio duration falls within [min_sec, max_sec] (e.g. Seedance 2-15s limit).

    Returns (result_path, was_adjusted, duration_sec).
    """
    if kind not in ("video", "audio"):
        return file_path, False, None
    exe = find_ffmpeg()
    if not exe:
        return file_path, False, None
    dur = probe_media_duration(file_path, exe)
    if dur is None or (min_sec <= dur <= max_sec):
        return file_path, False, dur

    out_ext = ".mp4" if kind == "video" else ".mp3"
    dest_dir = output_dir or file_path.parent
    out_path = dest_dir / f"{file_path.stem}-durfix-{uuid4().hex[:6]}{out_ext}"

    if dur > max_sec:
        if kind == "video":
            cmd = [
                exe, "-y", "-i", str(file_path),
                "-t", f"{max_sec:.2f}",
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart",
                str(out_path),
            ]
        else:
            cmd = [
                exe, "-y", "-i", str(file_path),
                "-t", f"{max_sec:.2f}",
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(out_path),
            ]
    else:
        pad_to = max(min_sec + 0.1, 2.1)
        pad_extra = max(pad_to - dur + 0.2, 0.2)
        if kind == "video":
            cmd = [
                exe, "-y", "-i", str(file_path),
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad_extra:.2f}",
                "-t", f"{pad_to:.2f}",
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart",
                str(out_path),
            ]
        else:
            cmd = [
                exe, "-y", "-i", str(file_path),
                "-af", f"apad=whole_dur={pad_to:.2f}",
                "-t", f"{pad_to:.2f}",
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(out_path),
            ]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=90)
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            new_dur = probe_media_duration(out_path, exe)
            return out_path, True, new_dur
    except Exception:
        pass
    out_path.unlink(missing_ok=True)
    return file_path, False, dur


class AssetStore:
    def __init__(self, input_dir: str | Path) -> None:
        self.input_dir = Path(input_dir).expanduser().resolve()
        self.input_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, file_storage: Any) -> dict[str, Any]:
        raw_name = str(getattr(file_storage, "filename", "") or "asset.bin")
        raw_mime = str(getattr(file_storage, "mimetype", "") or "").split(";")[0].strip().lower()
        ext = Path(raw_name).suffix.lower()
        if not ext:
            ext = EXT_BY_MIME.get(raw_mime) or mimetypes.guess_extension(raw_mime) or ".png"
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", Path(raw_name).stem).strip("_") or "asset"
        stored_name = f"{safe_stem[:32]}-{uuid4().hex[:10]}{ext}"
        target = self.input_dir / stored_name
        file_storage.save(target)
        size = target.stat().st_size
        if size == 0:
            target.unlink(missing_ok=True)
            raise ValueError("上传文件内容为空")
        if size > MAX_UPLOAD_BYTES:
            target.unlink(missing_ok=True)
            raise ValueError(f"上传文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 限制")
        kind = kind_for_suffix(ext, raw_mime)
        duration_sec: float | None = None
        duration_adjusted = False
        if kind in ("video", "audio"):
            norm_path, adjusted, norm_dur = normalize_media_duration_file(
                target,
                kind,
                min_sec=MEDIA_MIN_DURATION_SEC,
                max_sec=MEDIA_MAX_DURATION_SEC,
                output_dir=self.input_dir,
            )
            duration_sec = norm_dur
            if adjusted and norm_path != target:
                new_ext = norm_path.suffix.lower()
                if new_ext == ext:
                    os.replace(norm_path, target)
                else:
                    new_stored = f"{target.stem}{new_ext}"
                    new_target = self.input_dir / new_stored
                    os.replace(norm_path, new_target)
                    target.unlink(missing_ok=True)
                    target = new_target
                    stored_name = new_stored
                    ext = new_ext
                size = target.stat().st_size
                duration_adjusted = True
        res: dict[str, Any] = {
            "kind": kind,
            "name": raw_name,
            "stored_name": stored_name,
            "url": f"/api/assets/input/{quote(stored_name)}",
            "path": str(target),
            "size": size,
            "mime": mime_for_suffix(ext, kind),
        }
        if duration_sec is not None:
            res["duration"] = round(duration_sec, 2)
        if duration_adjusted:
            res["duration_adjusted"] = True
        return res

    def resolve_input(self, asset_name: str) -> Path:
        candidate = (self.input_dir / asset_name).resolve()
        if self.input_dir not in candidate.parents and candidate != self.input_dir:
            raise ValueError("非法素材路径")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError("素材不存在")
        return candidate
