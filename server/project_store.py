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
    """Thread-safe, file-backed canvas project storage compatible with RH canvas JSON."""

    def __init__(self, directory: str | Path, *, max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.max_snapshot_bytes = int(max_snapshot_bytes)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, project_id: str) -> Path:
        if not PROJECT_ID_PATTERN.match(project_id):
            raise ValueError("无效的画布项目 ID")
        return self.directory / f"{project_id}.json"

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > self.max_snapshot_bytes + 65536:
            raise SnapshotTooLargeError("画布快照体积超过限制")
        tmp = path.with_suffix(f".json.{uuid4().hex}.tmp")
        tmp.write_bytes(encoded)
        os.replace(tmp, path)

    def _read_record(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectCorruptError(f"项目文件损坏: {path.name}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("snapshot"), dict):
            raise ProjectCorruptError(f"项目结构无效: {path.name}")
        return data

    def _summary(self, record: dict[str, Any]) -> dict[str, Any]:
        snapshot = record.get("snapshot") or {}
        return {
            "schema_version": record.get("schema_version", SCHEMA_VERSION),
            "id": record["id"],
            "name": record.get("name") or DEFAULT_PROJECT_NAME,
            "created_at": record.get("created_at") or "",
            "updated_at": record.get("updated_at") or "",
            "node_count": _count_nodes(snapshot),
            "thumbnail": _extract_thumbnail(snapshot),
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items: list[dict[str, Any]] = []
            for path in self.directory.glob("canvas-*.json"):
                try:
                    record = self._read_record(path)
                except ProjectCorruptError:
                    continue
                items.append(self._summary(record))
            items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
            return items

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(project_id)
            if not path.exists():
                raise FileNotFoundError("画布项目不存在")
            return self._read_record(path)

    def create(self, *, name: str = DEFAULT_PROJECT_NAME, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            project_id = f"canvas-{uuid4().hex}"
            now = _now_iso()
            snap = snapshot if isinstance(snapshot, dict) else _default_snapshot()
            record = {
                "schema_version": SCHEMA_VERSION,
                "id": project_id,
                "name": _sanitize_name(name),
                "created_at": now,
                "updated_at": now,
                "node_count": _count_nodes(snap),
                "snapshot": snap,
            }
            self._atomic_write(self._path(project_id), record)
            return record

    def update(
        self,
        project_id: str,
        *,
        name: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            path = self._path(project_id)
            if not path.exists():
                raise FileNotFoundError("画布项目不存在")
            record = self._read_record(path)
            if name is not None:
                record["name"] = _sanitize_name(name)
            if snapshot is not None:
                if not isinstance(snapshot, dict):
                    raise ValueError("snapshot 必须是对象")
                record["snapshot"] = snapshot
                record["node_count"] = _count_nodes(snapshot)
            record["updated_at"] = _now_iso()
            self._atomic_write(path, record)
            return record

    def duplicate(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            source = self.get(project_id)
            copy_name = f"{source.get('name') or DEFAULT_PROJECT_NAME} 副本"
            return self.create(name=copy_name, snapshot=source.get("snapshot"))

    def delete(self, project_id: str) -> None:
        with self._lock:
            path = self._path(project_id)
            if not path.exists():
                raise FileNotFoundError("画布项目不存在")
            path.unlink()
