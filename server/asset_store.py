from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
MAX_UPLOAD_BYTES = 120 * 1024 * 1024


def kind_for_suffix(suffix: str) -> str:
    ext = suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "image"


class AssetStore:
    def __init__(self, input_dir: str | Path) -> None:
        self.input_dir = Path(input_dir).expanduser().resolve()
        self.input_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, file_storage: Any) -> dict[str, Any]:
        raw_name = str(getattr(file_storage, "filename", "") or "asset.bin")
        ext = Path(raw_name).suffix.lower()
        if not ext:
            guessed = mimetypes.guess_extension(getattr(file_storage, "mimetype", "") or "")
            ext = guessed or ".png"
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", Path(raw_name).stem).strip("_") or "asset"
        stored_name = f"{safe_stem[:32]}-{uuid4().hex[:10]}{ext}"
        target = self.input_dir / stored_name
        file_storage.save(target)
        size = target.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            target.unlink(missing_ok=True)
            raise ValueError("上传文件超过 120MB 限制")
        return {
            "kind": kind_for_suffix(ext),
            "name": raw_name,
            "stored_name": stored_name,
            "url": f"/api/assets/input/{quote(stored_name)}",
            "path": str(target),
        }

    def resolve_input(self, asset_name: str) -> Path:
        candidate = (self.input_dir / asset_name).resolve()
        if self.input_dir not in candidate.parents and candidate != self.input_dir:
            raise ValueError("非法素材路径")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError("素材不存在")
        return candidate
