from __future__ import annotations

import base64
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import requests

from .volc_assets import VolcAssetManager

DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

VIDEO_MODEL_MAP: dict[str, str] = {
    "seedance-2.5": "doubao-seedance-2-5-260628",
    "seedance-2.5-flash": "doubao-seedance-2-5-flash-261115",
    "seedance-2.0": "doubao-seedance-2-0-260128",
    "seedance-2.0-fast": "doubao-seedance-2-0-fast-260128",
    "seedance-2.0-mini": "doubao-seedance-2-0-mini-260615",
}

IMAGE_MODEL_MAP: dict[str, str] = {
    "seedream-5.5": "doubao-seedream-5-5-261028",
    "seedream-5-pro": "doubao-seedream-5-0-pro-260628",
    "seedream-5.0-pro": "doubao-seedream-5-0-pro-260628",
    "seedream-4.5": "doubao-seedream-4-5-251128",
}

IMAGE_SIZE_MAP: dict[str, str] = {
    "1:1": "2048x2048",
    "16:9": "2560x1440",
    "9:16": "1440x2560",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
}

# Built-in public HTTPS mirrors for local bundled RHTV assets (Ark requires web URL for reference_video / reference_audio)
BUILTIN_PUBLIC_URLS: dict[str, str] = {
    "rhtv-character-3view.png": "https://rh-canvas-files.xiaoyaoyou.com/90ee74089409a18d9e5b7ee558255424/output/d5b9cf30-ec73-470c-9d25-0996506599d4.png",
    "rhtv-face-ref.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/ccab4322a1594306b8c177ba5ef446bf.png",
    "rhtv-outfit-ref.jpg": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/80dd5c8b13124d7ca251ae51c15fffae.jpg",
    "rhtv-depth-motion.mp4": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/af282f70d937405f897ff35bc6b408b9.mp4",
    "rhtv-bgm.mp3": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/d51a5a24b40b41729962f1215a92502e.mp3",
    "rhtv-scene-1.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/39c17914df814de6980b9db54d0bbe1d.png",
    "rhtv-scene-2.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/4897fe2477e8467b8796ac40f4e19d30.png",
    "rhtv-scene-3.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/150e92d8bd16445eb65ec4c62c33ef29.png",
    "rhtv-scene-4.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/0cea4decb0114152ab9f8b1e72e31ad7.png",
    "rhtv-scene-5.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/f29e53d9b225421d98730fb96c49dce1.png",
    "rhtv-scene-6.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/14d1d88016794f0a8fde778f5e99cb32.png",
    "rhtv-scene-7.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/a1cb8fda8ab74666b8406d889398a005.png",
    "rhtv-scene-8.png": "https://rh-canvas-files.xiaoyaoyou.com/a9c68a3afadff09e6aa7cc91906a6ab9/uploads/2026-11-10/a72aa7594971408a8cffecab23b164fc.png",
}


def resolve_ark_key(config: dict[str, Any]) -> str:
    return str(config.get("ark_api_key") or os.environ.get("ARK_API_KEY") or "").strip()


def resolve_ark_base_url(config: dict[str, Any]) -> str:
    raw = str(config.get("ark_base_url") or DEFAULT_ARK_BASE_URL).strip().rstrip("/")
    if not raw:
        return DEFAULT_ARK_BASE_URL
    if not raw.endswith("/api/v3"):
        raw = f"{raw}/api/v3"
    return raw


def _extract_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            code = err.get("code") or ""
            msg = err.get("message") or str(err)
            return f"[{code}] {msg}" if code else msg
        if isinstance(err, str):
            return err
        return str(data)
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:240]}"


def _file_to_data_url(path: Path, kind: str) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = {"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}.get(kind, "application/octet-stream")
    raw = path.read_bytes()
    # Keep multimodal JSON body compact when encoding large local PNGs for Ark API
    if kind == "image" and len(raw) > 1_200_000:
        try:
            import io
            from PIL import Image

            with Image.open(path) as im:
                rgb = im.convert("RGB")
                rgb.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                rgb.save(buf, format="JPEG", quality=88, optimize=True)
                raw = buf.getvalue()
                mime = "image/jpeg"
        except Exception:
            pass
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _resolve_local_asset_path(url: str, path_str: str) -> Path | None:
    if path_str:
        p = Path(path_str)
        if p.exists() and p.is_file():
            return p
    if url.startswith("/api/assets/input/"):
        from urllib.parse import unquote
        name = unquote(url.removeprefix("/api/assets/input/"))
        cand = Path(__file__).resolve().parent.parent / "data" / "assets" / name
        if cand.exists() and cand.is_file():
            return cand
    return None


def _slot_to_ark_url(slot: dict[str, Any]) -> str | None:
    asset_id = str(slot.get("arkAssetId") or "").strip()
    if asset_id:
        return asset_id if asset_id.startswith("asset://") else f"asset://{asset_id}"

    url = str(slot.get("url") or "").strip()
    if url.startswith(("http://", "https://", "data:", "asset://")):
        return url
    if url.startswith("asset-"):
        return f"asset://{url}"

    # Check built-in public URL mirror first (fast + satisfies Ark's web-URL requirement for video/audio)
    if url.startswith("/api/assets/input/"):
        from urllib.parse import unquote
        fname = unquote(url.removeprefix("/api/assets/input/"))
        if fname in BUILTIN_PUBLIC_URLS:
            return BUILTIN_PUBLIC_URLS[fname]

    kind = str(slot.get("kind") or "image")
    p = _resolve_local_asset_path(url, str(slot.get("path") or "").strip())
    if p is not None:
        if p.name in BUILTIN_PUBLIC_URLS:
            return BUILTIN_PUBLIC_URLS[p.name]
        if kind in {"video", "audio"}:
            raise ValueError(
                f"火山方舟要求参考{('视频' if kind == 'video' else '音频')}必须提供公网 HTTPS URL（不支持本地 Base64）：{p.name}"
            )
        return _file_to_data_url(p, kind)
    return None


class ArkClient:
    """Official Volcengine Ark (火山方舟) API client for Seedance video & Seedream image generation."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_ARK_BASE_URL) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api/v3"):
            self.base_url = f"{self.base_url}/api/v3"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def test_connection(self) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("请先填写火山方舟 API Key")
        resp = requests.get(
            f"{self.base_url}/contents/generations/tasks",
            headers=self._headers,
            params={"page_size": 1},
            timeout=15,
        )
        if resp.status_code == 401 or resp.status_code == 403:
            raise ValueError(f"鉴权失败：{_extract_error(resp)}")
        if resp.status_code >= 400:
            raise ValueError(f"连接异常：{_extract_error(resp)}")
        return {"success": True, "status_code": resp.status_code, "message": "火山方舟 API Key 验证通过，可正常调用 Seedance / Seedream"}

    def cancel_task(self, task_id: str) -> bool:
        if not task_id or not self.api_key:
            return False
        try:
            resp = requests.delete(
                f"{self.base_url}/contents/generations/tasks/{task_id}",
                headers=self._headers,
                timeout=15,
            )
            return resp.status_code < 400
        except Exception:
            return False

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
        poll_interval: float = 4.0,
        timeout_seconds: float = 600.0,
        cancel_event: threading.Event | None = None,
        on_task_created: Callable[[str], None] | None = None,
        asset_manager: VolcAssetManager | None = None,
    ) -> dict[str, Any]:
        resolved_model = VIDEO_MODEL_MAP.get(model, model)

        content: list[dict[str, Any]] = []
        if prompt.strip():
            content.append({"type": "text", "text": prompt.strip()})

        img_slots = [s for s in slots if s.get("kind") == "image"]
        vid_slots = [s for s in slots if s.get("kind") == "video"]
        aud_slots = [s for s in slots if s.get("kind") == "audio"]

        if mode == "firstFrame":
            if img_slots:
                u = _slot_to_ark_url(img_slots[0])
                if u:
                    content.append({"type": "image_url", "image_url": {"url": u}, "role": "first_frame"})
        elif mode == "firstLastFrame":
            if img_slots:
                u1 = _slot_to_ark_url(img_slots[0])
                if u1:
                    content.append({"type": "image_url", "image_url": {"url": u1}, "role": "first_frame"})
            if len(img_slots) >= 2:
                u2 = _slot_to_ark_url(img_slots[1])
                if u2:
                    content.append({"type": "image_url", "image_url": {"url": u2}, "role": "last_frame"})
        elif mode == "omni":
            for s in img_slots[:9]:
                u = _slot_to_ark_url(s)
                if u:
                    content.append({"type": "image_url", "image_url": {"url": u}, "role": "reference_image"})
            for s in vid_slots[:3]:
                u = _slot_to_ark_url(s)
                if u:
                    content.append({"type": "video_url", "video_url": {"url": u}, "role": "reference_video"})
            for s in aud_slots[:3]:
                u = _slot_to_ark_url(s)
                if u:
                    content.append({"type": "audio_url", "audio_url": {"url": u}, "role": "reference_audio"})

        if not content:
            raise ValueError("提示词与参考素材不能同时为空")

        ratio = "adaptive" if aspect in ("", "自适应", "adaptive") else aspect
        payload: dict[str, Any] = {
            "model": resolved_model,
            "content": content,
            "resolution": resolution or "720p",
            "duration": max(4, min(30, int(duration or 5))),
            "ratio": ratio,
            "generate_audio": bool(generate_audio),
            "watermark": False,
        }

        # Pre-substitute any cached asset:// IDs if asset_manager is available
        if asset_manager is not None:
            for item in content:
                if item.get("type") == "image_url":
                    img_u = str((item.get("image_url") or {}).get("url") or "")
                    if img_u.startswith(("http://", "https://")):
                        cached_id = asset_manager.get_cached_asset_id(img_u)
                        if cached_id:
                            item["image_url"]["url"] = f"asset://{cached_id}"

        create_resp: requests.Response | None = None
        for attempt in range(4):
            create_resp = requests.post(
                f"{self.base_url}/contents/generations/tasks",
                headers=self._headers,
                json=payload,
                timeout=120,
            )
            if create_resp.status_code < 400:
                break

            err_text = _extract_error(create_resp)
            # Auto-enroll real-person portrait into Volcengine Private Asset Library & retry with asset://
            if (
                asset_manager is not None
                and "InputImageSensitiveContentDetected" in err_text
                and attempt < 3
            ):
                m = re.search(r"content\[(\d+)\]", err_text)
                if m:
                    idx = int(m.group(1))
                    if 0 <= idx < len(content) and content[idx].get("type") == "image_url":
                        orig_url = str((content[idx].get("image_url") or {}).get("url") or "")
                        if orig_url.startswith(("http://", "https://")):
                            asset_id = asset_manager.enroll_asset(
                                public_url=orig_url,
                                name=f"portrait-{idx}",
                                asset_type="Image",
                                cancel_event=cancel_event,
                            )
                            content[idx]["image_url"]["url"] = f"asset://{asset_id}"
                            continue
                        raise ValueError(
                            f"火山方舟拦截了真人图 content[{idx}]，且该图为本地文件（无公网 URL），"
                            f"无法自动调用 CreateAsset 录入私域资产库。原错误：{err_text}"
                        )
            raise ValueError(f"火山方舟创建视频任务失败：{err_text}")

        assert create_resp is not None

        task_id = str(create_resp.json().get("id") or "").strip()
        if not task_id:
            raise ValueError(f"火山方舟未返回任务 ID：{create_resp.text[:240]}")

        if on_task_created:
            on_task_created(task_id)

        deadline = time.monotonic() + timeout_seconds
        last_status = "queued"
        task_data: dict[str, Any] = {}

        while time.monotonic() < deadline:
            # Sleep in 0.5s slices so cancellation responds immediately
            slept = 0.0
            while slept < poll_interval:
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel_task(task_id)
                    raise InterruptedError(f"已中止火山方舟视频任务 ({task_id})")
                step = min(0.5, poll_interval - slept)
                time.sleep(step)
                slept += step

            if cancel_event is not None and cancel_event.is_set():
                self.cancel_task(task_id)
                raise InterruptedError(f"已中止火山方舟视频任务 ({task_id})")

            poll_resp = requests.get(
                f"{self.base_url}/contents/generations/tasks/{task_id}",
                headers=self._headers,
                timeout=30,
            )
            if poll_resp.status_code >= 400:
                raise ValueError(f"火山方舟查询任务 {task_id} 失败：{_extract_error(poll_resp)}")
            task_data = poll_resp.json()
            last_status = str(task_data.get("status") or "").lower()
            if last_status == "succeeded":
                break
            if last_status in {"failed", "cancelled", "expired"}:
                err_obj = task_data.get("error")
                err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj or last_status)
                raise ValueError(f"火山方舟任务失败 ({task_id}): {err_msg}")
        else:
            raise TimeoutError(f"火山方舟视频任务超时 ({task_id}，当前状态: {last_status})")

        content_out = task_data.get("content") or {}
        video_url = str(content_out.get("video_url") or "").strip()
        if not video_url:
            raise ValueError(f"任务 {task_id} 已完成但未返回 video_url")

        videos_dir = output_dir / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        filename = f"ark-{task_id[-10:]}-{uuid4().hex[:6]}.mp4"
        target = videos_dir / filename
        with requests.get(video_url, stream=True, timeout=180) as dl:
            dl.raise_for_status()
            with target.open("wb") as fh:
                for chunk in dl.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)

        return {
            "task_id": task_id,
            "model": resolved_model,
            "remote_url": video_url,
            "path": target,
            "filename": filename,
            "raw": task_data,
        }

    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        aspect: str,
        output_dir: Path,
    ) -> dict[str, Any]:
        resolved_model = IMAGE_MODEL_MAP.get(model, model)
        size = IMAGE_SIZE_MAP.get(aspect, "2560x1440")
        payload = {
            "model": resolved_model,
            "prompt": prompt.strip() or "高质感电影感画面",
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        resp = requests.post(
            f"{self.base_url}/images/generations",
            headers=self._headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code >= 400:
            raise ValueError(f"火山方舟生图失败：{_extract_error(resp)}")

        data = resp.json()
        items = data.get("data") or []
        if not items:
            raise ValueError(f"火山方舟未返回图片结果：{resp.text[:240]}")

        first = items[0]
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        filename = f"ark-img-{uuid4().hex[:10]}.png"
        target = images_dir / filename

        if first.get("url"):
            with requests.get(str(first["url"]), stream=True, timeout=120) as dl:
                dl.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in dl.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            fh.write(chunk)
        elif first.get("b64_json"):
            target.write_bytes(base64.b64decode(first["b64_json"]))
        else:
            raise ValueError("火山方舟返回图片格式无法解析")

        return {
            "model": resolved_model,
            "size": size,
            "path": target,
            "filename": filename,
        }
