from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, jsonify, request, send_file, send_from_directory

from .ark_client import ArkClient, resolve_ark_base_url, resolve_ark_key
from .asset_store import AssetStore, kind_for_suffix, mime_for_suffix
from .project_store import ProjectCorruptError, ProjectStore, SnapshotTooLargeError
from .runninghub_client import RunningHubVideoClient, resolve_rh_base_url, resolve_rh_key
from .settings_store import SettingsStore
from .tensor_client import TensorCanvasClient, resolve_tensor_base_url, resolve_tensor_key
from .workflow_runner import WorkflowRunner
from .zip_bundle import (
    export_project_zip,
    export_workspace_zip,
    import_project_zip,
    import_workspace_zip,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DIST_DIR = ROOT_DIR / "dist"
SERVER_BOOT_ID = uuid4().hex[:10]


def _spawn_relauncher(host: str, port: int) -> None:
    """Spawn a detached helper that waits for the port to close, then starts a fresh server."""
    relauncher_code = (
        "import os, socket, subprocess, sys, time\n"
        f"host = {host!r}\n"
        f"port = {int(port)}\n"
        f"root = {str(ROOT_DIR)!r}\n"
        "deadline = time.monotonic() + 8.0\n"
        "while time.monotonic() < deadline:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    try:\n"
        "        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "        s.bind((host, port))\n"
        "        s.close()\n"
        "        break\n"
        "    except OSError:\n"
        "        s.close()\n"
        "        time.sleep(0.15)\n"
        "kwargs = {'cwd': root, 'env': os.environ.copy()}\n"
        "if os.name == 'nt':\n"
        "    kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW\n"
        "else:\n"
        "    kwargs['start_new_session'] = True\n"
        "subprocess.Popen([sys.executable, '-m', 'server.app'], **kwargs)\n"
    )
    kwargs: dict[str, Any] = {"cwd": str(ROOT_DIR), "env": os.environ.copy()}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-c", relauncher_code], **kwargs)


def _json_error(error: Exception | str, status: int = 400):
    return jsonify({"success": False, "error": str(error)}), status


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024

    @app.errorhandler(413)
    def handle_too_large(_err):
        return _json_error("上传文件超过服务器 512MB 限制", 413)

    projects = ProjectStore(DATA_DIR / "projects")
    assets = AssetStore(DATA_DIR / "assets")
    settings = SettingsStore(DATA_DIR / "settings.json", DATA_DIR / "outputs")
    runner = WorkflowRunner(settings)

    @app.get("/api/health")
    def health():
        cfg = settings.load()
        return jsonify(
            {
                "success": True,
                "service": "rh-canvas-cli",
                "version": "1.0.0",
                "boot_id": SERVER_BOOT_ID,
                "output_dir": str(settings.output_dir()),
                "has_ark_key": bool(resolve_ark_key(cfg)),
                "has_runninghub_key": bool(str(cfg.get("runninghub_api_key") or "").strip()),
                "has_tensor_key": bool(resolve_tensor_key(cfg)),
                "has_llm_key": bool(str(cfg.get("llm_api_key") or "").strip()),
            }
        )

    @app.post("/api/server/restart")
    def restart_server():
        host = os.environ.get("RH_CANVAS_HOST", "127.0.0.1")
        port = int(os.environ.get("RH_CANVAS_PORT", "5188"))

        def _do_restart() -> None:
            time.sleep(0.35)
            try:
                _spawn_relauncher(host, port)
            finally:
                os._exit(0)

        threading.Thread(target=_do_restart, daemon=True).start()
        return jsonify({"success": True, "restarting": True, "boot_id": SERVER_BOOT_ID})

    @app.post("/api/ark/test")
    def test_ark_connection():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        cfg = settings.load()
        api_key = str(payload.get("ark_api_key") or resolve_ark_key(cfg)).strip()
        base_url = str(payload.get("ark_base_url") or resolve_ark_base_url(cfg)).strip()
        try:
            res = ArkClient(api_key=api_key, base_url=base_url).test_connection()
            return jsonify(res)
        except Exception as exc:
            return _json_error(exc, 400)

    @app.post("/api/runninghub/test")
    def test_runninghub_connection():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        cfg = settings.load()
        api_key = str(payload.get("runninghub_api_key") or resolve_rh_key(cfg)).strip()
        base_url = str(payload.get("runninghub_base_url") or resolve_rh_base_url(cfg)).strip()
        try:
            res = RunningHubVideoClient(api_key=api_key, base_url=base_url).test_connection()
            return jsonify(res)
        except Exception as exc:
            return _json_error(exc, 400)

    @app.post("/api/tensor/test")
    def test_tensor_connection():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        cfg = settings.load()
        api_key = str(payload.get("tensor_api_key") or resolve_tensor_key(cfg)).strip()
        base_url = str(payload.get("tensor_base_url") or resolve_tensor_base_url(cfg)).strip()
        try:
            res = TensorCanvasClient(api_key=api_key, base_url=base_url).test_connection()
            return jsonify(res)
        except Exception as exc:
            return _json_error(exc, 400)

    @app.get("/api/projects")
    def list_projects():
        stage = request.args.get("stage")
        try:
            items = projects.list(stage=stage)
        except ValueError as exc:
            return _json_error(exc, 400)
        return jsonify(
            {
                "success": True,
                "projects": items,
                "dirs": {
                    "online": str(projects.online_dir),
                    "test": str(projects.test_dir),
                },
            }
        )

    @app.post("/api/projects")
    def create_project():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            record = projects.create(
                name=str(payload.get("name") or "未命名画布"),
                snapshot=payload.get("snapshot"),
                stage=str(payload.get("stage") or "test"),
            )
            return jsonify({"success": True, "project": record}), 201
        except (ValueError, SnapshotTooLargeError) as exc:
            return _json_error(exc, 400)

    @app.get("/api/projects/<project_id>")
    def get_project(project_id: str):
        try:
            return jsonify({"success": True, "project": projects.get(project_id)})
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except (ValueError, ProjectCorruptError) as exc:
            return _json_error(exc, 400)

    @app.put("/api/projects/<project_id>")
    def update_project(project_id: str):
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            record = projects.update(
                project_id,
                name=payload.get("name"),
                snapshot=payload.get("snapshot"),
            )
            return jsonify({"success": True, "project": record})
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except (ValueError, SnapshotTooLargeError, ProjectCorruptError) as exc:
            return _json_error(exc, 400)

    @app.post("/api/projects/<project_id>/stage")
    def move_project_stage(project_id: str):
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        target_stage = payload.get("stage")
        try:
            record = projects.move_stage(project_id, str(target_stage or ""))
            return jsonify({"success": True, "project": record})
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except (ValueError, SnapshotTooLargeError, ProjectCorruptError) as exc:
            return _json_error(exc, 400)

    @app.post("/api/projects/<project_id>/duplicate")
    def duplicate_project(project_id: str):
        try:
            record = projects.duplicate(project_id)
            return jsonify({"success": True, "project": record}), 201
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except (ValueError, SnapshotTooLargeError) as exc:
            return _json_error(exc, 400)

    @app.delete("/api/projects/<project_id>")
    def delete_project(project_id: str):
        try:
            projects.delete(project_id)
            return jsonify({"success": True})
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except ValueError as exc:
            return _json_error(exc, 400)

    @app.get("/api/projects/<project_id>/export-zip")
    def export_project_as_zip(project_id: str):
        try:
            buf, download_name = export_project_zip(
                project_id,
                projects=projects,
                assets=assets,
                settings=settings,
            )
            return send_file(
                buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=download_name,
            )
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except Exception as exc:
            return _json_error(exc, 400)

    @app.post("/api/projects/import-zip")
    def import_project_from_zip():
        file = request.files.get("file")
        if file is None:
            return _json_error("缺少上传文件字段 file", 400)
        try:
            res = import_workspace_zip(
                file.stream,
                filename=str(file.filename or "canvas.zip"),
                projects=projects,
                assets=assets,
                settings=settings,
            )
            return jsonify(res), 201
        except (ValueError, SnapshotTooLargeError) as exc:
            return _json_error(exc, 400)
        except Exception as exc:
            return _json_error(f"导入 ZIP 失败：{exc}", 400)

    @app.get("/api/workspace/export-zip")
    def export_workspace_as_zip():
        raw_inc = str(request.args.get("include_secrets", "1")).strip().lower()
        include_secrets = raw_inc not in ("0", "false", "no")
        try:
            buf, download_name = export_workspace_zip(
                projects=projects,
                assets=assets,
                settings=settings,
                include_secrets=include_secrets,
            )
            return send_file(
                buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=download_name,
            )
        except Exception as exc:
            return _json_error(f"导出全量迁移包失败：{exc}", 400)

    @app.post("/api/workspace/import-zip")
    def import_workspace_from_zip():
        file = request.files.get("file")
        if file is None:
            return _json_error("缺少上传文件字段 file", 400)
        try:
            res = import_workspace_zip(
                file.stream,
                filename=str(file.filename or "migration.zip"),
                projects=projects,
                assets=assets,
                settings=settings,
            )
            return jsonify(res), 201
        except (ValueError, SnapshotTooLargeError) as exc:
            return _json_error(exc, 400)
        except Exception as exc:
            return _json_error(f"导入迁移包失败：{exc}", 400)

    @app.post("/api/assets/input")
    def upload_input_asset():
        file = request.files.get("file")
        if file is None:
            return _json_error("缺少上传文件字段 file", 400)
        try:
            asset = assets.save_upload(file)
            return jsonify({"success": True, "asset": asset}), 201
        except ValueError as exc:
            return _json_error(exc, 400)

    @app.get("/api/assets/input/<path:asset_name>")
    def serve_input_asset(asset_name: str):
        try:
            target = assets.resolve_input(asset_name)
            mime = mime_for_suffix(target.suffix, kind_for_suffix(target.suffix))
            return send_file(target, mimetype=mime, conditional=True)
        except FileNotFoundError as exc:
            return _json_error(exc, 404)
        except ValueError as exc:
            return _json_error(exc, 400)

    @app.get("/api/outputs")
    def list_outputs():
        import json
        from urllib.parse import quote
        from datetime import datetime, timezone

        root = settings.output_dir()
        meta_map: dict[str, Any] = {}
        meta_path = root / ".meta.json"
        if meta_path.exists():
            try:
                raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(raw_meta, dict):
                    meta_map = raw_meta
            except Exception:
                meta_map = {}

        items: list[dict[str, Any]] = []
        for sub, kind, exts in [
            ("videos", "video", {".mp4", ".mov", ".webm"}),
            ("images", "image", {".png", ".jpg", ".jpeg", ".webp"}),
        ]:
            folder = root / sub
            if not folder.exists():
                continue
            for p in folder.iterdir():
                if not p.is_file() or p.suffix.lower() not in exts:
                    continue
                st = p.stat()
                rel = f"{sub}/{p.name}"
                items.append(
                    {
                        "name": p.name,
                        "rel_path": rel,
                        "kind": kind,
                        "url": f"/api/outputs/{quote(rel)}",
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                        "meta": meta_map.get(rel),
                    }
                )
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return jsonify({"success": True, "outputs": items[:150]})

    @app.delete("/api/outputs/<path:rel_path>")
    def delete_output_asset(rel_path: str):
        root = settings.output_dir()
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            return _json_error("非法输出路径", 400)
        if not target.exists() or not target.is_file():
            return _json_error("文件不存在", 404)
        target.unlink()
        return jsonify({"success": True})

    @app.get("/api/outputs/<path:rel_path>")
    def serve_output_asset(rel_path: str):
        root = settings.output_dir()
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            return _json_error("非法输出路径", 400)
        if not target.exists() or not target.is_file():
            return _json_error("输出文件不存在", 404)
        mime = mime_for_suffix(target.suffix, kind_for_suffix(target.suffix))
        return send_file(target, mimetype=mime, conditional=True)

    @app.get("/api/config")
    def get_config():
        return jsonify({"success": True, "config": settings.load()})

    @app.post("/api/config")
    def save_config():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            updated = settings.save(payload)
            return jsonify({"success": True, "config": updated})
        except Exception as exc:
            return _json_error(exc, 400)

    @app.post("/api/nodes/run")
    def run_node():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        card = payload.get("card")
        upstream = payload.get("upstream") or []
        if not isinstance(card, dict):
            return _json_error("缺少节点数据 card", 400)
        try:
            result = runner.run_node(card, upstream if isinstance(upstream, list) else [])
            return jsonify(result)
        except Exception as exc:
            return _json_error(exc, 400)

    @app.post("/api/nodes/cancel")
    def cancel_node():
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        node_id = str(payload.get("node_id") or "").strip()
        if not node_id:
            return _json_error("缺少 node_id", 400)
        return jsonify(runner.cancel_node(node_id))

    @app.get("/api/tasks")
    def list_tasks():
        limit = min(200, max(1, int(request.args.get("limit", "50"))))
        return jsonify({"success": True, "tasks": runner.list_tasks(limit=limit)})

    # Serve built Vue SPA when dist/ exists (single-command production mode)
    @app.get("/")
    def spa_index():
        if (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return jsonify(
            {
                "success": True,
                "message": "RH Canvas Studio backend is running. Run `pnpm dev` for dev UI or `pnpm build` for bundled UI.",
            }
        )

    @app.get("/<path:asset_path>")
    def spa_static(asset_path: str):
        if asset_path.startswith("api/"):
            return _json_error("API route not found", 404)
        candidate = DIST_DIR / asset_path
        if candidate.exists() and candidate.is_file():
            return send_from_directory(DIST_DIR, asset_path)
        if (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return _json_error("Not found", 404)

    return app


def main() -> None:
    host = os.environ.get("RH_CANVAS_HOST", "127.0.0.1")
    port = int(os.environ.get("RH_CANVAS_PORT", "5188"))
    app = create_app()
    print(f"RH Canvas Studio Backend running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
