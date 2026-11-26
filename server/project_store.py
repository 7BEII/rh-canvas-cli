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
APP_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")
SLOT_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
VALID_SLOT_KINDS = ("image", "video", "audio", "text")
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


def _cards_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    canvas = snapshot.get("canvas")
    if isinstance(canvas, dict) and isinstance(canvas.get("cards"), list):
        return [c for c in canvas["cards"] if isinstance(c, dict)]
    if isinstance(snapshot.get("cards"), list):
        return [c for c in snapshot["cards"] if isinstance(c, dict)]
    return []


def _card_primary_kind(card: dict[str, Any]) -> str:
    ctype = str(card.get("type") or "")
    if ctype == "source":
        mk = str(card.get("mediaKind") or "").lower()
        if mk in VALID_SLOT_KINDS:
            return mk
        results = card.get("results") or []
        if isinstance(results, list) and results and isinstance(results[0], dict):
            rk = str(results[0].get("kind") or "").lower()
            if rk in VALID_SLOT_KINDS:
                return rk
        return "image"
    if ctype in ("video", "transition", "videoEdit"):
        return "video"
    if ctype in ("image",):
        return "image"
    if ctype in ("audio", "ttsIndex"):
        return "audio"
    if ctype in ("prompt", "storyboard", "shotExtract"):
        return "text"
    if ctype in ("save", "gallery"):
        refs = card.get("refs") or []
        if isinstance(refs, list) and refs and isinstance(refs[0], dict):
            rk = str(refs[0].get("kind") or "").lower()
            if rk in VALID_SLOT_KINDS:
                return rk
    return "video"


def default_app_id_for_project(project_id: str) -> str:
    suffix = project_id.removeprefix("canvas-")[:8]
    return f"app-{suffix or 'online'}"


def infer_default_app_config(record: dict[str, Any]) -> dict[str, Any]:
    """Infer a ready-to-use app_config (loadimage -> save_video) from a canvas snapshot."""
    project_id = str(record.get("id") or "")
    cards = _cards_from_snapshot(record.get("snapshot"))

    # Find which cards are referenced by downstream cards (to identify sink outputs)
    referenced_ids: set[str] = set()
    for c in cards:
        for r in c.get("refs") or []:
            if isinstance(r, dict) and r.get("from"):
                referenced_ids.add(str(r["from"]))

    # 1. Pick best default input node (prefer image source node matching common subject/face keywords)
    image_sources = [c for c in cards if c.get("type") == "source" and _card_primary_kind(c) == "image"]
    chosen_in: dict[str, Any] | None = None
    priority_keywords = ("人脸", "人物", "主体", "原图", "输入", "load", "image", "face", "subject")
    for kw in priority_keywords:
        for c in image_sources:
            if kw in str(c.get("name") or "").lower():
                chosen_in = c
                break
        if chosen_in is not None:
            break
    if chosen_in is None and image_sources:
        chosen_in = image_sources[0]

    inputs: list[dict[str, Any]] = []
    if chosen_in is not None:
        inputs.append(
            {
                "key": "loadimage",
                "label": str(chosen_in.get("name") or "输入图片"),
                "node_id": str(chosen_in.get("id") or ""),
                "kind": "image",
                "required": True,
            }
        )

    # 2. Pick best default output node (prefer sink video/videoEdit/save node, then any video, then image)
    chosen_out: dict[str, Any] | None = None
    out_candidates = [c for c in cards if c.get("type") in ("save", "videoEdit", "video", "transition")]
    sink_video = [c for c in out_candidates if str(c.get("id") or "") not in referenced_ids]
    if sink_video:
        chosen_out = sink_video[-1]
    elif out_candidates:
        chosen_out = out_candidates[-1]
    else:
        img_candidates = [c for c in cards if c.get("type") == "image"]
        if img_candidates:
            chosen_out = img_candidates[-1]

    outputs: list[dict[str, Any]] = []
    if chosen_out is not None:
        out_kind = _card_primary_kind(chosen_out)
        outputs.append(
            {
                "key": "save_video" if out_kind == "video" else f"save_{out_kind}",
                "label": str(chosen_out.get("name") or ("输出视频" if out_kind == "video" else "输出产物")),
                "node_id": str(chosen_out.get("id") or ""),
                "kind": out_kind,
            }
        )

    return {
        "enabled": True,
        "app_id": default_app_id_for_project(project_id),
        "description": "",
        "inputs": inputs,
        "outputs": outputs,
    }


def normalize_app_config(raw: Any, record: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Validate and normalize app_config against the project's cards."""
    inferred = infer_default_app_config(record)
    if not isinstance(raw, dict):
        return inferred

    cards = _cards_from_snapshot(record.get("snapshot"))
    card_map = {str(c.get("id") or ""): c for c in cards if c.get("id")}

    raw_app_id = str(raw.get("app_id") or "").strip()
    if not raw_app_id:
        app_id = inferred["app_id"]
    else:
        if not APP_ID_PATTERN.match(raw_app_id):
            if strict:
                raise ValueError("AppID 仅支持字母、数字、下划线和短横线，长度 2-64 位且需以字母或数字开头")
            app_id = inferred["app_id"]
        else:
            app_id = raw_app_id

    enabled = bool(raw.get("enabled", True))
    description = str(raw.get("description") or "").strip()[:300]

    def _clean_slots(items: Any, *, is_input: bool) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return inferred["inputs"] if is_input else inferred["outputs"]
        out: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "").strip()
            if not node_id:
                continue
            if strict and card_map and node_id not in card_map:
                raise ValueError(f"映射节点不存在：{node_id}")
            raw_key = str(item.get("key") or "").strip()
            if not raw_key:
                raw_key = f"{'loadimage' if is_input else 'save_video'}_{idx + 1}" if idx > 0 else (
                    "loadimage" if is_input else "save_video"
                )
            if not SLOT_KEY_PATTERN.match(raw_key):
                if strict:
                    raise ValueError(f"映射参数名无效：{raw_key}（仅支持字母、数字、下划线和短横线）")
                continue
            key_lower = raw_key.lower()
            if key_lower in seen_keys:
                if strict:
                    raise ValueError(f"映射参数名重复：{raw_key}")
                continue
            seen_keys.add(key_lower)
            card = card_map.get(node_id)
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in VALID_SLOT_KINDS:
                kind = _card_primary_kind(card) if card else ("image" if is_input else "video")
            label = str(item.get("label") or (card.get("name") if card else "") or raw_key).strip()[:60]
            slot: dict[str, Any] = {
                "key": raw_key,
                "label": label,
                "node_id": node_id,
                "kind": kind,
            }
            if is_input:
                slot["required"] = bool(item.get("required", True))
            out.append(slot)
        return out

    inputs = _clean_slots(raw.get("inputs"), is_input=True)
    outputs = _clean_slots(raw.get("outputs"), is_input=False)
    if strict and not outputs:
        raise ValueError("请至少映射一个输出节点（例如 save_video）")

    return {
        "enabled": enabled,
        "app_id": app_id,
        "description": description,
        "inputs": inputs,
        "outputs": outputs,
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
        if data.get("stage") == STAGE_ONLINE:
            data["app_config"] = normalize_app_config(data.get("app_config"), data, strict=False)
        return data

    def _summary(self, record: dict[str, Any], *, stage: str) -> dict[str, Any]:
        snapshot = record.get("snapshot") or {}
        cover = str(record.get("cover") or "").strip()
        summary: dict[str, Any] = {
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
        if stage == STAGE_ONLINE:
            summary["app_config"] = normalize_app_config(record.get("app_config"), record, strict=False)
        return summary

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
