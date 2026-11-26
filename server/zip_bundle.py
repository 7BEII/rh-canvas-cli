from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from .asset_store import AssetStore, kind_for_suffix
from .project_store import ProjectStore
from .settings_store import SettingsStore

MAX_ZIP_FILES = 500
MAX_ZIP_TOTAL_BYTES = 1024 * 1024 * 1024  # 1 GB uncompressed safety cap


def _safe_filename(raw: str, fallback: str = "file") -> str:
    stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff.]+", "_", Path(raw).name).strip("._")
    return stem[:80] if stem else fallback


def _resolve_local_media(
    item: dict[str, Any],
    assets: AssetStore,
    output_dir: Path,
) -> Path | None:
    """Safely resolve a result/localMedia item to an existing file under data/assets or data/outputs."""
    url = str(item.get("url") or "").strip()
    if url.startswith("/api/assets/input/"):
        name = unquote(url.removeprefix("/api/assets/input/"))
        try:
            return assets.resolve_input(name)
        except Exception:
            pass
    elif url.startswith("/api/outputs/"):
        rel = unquote(url.removeprefix("/api/outputs/"))
        cand = (output_dir / rel).resolve()
        if (output_dir in cand.parents or cand == output_dir) and cand.exists() and cand.is_file():
            return cand

    raw_path = str(item.get("path") or "").strip()
    if raw_path:
        try:
            cand = Path(raw_path).resolve()
            if cand.exists() and cand.is_file():
                in_assets = assets.input_dir in cand.parents or cand == assets.input_dir
                in_outputs = output_dir in cand.parents or cand == output_dir
                if in_assets or in_outputs:
                    return cand
        except Exception:
            pass
    return None


def export_project_zip(
    project_id: str,
    *,
    projects: ProjectStore,
    assets: AssetStore,
    settings: SettingsStore,
) -> tuple[io.BytesIO, str]:
    """Bundle a canvas project JSON + all referenced local assets/outputs into a portable ZIP."""
    record = projects.get(project_id)
    snapshot = copy.deepcopy(record.get("snapshot") or {})
    output_dir = settings.output_dir()

    cards = (snapshot.get("canvas") or {}).get("cards")
    if not isinstance(cards, list):
        cards = []

    path_to_arc: dict[Path, str] = {}
    used_arc_names: set[str] = set()

    def register_file(real_path: Path) -> str:
        resolved = real_path.resolve()
        if resolved in path_to_arc:
            return path_to_arc[resolved]
        base = _safe_filename(resolved.name, "asset.bin")
        stem = Path(base).stem
        ext = Path(base).suffix
        candidate = f"assets/{base}"
        idx = 2
        while candidate in used_arc_names:
            candidate = f"assets/{stem}-{idx}{ext}"
            idx += 1
        used_arc_names.add(candidate)
        path_to_arc[resolved] = candidate
        return candidate

    def rewrite_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            real_path = _resolve_local_media(item, assets, output_dir)
            if real_path is None:
                continue
            arc_name = register_file(real_path)
            item["url"] = f"zip://{arc_name}"
            item.pop("path", None)

    for card in cards:
        if not isinstance(card, dict):
            continue
        rewrite_list(card.get("results"))
        rewrite_list(card.get("localMedia"))

    cover_url = str(record.get("cover") or "").strip()
    if cover_url:
        cover_path = _resolve_local_media({"url": cover_url}, assets, output_dir)
        if cover_path is not None:
            cover_url = f"zip://{register_file(cover_path)}"

    manifest = {
        "schema_version": 1,
        "format": "rh-canvas-zip",
        "id": record.get("id"),
        "name": record.get("name") or "未命名画布",
        "cover": cover_url,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "asset_count": len(path_to_arc),
        "snapshot": snapshot,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "canvas.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for real_path, arc_name in path_to_arc.items():
            # Already-compressed media stored directly for fast packing
            zf.write(real_path, arcname=arc_name, compress_type=zipfile.ZIP_STORED)

    buf.seek(0)
    proj_name = re.sub(r'[\\/:*?"<>|]+', "_", str(record.get("name") or "canvas")).strip() or "canvas"
    return buf, f"{proj_name}.zip"


def _normalize_zip_member(name: str) -> str | None:
    clean = name.replace("\\", "/").lstrip("/")
    if not clean or clean.endswith("/"):
        return None
    parts = clean.split("/")
    if any(p in ("..", "") for p in parts):
        return None
    if parts[0] == "__MACOSX":
        return None
    return "/".join(parts)


def import_project_zip(
    file_stream: Any,
    *,
    filename: str,
    projects: ProjectStore,
    assets: AssetStore,
) -> dict[str, Any]:
    """Import a ZIP archive containing canvas.json + assets/* into a new project."""
    try:
        zf = zipfile.ZipFile(file_stream, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("无效的 ZIP 压缩包文件") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP 内文件数量超过限制（最多 {MAX_ZIP_FILES} 个）")

        total_bytes = sum(max(0, info.file_size) for info in infos)
        if total_bytes > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("ZIP 解压体积超过 1GB 限制")

        members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            norm = _normalize_zip_member(info.filename)
            if norm:
                members[norm] = info

        # Find canvas.json (prefer root canvas.json, otherwise shortest *.json)
        json_key = "canvas.json" if "canvas.json" in members else None
        if not json_key:
            json_candidates = sorted(
                (k for k in members if k.lower().endswith(".json")),
                key=lambda k: (k.count("/"), len(k)),
            )
            if json_candidates:
                json_key = json_candidates[0]
        if not json_key:
            raise ValueError("ZIP 包中未找到 canvas.json 画布文件")

        try:
            raw = json.loads(zf.read(members[json_key]).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"画布 JSON 解析失败：{exc}") from exc

        default_name = Path(filename or "导入画布").stem or "导入画布"
        project_name = default_name
        if isinstance(raw, dict) and isinstance(raw.get("snapshot"), dict):
            snapshot = raw["snapshot"]
            if raw.get("name"):
                project_name = str(raw["name"])
        elif isinstance(raw, dict) and isinstance((raw.get("canvas") or {}).get("cards"), list):
            snapshot = raw
            if raw.get("name"):
                project_name = str(raw["name"])
        elif isinstance(raw, dict) and isinstance(raw.get("cards"), list):
            snapshot = {
                "format": "rh-canvas",
                "version": 1,
                "exportedAt": datetime.now(timezone.utc).isoformat(),
                "schemaVersion": 1,
                "canvas": {
                    "cards": raw["cards"],
                    "seq": len(raw["cards"]) + 1,
                    "viewport": raw.get("viewport") or {"x": 60, "y": 60, "z": 0.9},
                },
            }
        else:
            raise ValueError("ZIP 中的 JSON 格式不兼容，缺少 canvas.cards 字段")

        # Extract all non-JSON media members into data/assets/
        extracted_by_arc: dict[str, dict[str, str]] = {}
        extracted_by_basename: dict[str, dict[str, str]] = {}
        for norm, info in members.items():
            if norm == json_key:
                continue
            if info.file_size <= 0:
                continue
            orig_name = Path(norm).name
            ext = Path(orig_name).suffix.lower() or ".bin"
            safe_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", Path(orig_name).stem).strip("_") or "asset"
            stored_name = f"{safe_stem[:32]}-{uuid4().hex[:10]}{ext}"
            target = assets.input_dir / stored_name
            with zf.open(info, "r") as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 256)
                    if not chunk:
                        break
                    dst.write(chunk)
            entry = {
                "kind": kind_for_suffix(ext),
                "url": f"/api/assets/input/{quote(stored_name)}",
                "path": str(target),
                "stored_name": stored_name,
            }
            extracted_by_arc[norm] = entry
            extracted_by_basename.setdefault(orig_name, entry)

        # Rewrite zip:// URLs (and fallback basename matches) back to local /api/assets/input/...
        cards = (snapshot.get("canvas") or {}).get("cards")
        if isinstance(cards, list):
            for card in cards:
                if not isinstance(card, dict):
                    continue
                for field in ("results", "localMedia"):
                    items = card.get(field)
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        url = str(item.get("url") or "").strip()
                        matched: dict[str, str] | None = None
                        if url.startswith("zip://"):
                            arc_key = url.removeprefix("zip://").replace("\\", "/").lstrip("/")
                            matched = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
                        elif url.startswith(("/api/assets/input/", "/api/outputs/")):
                            base = unquote(Path(url).name)
                            matched = extracted_by_basename.get(base)
                        if matched:
                            item["url"] = matched["url"]
                            item["path"] = matched["path"]

        cover_url = str(raw.get("cover") or "").strip() if isinstance(raw, dict) else ""
        if cover_url.startswith("zip://"):
            arc_key = cover_url.removeprefix("zip://").replace("\\", "/").lstrip("/")
            matched_cover = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
            if matched_cover:
                cover_url = matched_cover["url"]
        elif cover_url.startswith(("/api/assets/input/", "/api/outputs/")):
            matched_cover = extracted_by_basename.get(unquote(Path(cover_url).name))
            if matched_cover:
                cover_url = matched_cover["url"]

        record = projects.create(name=project_name, snapshot=snapshot, cover=cover_url)
        return {
            "success": True,
            "mode": "project",
            "project": record,
            "imported_assets": len(extracted_by_arc),
        }


def import_canvas_zip(
    file_stream: Any,
    *,
    filename: str,
    projects: ProjectStore,
    assets: AssetStore,
) -> dict[str, Any]:
    """Import exported canvas ZIP (single canvas.json or batch workspace.json) into test stage without touching settings or overwriting existing canvases."""
    raw_bytes = file_stream.read()
    mem = io.BytesIO(raw_bytes)
    try:
        zf = zipfile.ZipFile(mem, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("无效的 ZIP 压缩包文件") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES * 4:
            raise ValueError(f"ZIP 内文件数量超过限制（最多 {MAX_ZIP_FILES * 4} 个）")
        total_bytes = sum(max(0, info.file_size) for info in infos)
        if total_bytes > MAX_ZIP_TOTAL_BYTES * 2:
            raise ValueError("ZIP 解压体积超过 2GB 限制")

        members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            norm = _normalize_zip_member(info.filename)
            if norm:
                members[norm] = info

        # Find workspace.json (root or nested)
        ws_key = "workspace.json" if "workspace.json" in members else None
        if not ws_key:
            ws_candidates = sorted(
                (k for k in members if Path(k).name.lower() == "workspace.json"),
                key=lambda k: (k.count("/"), len(k)),
            )
            if ws_candidates:
                ws_key = ws_candidates[0]

        if not ws_key:
            # Single-canvas ZIP (canvas.json + assets/*)
            return import_project_zip(
                io.BytesIO(raw_bytes),
                filename=filename,
                projects=projects,
                assets=assets,
            )

        try:
            manifest = json.loads(zf.read(members[ws_key]).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"画布包清单 workspace.json 解析失败：{exc}") from exc

        if not isinstance(manifest, dict) or not isinstance(manifest.get("projects"), list):
            raise ValueError("ZIP 清单格式不兼容，缺少 projects 列表")

        # Extract all media assets into data/assets/
        extracted_by_arc: dict[str, dict[str, str]] = {}
        extracted_by_basename: dict[str, dict[str, str]] = {}
        for norm, info in members.items():
            if norm == ws_key or info.file_size <= 0:
                continue
            orig_name = Path(norm).name
            ext = Path(orig_name).suffix.lower() or ".bin"
            safe_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", Path(orig_name).stem).strip("_") or "asset"
            reuse_cand = assets.input_dir / orig_name
            if reuse_cand.exists() and reuse_cand.is_file() and reuse_cand.stat().st_size == info.file_size:
                stored_name = orig_name
                target = reuse_cand
            else:
                stored_name = f"{safe_stem[:32]}-{uuid4().hex[:10]}{ext}"
                target = assets.input_dir / stored_name
                with zf.open(info, "r") as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 256)
                        if not chunk:
                            break
                        dst.write(chunk)
            entry = {
                "kind": kind_for_suffix(ext),
                "url": f"/api/assets/input/{quote(stored_name)}",
                "path": str(target),
                "stored_name": stored_name,
            }
            extracted_by_arc[norm] = entry
            extracted_by_basename.setdefault(orig_name, entry)

        created_records: list[dict[str, Any]] = []
        for item in manifest.get("projects") or []:
            if not isinstance(item, dict):
                continue
            snapshot = item.get("snapshot")
            if not isinstance(snapshot, dict):
                continue
            cards = (snapshot.get("canvas") or {}).get("cards")
            if isinstance(cards, list):
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    for field in ("results", "localMedia"):
                        media_items = card.get(field)
                        if not isinstance(media_items, list):
                            continue
                        for m in media_items:
                            if not isinstance(m, dict):
                                continue
                            url = str(m.get("url") or "").strip()
                            matched: dict[str, str] | None = None
                            if url.startswith("zip://"):
                                arc_key = url.removeprefix("zip://").replace("\\", "/").lstrip("/")
                                matched = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
                            elif url.startswith(("/api/assets/input/", "/api/outputs/")):
                                base = unquote(Path(url).name)
                                matched = extracted_by_basename.get(base)
                            if matched:
                                m["url"] = matched["url"]
                                m["path"] = matched["path"]

            pname = str(item.get("name") or "导入画布")
            pcover = str(item.get("cover") or "").strip()
            if pcover.startswith("zip://"):
                arc_key = pcover.removeprefix("zip://").replace("\\", "/").lstrip("/")
                matched_cover = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
                if matched_cover:
                    pcover = matched_cover["url"]
            elif pcover.startswith(("/api/assets/input/", "/api/outputs/")):
                matched_cover = extracted_by_basename.get(unquote(Path(pcover).name))
                if matched_cover:
                    pcover = matched_cover["url"]

            # Always create into test stage so importing never overwrites online/existing canvases
            rec = projects.create(name=pname, snapshot=snapshot, stage="test", cover=pcover)
            created_records.append(rec)

        if len(created_records) == 1:
            return {
                "success": True,
                "mode": "project",
                "project": created_records[0],
                "projects": created_records,
                "imported_projects": 1,
                "imported_assets": len(extracted_by_arc),
            }
        return {
            "success": True,
            "mode": "batch",
            "projects": created_records,
            "imported_projects": len(created_records),
            "imported_assets": len(extracted_by_arc),
        }


SECRET_SETTINGS_KEYS = (
    "ark_api_key",
    "volc_ak",
    "volc_sk",
    "runninghub_api_key",
    "tensor_api_key",
    "llm_api_key",
)


def export_workspace_zip(
    *,
    projects: ProjectStore,
    assets: AssetStore,
    settings: SettingsStore,
    include_secrets: bool = True,
) -> tuple[io.BytesIO, str]:
    """Bundle ALL canvas projects + referenced local assets + settings.json into one migration ZIP."""
    output_dir = settings.output_dir()
    cfg = copy.deepcopy(settings.load())
    cfg.pop("output_dir", None)
    if not include_secrets:
        for k in SECRET_SETTINGS_KEYS:
            cfg[k] = ""

    path_to_arc: dict[Path, str] = {}
    used_arc_names: set[str] = set()

    def register_file(real_path: Path) -> str:
        resolved = real_path.resolve()
        if resolved in path_to_arc:
            return path_to_arc[resolved]
        base = _safe_filename(resolved.name, "asset.bin")
        stem = Path(base).stem
        ext = Path(base).suffix
        candidate = f"assets/{base}"
        idx = 2
        while candidate in used_arc_names:
            candidate = f"assets/{stem}-{idx}{ext}"
            idx += 1
        used_arc_names.add(candidate)
        path_to_arc[resolved] = candidate
        return candidate

    def rewrite_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            real_path = _resolve_local_media(item, assets, output_dir)
            if real_path is None:
                continue
            arc_name = register_file(real_path)
            item["url"] = f"zip://{arc_name}"
            item.pop("path", None)

    exported_projects: list[dict[str, Any]] = []
    for summary in projects.list():
        pid = str(summary.get("id") or "")
        if not pid:
            continue
        try:
            record = projects.get(pid)
        except Exception:
            continue
        snapshot = copy.deepcopy(record.get("snapshot") or {})
        cards = (snapshot.get("canvas") or {}).get("cards")
        if isinstance(cards, list):
            for card in cards:
                if not isinstance(card, dict):
                    continue
                rewrite_list(card.get("results"))
                rewrite_list(card.get("localMedia"))
        cover_url = str(record.get("cover") or "").strip()
        if cover_url:
            cover_path = _resolve_local_media({"url": cover_url}, assets, output_dir)
            if cover_path is not None:
                cover_url = f"zip://{register_file(cover_path)}"
        exported_projects.append(
            {
                "id": record.get("id"),
                "name": record.get("name") or "未命名画布",
                "stage": record.get("stage") or summary.get("stage") or "test",
                "cover": cover_url,
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "snapshot": snapshot,
            }
        )

    manifest = {
        "schema_version": 1,
        "format": "rh-canvas-workspace-zip",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "include_secrets": bool(include_secrets),
        "project_count": len(exported_projects),
        "asset_count": len(path_to_arc),
        "settings": cfg,
        "projects": exported_projects,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "workspace.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for real_path, arc_name in path_to_arc.items():
            zf.write(real_path, arcname=arc_name, compress_type=zipfile.ZIP_STORED)

    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return buf, f"rh-canvas-migration-{stamp}.zip"


def import_workspace_zip(
    file_stream: Any,
    *,
    filename: str,
    projects: ProjectStore,
    assets: AssetStore,
    settings: SettingsStore,
) -> dict[str, Any]:
    """Import a full workspace migration ZIP (workspace.json) or fall back to single-canvas ZIP."""
    raw_bytes = file_stream.read()
    mem = io.BytesIO(raw_bytes)
    try:
        zf = zipfile.ZipFile(mem, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("无效的 ZIP 压缩包文件") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES * 4:
            raise ValueError(f"ZIP 内文件数量超过限制（最多 {MAX_ZIP_FILES * 4} 个）")
        total_bytes = sum(max(0, info.file_size) for info in infos)
        if total_bytes > MAX_ZIP_TOTAL_BYTES * 2:
            raise ValueError("ZIP 解压体积超过 2GB 限制")

        members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            norm = _normalize_zip_member(info.filename)
            if norm:
                members[norm] = info

        if "workspace.json" not in members:
            # Seamlessly fall back to single-project ZIP import
            return import_project_zip(
                io.BytesIO(raw_bytes),
                filename=filename,
                projects=projects,
                assets=assets,
            )

        try:
            manifest = json.loads(zf.read(members["workspace.json"]).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"迁移清单 workspace.json 解析失败：{exc}") from exc

        if not isinstance(manifest, dict) or not isinstance(manifest.get("projects"), list):
            raise ValueError("workspace.json 格式不兼容，缺少 projects 列表")

        # 1. Restore settings (never overwrite existing non-empty API keys with empty strings)
        restored_settings = False
        incoming_cfg = manifest.get("settings")
        if isinstance(incoming_cfg, dict):
            current_cfg = settings.load()
            merged: dict[str, Any] = {}
            for k, v in incoming_cfg.items():
                if k == "output_dir":
                    continue
                if k in SECRET_SETTINGS_KEYS and not str(v or "").strip() and str(current_cfg.get(k) or "").strip():
                    continue
                merged[k] = v
            if merged:
                settings.save(merged)
                restored_settings = True

        # 2. Extract all media assets into data/assets/
        extracted_by_arc: dict[str, dict[str, str]] = {}
        extracted_by_basename: dict[str, dict[str, str]] = {}
        for norm, info in members.items():
            if norm == "workspace.json" or info.file_size <= 0:
                continue
            orig_name = Path(norm).name
            ext = Path(orig_name).suffix.lower() or ".bin"
            safe_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", Path(orig_name).stem).strip("_") or "asset"
            # Reuse existing file if exact name & size already present to keep migration clean
            reuse_cand = assets.input_dir / orig_name
            if reuse_cand.exists() and reuse_cand.is_file() and reuse_cand.stat().st_size == info.file_size:
                stored_name = orig_name
                target = reuse_cand
            else:
                stored_name = f"{safe_stem[:32]}-{uuid4().hex[:10]}{ext}"
                target = assets.input_dir / stored_name
                with zf.open(info, "r") as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 256)
                        if not chunk:
                            break
                        dst.write(chunk)
            entry = {
                "kind": kind_for_suffix(ext),
                "url": f"/api/assets/input/{quote(stored_name)}",
                "path": str(target),
                "stored_name": stored_name,
            }
            extracted_by_arc[norm] = entry
            extracted_by_basename.setdefault(orig_name, entry)

        # 3. Restore all projects (update in place if exact ID or non-generic name exists, otherwise create)
        existing_list = projects.list()
        existing_ids = {str(p.get("id") or "") for p in existing_list}
        existing_by_name = {
            str(p.get("name") or ""): str(p.get("id") or "")
            for p in existing_list
            if str(p.get("name") or "").strip() and str(p.get("name") or "").strip() != "未命名画布"
        }
        imported_count = 0
        updated_count = 0
        restored_records: list[dict[str, Any]] = []

        for item in manifest.get("projects") or []:
            if not isinstance(item, dict):
                continue
            snapshot = item.get("snapshot")
            if not isinstance(snapshot, dict):
                continue
            cards = (snapshot.get("canvas") or {}).get("cards")
            if isinstance(cards, list):
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    for field in ("results", "localMedia"):
                        media_items = card.get(field)
                        if not isinstance(media_items, list):
                            continue
                        for m in media_items:
                            if not isinstance(m, dict):
                                continue
                            url = str(m.get("url") or "").strip()
                            matched: dict[str, str] | None = None
                            if url.startswith("zip://"):
                                arc_key = url.removeprefix("zip://").replace("\\", "/").lstrip("/")
                                matched = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
                            elif url.startswith(("/api/assets/input/", "/api/outputs/")):
                                base = unquote(Path(url).name)
                                matched = extracted_by_basename.get(base)
                            if matched:
                                m["url"] = matched["url"]
                                m["path"] = matched["path"]

            pname = str(item.get("name") or "导入画布")
            pstage = str(item.get("stage") or "test")
            pcover = str(item.get("cover") or "").strip()
            if pcover.startswith("zip://"):
                arc_key = pcover.removeprefix("zip://").replace("\\", "/").lstrip("/")
                matched_cover = extracted_by_arc.get(arc_key) or extracted_by_basename.get(Path(arc_key).name)
                if matched_cover:
                    pcover = matched_cover["url"]
            elif pcover.startswith(("/api/assets/input/", "/api/outputs/")):
                matched_cover = extracted_by_basename.get(unquote(Path(pcover).name))
                if matched_cover:
                    pcover = matched_cover["url"]
            pid = str(item.get("id") or "")
            target_id = pid if (pid and pid in existing_ids) else existing_by_name.get(pname, "")
            if target_id:
                rec = projects.update(
                    target_id,
                    name=pname,
                    snapshot=snapshot,
                    cover=pcover if pcover else None,
                )
                updated_count += 1
            else:
                rec = projects.create(name=pname, snapshot=snapshot, stage=pstage, cover=pcover)
                imported_count += 1
                existing_ids.add(rec["id"])
                if pname != "未命名画布":
                    existing_by_name[pname] = rec["id"]
            restored_records.append(rec)

        return {
            "success": True,
            "mode": "workspace",
            "imported_projects": imported_count,
            "updated_projects": updated_count,
            "imported_assets": len(extracted_by_arc),
            "restored_settings": restored_settings,
            "projects": restored_records,
        }


def export_selected_projects_zip(
    project_ids: list[str],
    *,
    projects: ProjectStore,
    assets: AssetStore,
    settings: SettingsStore,
) -> tuple[io.BytesIO, str]:
    """Bundle one or more selected canvas projects + their referenced local assets into a ZIP."""
    unique_ids: list[str] = []
    seen: set[str] = set()
    for pid in project_ids:
        clean = str(pid or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique_ids.append(clean)
    if not unique_ids:
        raise ValueError("请至少选择一个画布")
    if len(unique_ids) == 1:
        return export_project_zip(unique_ids[0], projects=projects, assets=assets, settings=settings)

    output_dir = settings.output_dir()
    path_to_arc: dict[Path, str] = {}
    used_arc_names: set[str] = set()

    def register_file(real_path: Path) -> str:
        resolved = real_path.resolve()
        if resolved in path_to_arc:
            return path_to_arc[resolved]
        base = _safe_filename(resolved.name, "asset.bin")
        stem = Path(base).stem
        ext = Path(base).suffix
        candidate = f"assets/{base}"
        idx = 2
        while candidate in used_arc_names:
            candidate = f"assets/{stem}-{idx}{ext}"
            idx += 1
        used_arc_names.add(candidate)
        path_to_arc[resolved] = candidate
        return candidate

    def rewrite_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            real_path = _resolve_local_media(item, assets, output_dir)
            if real_path is None:
                continue
            arc_name = register_file(real_path)
            item["url"] = f"zip://{arc_name}"
            item.pop("path", None)

    exported_projects: list[dict[str, Any]] = []
    for pid in unique_ids:
        record = projects.get(pid)
        snapshot = copy.deepcopy(record.get("snapshot") or {})
        cards = (snapshot.get("canvas") or {}).get("cards")
        if isinstance(cards, list):
            for card in cards:
                if not isinstance(card, dict):
                    continue
                rewrite_list(card.get("results"))
                rewrite_list(card.get("localMedia"))
        cover_url = str(record.get("cover") or "").strip()
        if cover_url:
            cover_path = _resolve_local_media({"url": cover_url}, assets, output_dir)
            if cover_path is not None:
                cover_url = f"zip://{register_file(cover_path)}"
        exported_projects.append(
            {
                "id": record.get("id"),
                "name": record.get("name") or "未命名画布",
                "stage": record.get("stage") or "test",
                "cover": cover_url,
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "snapshot": snapshot,
            }
        )

    manifest = {
        "schema_version": 1,
        "format": "rh-canvas-workspace-zip",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "include_secrets": False,
        "project_count": len(exported_projects),
        "asset_count": len(path_to_arc),
        "projects": exported_projects,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "workspace.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for real_path, arc_name in path_to_arc.items():
            zf.write(real_path, arcname=arc_name, compress_type=zipfile.ZIP_STORED)

    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return buf, f"rh-canvas-batch-{len(exported_projects)}-{stamp}.zip"
