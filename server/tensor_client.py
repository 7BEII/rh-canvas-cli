from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote
from uuid import uuid4

import requests
from PIL import Image

from .asset_store import (
    MEDIA_MAX_DURATION_SEC,
    MEDIA_MIN_DURATION_SEC,
    kind_for_suffix,
    mime_for_suffix,
    normalize_media_duration_file,
    probe_media_duration,
)

DEFAULT_TENSOR_BASE = "https://openapi.tensorart.me/canvas-openapi"
TUSI_TENSOR_BASE = "https://openapi.tusiart.cn/canvas-openapi"

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_ROOT / "tensor_state.json"
TEMPLATES_PATH = Path(__file__).with_name("tensor_templates.json")

CANVAS_NAME = "RH Canvas CLI"
CANVAS_NODE_SOFT_LIMIT = 240  # 官方单画布上限 300 节点，留余量
UPLOAD_CACHE_TTL = 6 * 3600

EXT_TO_FILE_TYPE: dict[str, str] = {
    ".jpg": "JPG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".gif": "GIF",
    ".mp4": "MP4",
    ".mov": "MOV",
    ".webm": "WEBM",
    ".mp3": "MP3",
    ".wav": "WAV",
    ".m4a": "M4A",
    ".aac": "AAC",
    ".ogg": "OGG",
    ".flac": "FLAC",
}

TAG_TO_EN = {"图片": "image", "视频": "video", "音频": "audio"}

_state_lock = threading.RLock()
_canvas_lock = threading.Lock()
_canvas_node_count: dict[str, int] = {}
_upload_cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}


# ---------------------------------------------------------------- config helpers

def resolve_tensor_key(config: dict[str, Any]) -> str:
    return str(config.get("tensor_api_key") or os.environ.get("TENSOR_CANVAS_API_KEY") or "").strip()


def resolve_tensor_base_url(config: dict[str, Any]) -> str:
    raw = str(config.get("tensor_base_url") or DEFAULT_TENSOR_BASE).strip().rstrip("/")
    return raw or DEFAULT_TENSOR_BASE


def is_tensor_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("tensor:")


def _norm_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(name or "").upper()).strip("_")


def resolve_model_key(model: str, kind: str = "image") -> str:
    """Map UI model id -> Tensor baseModel key, e.g. tensor:SEEDANCE_2_0_FAST -> SEEDANCE_2_0_FAST."""
    raw = str(model or "").strip()
    if raw.lower().startswith("tensor:"):
        return _norm_key(raw.split(":", 1)[1])
    low = raw.lower()
    if kind == "video":
        if "seedance" in low and ("2.5" in low or "2_5" in low):
            return "SEEDANCE_2_5_FLASH" if "flash" in low else "SEEDANCE_2_5"
        if "seedance" in low and "fast" in low:
            return "SEEDANCE_2_0_FAST"
        if "seedance" in low:
            return "SEEDANCE_2_0"
        if "kling" in low and "omni" in low:
            return "KLING_V3_OMNI"
        if "kling" in low:
            return "KLING_V3"
        if "vidu" in low:
            return "VIDU_Q3_PRO"
        if "pixverse" in low:
            return "PIXVERSE_V6"
        if "wan" in low:
            return "WAN_2_7_I2V"
        return "SEEDANCE_2_0_FAST"
    return "NANO_BANANA_2"


# Backward-compatible alias
resolve_pinned_model = resolve_model_key


# ---------------------------------------------------------------- local state

def _load_state() -> dict[str, Any]:
    with _state_lock:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}


def _save_state(update: Callable[[dict[str, Any]], None]) -> None:
    with _state_lock:
        state = _load_state()
        update(state)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_templates() -> dict[str, Any]:
    try:
        builtin = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    except Exception:
        builtin = {"base_request": {}, "models": {}}
    learned = _load_state().get("learned_models") or {}
    models = {**(builtin.get("models") or {}), **learned}
    return {"base_request": builtin.get("base_request") or {}, "models": models}


def _strip_request(req: dict[str, Any]) -> dict[str, Any]:
    req = copy.deepcopy(req)
    p = req.get("params") or {}
    for k in ("prompt", "displayPrompt", "negativePrompt"):
        if k in p:
            p[k] = ""
    for k in ("files", "images", "imageIds", "promptBlocks"):
        if k in p:
            p[k] = []
    vp = p.get("videoParams")
    if isinstance(vp, dict):
        vp["refVideoDurations"] = []
    req["credits"] = 0
    return req


# ---------------------------------------------------------------- client

class TensorCanvasClient:
    """Tensor / Forge Canvas OpenAPI v1 —— 直接运行画布节点（workflow/actions + workflow/nodes/run）。

    不走 Agent 对话；所有生成复用同一个固定画布（按接入节点区分），不会每次新建画布。
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_TENSOR_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------ http
    def _headers(self, json_content: bool = True) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("请先在设置中填写 Tensor (Forge) Access Key")
        h = {"Authorization": f"Bearer {self.api_key}", "X-Request-Id": str(uuid4())}
        if json_content:
            h["Content-Type"] = "application/json"
        return h

    def _post_json(self, path: str, body: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/v1{path}",
                    headers=self._headers(json_content=True),
                    json=body,
                    timeout=timeout,
                )
                break
            except requests.exceptions.SSLError as exc:
                last_exc = exc
                if attempt == 2:
                    raise
                time.sleep(0.6 * (attempt + 1))
        else:
            raise last_exc  # pragma: no cover
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Tensor 接口响应异常 (HTTP {resp.status_code})") from exc
        if not resp.ok:
            msg = payload.get("message") or payload.get("msg") or f"HTTP {resp.status_code}"
            raise RuntimeError(f"Tensor 请求失败 (HTTP {resp.status_code})：{msg}")
        if str(payload.get("code")) != "0":
            msg = payload.get("message") or f"code={payload.get('code')}"
            raise RuntimeError(f"Tensor 业务失败：{msg}")
        return payload.get("data") or {}

    # ------------------------------------------------------------ connection test
    def test_connection(self) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("请先填写 Tensor (Forge) Access Key")
        data = self._post_json("/canvas/list", {"size": 5}, timeout=15)
        canvas_count = len(data.get("canvasList") or [])
        site = "吐司国内" if "tusiart" in self.base_url else "Tensor 海外"
        models = sorted(_load_templates()["models"].keys())
        # 后台从已有画布学习更多模型参数，不阻塞测试
        threading.Thread(target=self._safe_harvest, daemon=True).start()
        return {
            "success": True,
            "status_code": 200,
            "message": f"Tensor ({site}) Access Key 验证通过（画布 {canvas_count} 个 · 直跑节点可用模型 {len(models)} 个）",
        }

    def cancel_chat(self, canvas_id: str, session_id: str) -> bool:
        """Kept for compatibility; direct node runs have no public remote-cancel endpoint."""
        return False

    # ------------------------------------------------------------ model template learning
    def _safe_harvest(self) -> None:
        try:
            self.harvest_models()
        except Exception:
            pass

    def harvest_models(self, max_canvases: int = 60) -> list[str]:
        """Scan the user's canvases for GENERATE nodes and learn baseModel + request template per model."""
        learned: dict[str, Any] = {}
        cursor = ""
        scanned = 0
        while scanned < max_canvases:
            body: dict[str, Any] = {"size": 50}
            if cursor:
                body["cursor"] = cursor
            data = self._post_json("/canvas/list", body, timeout=20)
            canvases = data.get("canvasList") or []
            for c in canvases:
                if scanned >= max_canvases:
                    break
                scanned += 1
                try:
                    g = self._post_json("/workflow/graph", {"canvasId": str(c.get("id"))}, timeout=20)
                except Exception:
                    continue
                for n in (g.get("workflow") or {}).get("nodes") or []:
                    if str(n.get("subType") or "").upper() != "GENERATE":
                        continue
                    req = (((n.get("data") or {}).get("generation") or {}).get("params") or {}).get("request")
                    if not isinstance(req, dict):
                        continue
                    bm = (req.get("params") or {}).get("baseModel") or {}
                    key = _norm_key(bm.get("baseModel") or "")
                    if not key or not bm.get("modelId") or key in learned:
                        continue
                    learned[key] = {
                        "kind": str(n.get("type") or "image").lower(),
                        "baseModel": bm,
                        "request": _strip_request(req),
                    }
            cursor = str(data.get("cursor") or "")
            if not cursor or not canvases:
                break
        if learned:
            _save_state(lambda s: s.setdefault("learned_models", {}).update(learned))
        return sorted(learned.keys())

    def _get_model_template(self, model_key: str, kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
        tpl = _load_templates()
        entry = tpl["models"].get(model_key)
        if not entry:
            self.harvest_models()
            tpl = _load_templates()
            entry = tpl["models"].get(model_key)
        if not entry:
            known = ", ".join(sorted(k for k, v in tpl["models"].items() if v.get("kind") == kind))
            raise RuntimeError(
                f"Tensor 模型 {model_key} 还没有节点参数模板。请先在 Tensor 网页画布上用该模型跑一次生成，"
                f"再点「测试连通性」自动学习。当前可直跑：{known or '无'}"
            )
        base_req = entry.get("request") or tpl["base_request"].get(kind)
        if not base_req:
            raise RuntimeError(f"缺少 {kind} 节点基础参数模板")
        return entry, copy.deepcopy(base_req)

    # ------------------------------------------------------------ fixed canvas
    def _canvas_state_key(self) -> str:
        return self.base_url

    def ensure_canvas(self) -> str:
        """Return the persistent working canvas; create once, clean up when approaching node limit."""
        with _canvas_lock:
            skey = self._canvas_state_key()
            canvas_id = str((_load_state().get("canvas") or {}).get(skey) or "").strip()
            if canvas_id and canvas_id in _canvas_node_count:
                count = _canvas_node_count[canvas_id]
            elif canvas_id:
                try:
                    g = self._post_json("/workflow/graph", {"canvasId": canvas_id}, timeout=20)
                    count = len((g.get("workflow") or {}).get("nodes") or [])
                except Exception:
                    canvas_id, count = "", 0
            else:
                count = 0
            if not canvas_id:
                data = self._post_json("/canvas/create", {"name": CANVAS_NAME})
                canvas_id = str((data.get("canvas") or {}).get("id") or "").strip()
                if not canvas_id:
                    raise RuntimeError("Tensor 创建画布未返回 canvasId")
                _save_state(lambda s: s.setdefault("canvas", {}).__setitem__(skey, canvas_id))
                count = 0
            if count >= CANVAS_NODE_SOFT_LIMIT:
                self._clear_canvas(canvas_id)
                count = 0
            _canvas_node_count[canvas_id] = count
            return canvas_id

    def _clear_canvas(self, canvas_id: str) -> None:
        g = self._post_json("/workflow/graph", {"canvasId": canvas_id}, timeout=30)
        ids = [str(n.get("id")) for n in (g.get("workflow") or {}).get("nodes") or [] if n.get("id")]
        for i in range(0, len(ids), 100):
            self._post_json(
                "/workflow/actions",
                {"canvasId": canvas_id, "actions": [{"action": "delete", "nodeIds": ids[i : i + 100]}]},
                timeout=60,
            )

    def _bump_count(self, canvas_id: str, n: int) -> int:
        with _canvas_lock:
            _canvas_node_count[canvas_id] = _canvas_node_count.get(canvas_id, 0) + n
            return _canvas_node_count[canvas_id]

    # ------------------------------------------------------------ media upload
    def _resolve_local_file(self, item: dict[str, Any]) -> Path | None:
        raw_path = str(item.get("path") or "").strip()
        if raw_path:
            p = Path(raw_path)
            if p.exists() and p.is_file():
                return p
        url = str(item.get("url") or "").strip()
        if url.startswith("/api/assets/input/"):
            cand = DATA_ROOT / "assets" / unquote(url.removeprefix("/api/assets/input/"))
            if cand.exists() and cand.is_file():
                return cand
        if url.startswith("/api/outputs/"):
            cand = DATA_ROOT / "outputs" / unquote(url.removeprefix("/api/outputs/"))
            if cand.exists() and cand.is_file():
                return cand
        return None

    @staticmethod
    def _prepare_upload_bytes(file_path: Path) -> tuple[bytes, str, str]:
        """Return (payload_bytes, file_type, mime); shrink oversized opaque images to avoid slow R2 PUT timeouts."""
        ext = file_path.suffix.lower()
        file_type = EXT_TO_FILE_TYPE.get(ext)
        if not file_type:
            raise ValueError(f"Tensor 不支持的文件类型：{ext or file_path.name}")
        raw = file_path.read_bytes()
        kind = kind_for_suffix(ext)
        mime = mime_for_suffix(ext, kind)
        if kind == "image" and len(raw) > 800 * 1024 and ext != ".gif":
            try:
                import io

                with Image.open(io.BytesIO(raw)) as im:
                    has_alpha = im.mode in ("RGBA", "LA", "PA") and im.getchannel("A").getextrema()[0] < 255
                    if max(im.size) > 2048:
                        im.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    if has_alpha:
                        im.save(buf, format="PNG", optimize=True)
                        cand, cand_type, cand_mime = buf.getvalue(), "PNG", "image/png"
                    else:
                        im.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True)
                        cand, cand_type, cand_mime = buf.getvalue(), "JPG", "image/jpeg"
                    if len(cand) < len(raw):
                        return cand, cand_type, cand_mime
            except Exception:
                pass
        return raw, file_type, mime

    def upload_file(self, file_path: Path) -> dict[str, Any]:
        md5 = hashlib.md5(file_path.read_bytes()).hexdigest()
        cache_key = (self.base_url, md5)
        cached = _upload_cache.get(cache_key)
        if cached and time.time() - cached[1] < UPLOAD_CACHE_TTL:
            return dict(cached[0])

        payload_bytes, file_type, mime = self._prepare_upload_bytes(file_path)
        # Do not pass hex md5 to /media/upload/create; R2 presigned URL signs content-md5 and rejects hex-signed PUTs with 403.
        last_exc: Exception | None = None
        for attempt in range(2):
            data = self._post_json("/media/upload/create", {"fileType": file_type}, timeout=20)
            upload_url = str(data.get("uploadUrl") or "").strip()
            display_url = str(data.get("displayUrl") or "").strip()
            if not upload_url or not display_url:
                raise RuntimeError("Tensor 上传地址申请失败")
            try:
                put_resp = requests.put(upload_url, headers={"Content-Type": mime}, data=payload_bytes, timeout=180)
                if not put_resp.ok:
                    raise RuntimeError(f"上传素材到 Tensor 存储失败 (HTTP {put_resp.status_code})")
                file_obj = {"url": display_url, "mimeType": mime, "assetId": str(data.get("mediaId") or "")}
                _upload_cache[cache_key] = (file_obj, time.time())
                return dict(file_obj)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt == 1:
                    raise
                time.sleep(0.8)
        raise last_exc  # pragma: no cover

    def _upload_media(self, file_path: Path, kind: str) -> tuple[dict[str, Any], float | None]:
        """Upload; video/audio duration is normalized into Seedance's 2–15s window first."""
        if kind not in ("video", "audio"):
            return self.upload_file(file_path), None
        with tempfile.TemporaryDirectory(prefix="tensor-dur-") as tmpdir:
            norm_path, _adjusted, dur = normalize_media_duration_file(
                file_path, kind, min_sec=MEDIA_MIN_DURATION_SEC, max_sec=MEDIA_MAX_DURATION_SEC, output_dir=Path(tmpdir)
            )
            if _adjusted:
                dur = probe_media_duration(norm_path) or dur
            return self.upload_file(norm_path), dur

    # ------------------------------------------------------------ core: direct node run
    @staticmethod
    def _convert_prompt_tags(prompt: str) -> str:
        return re.sub(r"@(图片|视频|音频)(\d+)", lambda m: f"@{TAG_TO_EN[m.group(1)]}{m.group(2)}", prompt)

    def _run_generate_node(
        self,
        *,
        kind: str,
        model_key: str,
        prompt: str,
        ref_items: list[dict[str, Any]],
        configure: Callable[[dict[str, Any], dict[str, Any]], None],
        cancel_event: threading.Event | None,
        on_session_created: Callable[[str, str], None] | None,
        timeout_sec: int,
    ) -> dict[str, Any]:
        entry, req = self._get_model_template(model_key, kind)
        canvas_id = self.ensure_canvas()
        if on_session_created:
            on_session_created(canvas_id, "")

        base_count = _canvas_node_count.get(canvas_id, 0)
        row_y = 80 + (base_count % 40) * 60

        # 1) upload refs + build UPLOAD nodes (per-kind order => @image1/@video1/@audio1)
        ref_nodes: list[dict[str, Any]] = []
        video_durations: list[float] = []
        for idx, item in enumerate(ref_items):
            rkind = str(item.get("kind") or "image").lower()
            if rkind not in ("image", "video", "audio"):
                continue
            local_p = self._resolve_local_file(item)
            if local_p is None:
                continue
            up, dur = self._upload_media(local_p, rkind)
            if rkind == "video" and dur:
                video_durations.append(round(float(dur), 1))
            title = str(item.get("tag") or item.get("name") or local_p.stem)[:60]
            ref_nodes.append(
                {
                    "id": f"ref-{rkind}-{uuid4().hex[:10]}",
                    "type": rkind.upper(),
                    "subType": "UPLOAD",
                    "title": title,
                    "position": {"x": 80 + idx * 40, "y": row_y + idx * 30},
                    "data": {rkind: {"url": up["url"]}},
                }
            )

        # 2) build GENERATE node params
        p = req.setdefault("params", {})
        p["baseModel"] = copy.deepcopy(entry["baseModel"])
        clean_prompt = self._convert_prompt_tags(prompt)
        p["prompt"] = clean_prompt
        p["displayPrompt"] = clean_prompt
        configure(req, {"has_refs": bool(ref_nodes), "video_durations": video_durations})

        gen_id = f"{kind}-{uuid4()}"
        gen_node = {
            "id": gen_id,
            "type": kind.upper(),
            "subType": "GENERATE",
            "title": f"{model_key} · {clean_prompt[:24]}",
            "position": {"x": 700, "y": row_y},
            "data": {"generation": {"params": {"request": req, "promptBlocks": [], "characterBindings": []}}},
        }

        # 3) add nodes (server may return created.nodes in any order, so match by id first)
        wanted = [*ref_nodes, gen_node]
        actions: list[dict[str, Any]] = [{"action": "add", "nodes": wanted}]
        res = self._post_json("/workflow/actions", {"canvasId": canvas_id, "actions": actions}, timeout=60)
        created = (res.get("created") or {}).get("nodes") or []
        created_by_id = {str(c.get("id")): c for c in created if c.get("id")}
        remaining = [c for c in created if str(c.get("id") or "") not in {w["id"] for w in wanted}]
        id_map: dict[str, str] = {}
        for w in wanted:
            wid = w["id"]
            if wid in created_by_id:
                id_map[wid] = wid
            else:
                match_idx = next(
                    (
                        i
                        for i, c in enumerate(remaining)
                        if c.get("type") == w.get("type")
                        and c.get("subType") == w.get("subType")
                        and c.get("title") == w.get("title")
                    ),
                    None,
                )
                if match_idx is not None:
                    id_map[wid] = str(remaining.pop(match_idx).get("id") or wid)
                else:
                    id_map[wid] = wid
        gen_real = id_map.get(gen_id, gen_id)
        if ref_nodes:
            conns = [
                {
                    "id": f"edge-{uuid4()}",
                    "source": id_map.get(r["id"], r["id"]),
                    "sourceHandle": "output",
                    "target": gen_real,
                    "targetHandle": "input",
                    "type": "default",
                }
                for r in ref_nodes
            ]
            self._post_json(
                "/workflow/actions",
                {"canvasId": canvas_id, "actions": [{"action": "connect", "connections": conns}]},
                timeout=60,
            )
        self._bump_count(canvas_id, len(wanted))

        # 4) run node
        run = self._post_json(
            "/workflow/nodes/run",
            {"canvasId": canvas_id, "runTarget": {"type": "node", "nodeIds": [gen_real]}, "clientRequestId": str(uuid4())},
            timeout=60,
        )
        run_id = str(run.get("runId") or "")
        if on_session_created:
            on_session_created(canvas_id, "")

        # 5) poll only this node (fast at first, then back off)
        urls, dims = self._poll_node(canvas_id, gen_real, kind, cancel_event, timeout_sec)
        return {"canvas_id": canvas_id, "node_id": gen_real, "run_id": run_id, "urls": urls, "dims": dims}

    def _poll_node(
        self,
        canvas_id: str,
        node_id: str,
        kind: str,
        cancel_event: threading.Event | None,
        timeout_sec: int,
    ) -> tuple[list[str], str]:
        start = time.monotonic()
        while time.monotonic() - start < timeout_sec:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("任务已取消（Tensor 云端节点可能仍会继续运行）")
            elapsed = time.monotonic() - start
            try:
                data = self._post_json("/workflow/nodes/query", {"canvasId": canvas_id, "nodeIds": [node_id]}, timeout=20)
                node = (data.get("nodes") or [{}])[0]
            except Exception:
                node = {}
            status = str(node.get("status") or "").upper()
            gen = (node.get("data") or {}).get("generation") or {}
            runs = gen.get("runs") or []
            last = runs[-1] if runs else {}
            if status == "SUCCESS" or str(last.get("status") or "").upper() == "SUCCESS":
                outputs = last.get("outputs") or []
                cur = gen.get("currentOutput")
                if isinstance(cur, dict):
                    outputs = [cur, *outputs]
                urls: list[str] = []
                dims = ""
                for o in outputs:
                    if not isinstance(o, dict):
                        continue
                    u = o.get("downloadUrl") or o.get("signedUrl") or o.get("url")
                    if isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
                        if not dims and o.get("width") and o.get("height"):
                            dims = f"{o['width']}x{o['height']}"
                urls = list(dict.fromkeys(urls))
                if urls:
                    return urls, dims
            if status in ("FAILED", "INVALID", "ERROR") or str(last.get("status") or "").upper() in ("FAILED", "ERROR"):
                code = str(last.get("failCode") or "").strip()
                msg = str(last.get("failMessage") or "").strip()
                reviews = last.get("inputReviews") or last.get("reviewInfos") or []
                detail = f"{code}: {msg}".strip(": ") if (code or msg) else (json.dumps(reviews, ensure_ascii=False) if reviews else status)
                raise RuntimeError(f"Tensor 节点生成失败：{detail}")
            interval = 1.0 if elapsed < 30 else (2.0 if elapsed < 120 else 4.0)
            time.sleep(interval)
        raise RuntimeError(f"Tensor 节点生成超时（{timeout_sec}s），canvas={canvas_id} node={node_id}")

    @staticmethod
    def _download(url: str, target: Path, timeout: int) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)

    # ------------------------------------------------------------ public: image
    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        aspect: str,
        ref_assets: list[dict[str, Any]],
        output_dir: Path,
        cancel_event: threading.Event | None = None,
        on_session_created: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        ref_items: list[dict[str, Any]] = []
        img_idx = 0
        for asset in ref_assets:
            if asset.get("kind") != "image" or self._resolve_local_file(asset) is None:
                continue
            img_idx += 1
            ref_items.append({**asset, "tag": f"@图片{img_idx}"})

        model_key = resolve_model_key(model, kind="image")
        clean_prompt = (prompt or "").strip() or "a high quality cinematic image"
        if ref_items and "@图片" not in clean_prompt and "@image" not in clean_prompt:
            refs = " ".join(f"@image{i + 1}" for i in range(len(ref_items)))
            clean_prompt = f"Reference: {refs}\n{clean_prompt}"

        def configure(req: dict[str, Any], ctx: dict[str, Any]) -> None:
            p = req["params"]
            if aspect and aspect != "自适应":
                p["aspectRatio"] = aspect
            req["taskType"] = "IMG2IMG" if ctx["has_refs"] else "TXT2IMG"

        gen = self._run_generate_node(
            kind="image",
            model_key=model_key,
            prompt=clean_prompt,
            ref_items=ref_items,
            configure=configure,
            cancel_event=cancel_event,
            on_session_created=on_session_created,
            timeout_sec=600,
        )

        remote_url = gen["urls"][0]
        ext = ".png"
        low_url = remote_url.lower().split("?")[0]
        for cand_ext in (".png", ".jpg", ".jpeg", ".webp"):
            if low_url.endswith(cand_ext):
                ext = cand_ext
                break
        filename = f"tensor-{model_key.lower()}-{uuid4().hex[:8]}{ext}"
        target = output_dir / "images" / filename
        self._download(remote_url, target, timeout=120)

        dims = gen["dims"] or aspect
        try:
            with Image.open(target) as im:
                dims = f"{im.width}x{im.height}"
        except Exception:
            pass

        return {
            "model": model_key,
            "filename": filename,
            "path": target,
            "size": dims,
            "remote_url": remote_url,
            "canvas_id": gen["canvas_id"],
            "session_id": gen["run_id"],
        }

    # ------------------------------------------------------------ public: video
    def generate_video(
        self,
        *,
        model: str,
        prompt: str,
        mode: str,
        resolution: str,
        duration: int,
        aspect: str,
        generate_audio: bool,
        slots: list[dict[str, Any]],
        output_dir: Path,
        cancel_event: threading.Event | None = None,
        on_session_created: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        ref_items: list[dict[str, Any]] = []
        if mode != "t2v":
            ref_items = [s for s in slots if self._resolve_local_file(s) is not None]

        model_key = resolve_model_key(model, kind="video")
        clean_prompt = (prompt or "").strip() or "a cinematic video shot with smooth camera motion"

        def configure(req: dict[str, Any], ctx: dict[str, Any]) -> None:
            p = req["params"]
            if aspect and aspect != "自适应":
                p["aspectRatio"] = aspect
            if resolution:
                p["imageSize"] = resolution
            vp = p.get("videoParams") or {}
            if duration:
                vp["dur"] = str(int(duration))
            vp["hasAudioOutput"] = bool(generate_audio)
            vp["refVideoDurations"] = ctx["video_durations"]
            vp["mode"] = "NORMAL" if ctx["video_durations"] else "DEFAULT"
            p["videoParams"] = vp
            req["taskType"] = "REF2VIDEO"

        gen = self._run_generate_node(
            kind="video",
            model_key=model_key,
            prompt=clean_prompt,
            ref_items=ref_items,
            configure=configure,
            cancel_event=cancel_event,
            on_session_created=on_session_created,
            timeout_sec=1800,
        )

        remote_url = gen["urls"][0]
        filename = f"tensor-{model_key.lower()}-{uuid4().hex[:8]}.mp4"
        target = output_dir / "videos" / filename
        self._download(remote_url, target, timeout=300)

        return {
            "task_id": gen["run_id"],
            "canvas_id": gen["canvas_id"],
            "model": model_key,
            "filename": filename,
            "path": target,
            "remote_url": remote_url,
        }
