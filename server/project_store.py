from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
DEFAULT_PROJECT_NAME = "未命名画布"
MAX_PROJECT_NAME_LENGTH = 80
DEFAULT_MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024
PROJECT_ID_PATTERN = re.compile(r"^canvas-[0-9a-f]{32}$")
STAGE_ONLINE = "online"
STAGE_TEST = "test"
VALID_STAGES = (STAGE_ONLINE, STAGE_TEST)


def normalize_stage(raw: Any, *, default: str = STAGE_TEST, strict: bool = False) -> str:
    val = str(raw or "").strip().lower()
    if val in VALID_STAGES:
        return val
    if not val and not strict:
        return default
    if strict:
        raise ValueError("无效的画布分区，仅支持 online 或 test")
    return default


class ProjectCorruptError(ValueError):
    """Raised when a project file exists but is not valid JSON."""


class SnapshotTooLargeError(ValueError):
    """Raised when a snapshot exceeds the configured byte cap."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_name(raw: Any) -> str:
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    if not text:
        return DEFAULT_PROJECT_NAME
    return text[:MAX_PROJECT_NAME_LENGTH]


def _default_snapshot() -> dict[str, Any]:
    return {
        "format": "rh-canvas",
        "version": 1,
        "exportedAt": _now_iso(),
        "schemaVersion": SCHEMA_VERSION,
        "canvas": {
            "cards": [],
            "seq": 1,
            "viewport": {"x": 0, "y": 0, "z": 1},
        },
    }


def _count_nodes(snapshot: dict[str, Any]) -> int:
    canvas = snapshot.get("canvas") if isinstance(snapshot, dict) else None
    if isinstance(canvas, dict) and isinstance(canvas.get("cards"), list):
        return len(canvas["cards"])
    if isinstance(snapshot.get("cards"), list):
        return len(snapshot["cards"])
    return 0


def _extract_thumbnail(snapshot: dict[str, Any]) -> str:
    canvas = snapshot.get("canvas") if isinstance(snapshot, dict) else None
    cards = canvas.get("cards") if isinstance(canvas, dict) else []
    if not isinstance(cards, list):
        return ""
    for card in reversed(cards):
        results = card.get("results") if isinstance(card, dict) else None
        if not isinstance(results, list):
            continue
        for item in reversed(results):
            if isinstance(item, dict) and item.get("kind") == "image":
                url = item.get("url") or item.get("dataUrl") or ""
                if isinstance(url, str) and url:
                    return url
    return ""


class ProjectStore:
    """Thread-safe, file-backed canvas project storage with separate online/test folders."""

    def __init__(self, directory: str | Path, *, max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.online_dir = self.directory / STAGE_ONLINE
        self.test_dir = self.directory / STAGE_TEST
        self.max_snapshot_bytes = int(max_snapshot_bytes)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.online_dir.mkdir(parents=True, exist_ok=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate_legacy_files()

    def _migrate_legacy_files(self) -> None:
        """Move any legacy root-level canvas-*.json files into the test/ folder."""
        with self._lock:
            for legacy in self.directory.glob("canvas-*.json"):
                if not legacy.is_file():
                    continue
                if not PROJECT_ID_PATTERN.match(legacy.stem):
                    continue
                if (self.online_dir / legacy.name).exists() or (self.test_dir / legacy.name).exists():
                    continue
                try:
                    os.replace(legacy, self.test_dir / legacy.name)
                except OSError:
                    continue

    def _dir_for_stage(self, stage: str) -> Path:
        norm = normalize_stage(stage, strict=True)
        return self.online_dir if norm == STAGE_ONLINE else self.test_dir

    def _path_for_stage(self, project_id: str, stage: str) -> Path:
        if not PROJECT_ID_PATTERN.match(project_id):
            raise ValueError("无效的画布项目 ID")
        return self._dir_for_stage(stage) / f"{project_id}.json"

    def _locate(self, project_id: str) -> tuple[str, Path]:
        if not PROJECT_ID_PATTERN.match(project_id):
            raise ValueError("无效的画布项目 ID")
        online_path = self.online_dir / f"{project_id}.json"
        if online_path.exists():
            return STAGE_ONLINE, online_path
        test_path = self.test_dir / f"{project_id}.json"
        if test_path.exists():
            return STAGE_TEST, test_path
        raise FileNotFoundError("画布项目不存在")

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > self.max_snapshot_bytes + 65536:
            raise SnapshotTooLargeError("画布快照体积超过限制")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".json.{uuid4().hex}.tmp")
        tmp.write_bytes(encoded)
        os.replace(tmp, path)

    def _read_record(self, path: Path, *, stage: str | None = None) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectCorruptError(f"项目文件损坏: {path.name}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("snapshot"), dict):
            raise ProjectCorruptError(f"项目结构无效: {path.name}")
        if stage in VALID_STAGES:
            data["stage"] = stage
        return data

    def _summary(self, record: dict[str, Any], *, stage: str) -> dict[str, Any]:
        snapshot = record.get("snapshot") or {}
        cover = str(record.get("cover") or "").strip()
        return {
            "schema_version": record.get("schema_version", SCHEMA_VERSION),
            "id": record["id"],
            "name": record.get("name") or DEFAULT_PROJECT_NAME,
            "stage": stage,
            "created_at": record.get("created_at") or "",
            "updated_at": record.get("updated_at") or "",
            "node_count": _count_nodes(snapshot),
            "thumbnail": cover or _extract_thumbnail(snapshot),
            "has_custom_cover": bool(cover),
        }

    def list(self, *, stage: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            stages = (normalize_stage(stage, strict=True),) if stage else VALID_STAGES
            items: list[dict[str, Any]] = []
            for st in stages:
                folder = self._dir_for_stage(st)
                if not folder.exists():
                    continue
                for path in folder.glob("canvas-*.json"):
                    try:
                        record = self._read_record(path, stage=st)
                    except ProjectCorruptError:
                        continue
                    items.append(self._summary(record, stage=st))
            items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
            return items

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            stage, path = self._locate(project_id)
            return self._read_record(path, stage=stage)

    def create(
        self,
        *,
        name: str = DEFAULT_PROJECT_NAME,
        snapshot: dict[str, Any] | None = None,
        stage: str = STAGE_TEST,
        cover: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            norm_stage = normalize_stage(stage, default=STAGE_TEST)
            project_id = f"canvas-{uuid4().hex}"
            now = _now_iso()
            snap = snapshot if isinstance(snapshot, dict) else _default_snapshot()
            record: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "id": project_id,
                "name": _sanitize_name(name),
                "stage": norm_stage,
                "created_at": now,
                "updated_at": now,
                "node_count": _count_nodes(snap),
                "snapshot": snap,
            }
            clean_cover = str(cover or "").strip()
            if clean_cover:
                record["cover"] = clean_cover
            self._atomic_write(self._path_for_stage(project_id, norm_stage), record)
            return record

    def update(
        self,
        project_id: str,
        *,
        name: str | None = None,
        snapshot: dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            stage, path = self._locate(project_id)
            record = self._read_record(path, stage=stage)
            if name is not None:
                record["name"] = _sanitize_name(name)
            if snapshot is not None:
                if not isinstance(snapshot, dict):
                    raise ValueError("snapshot 必须是对象")
                record["snapshot"] = snapshot
                record["node_count"] = _count_nodes(snapshot)
            if cover is not None:
                clean_cover = str(cover).strip()
                if clean_cover:
                    record["cover"] = clean_cover
                else:
                    record.pop("cover", None)
            record["stage"] = stage
            record["updated_at"] = _now_iso()
            self._atomic_write(path, record)
            return record

    def move_stage(self, project_id: str, target_stage: str) -> dict[str, Any]:
        with self._lock:
            norm_target = normalize_stage(target_stage, strict=True)
            current_stage, src_path = self._locate(project_id)
            record = self._read_record(src_path, stage=current_stage)
            if current_stage == norm_target:
                return record
            record["stage"] = norm_target
            record["updated_at"] = _now_iso()
            dst_path = self._path_for_stage(project_id, norm_target)
            self._atomic_write(dst_path, record)
            if src_path.exists() and src_path != dst_path:
                src_path.unlink()
            return record

    def duplicate(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            source = self.get(project_id)
            copy_name = f"{source.get('name') or DEFAULT_PROJECT_NAME} 副本"
            return self.create(
                name=copy_name,
                snapshot=source.get("snapshot"),
                stage=STAGE_TEST,
                cover=str(source.get("cover") or ""),
            )

    def delete(self, project_id: str) -> None:
        with self._lock:
            _, path = self._locate(project_id)
            path.unlink()
