from __future__ import annotations

import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote
from uuid import uuid4

import requests

from .ark_client import BUILTIN_PUBLIC_URLS
from .asset_store import mime_for_suffix

DEFAULT_RH_BASE = "https://www.runninghub.cn"
UPLOAD_PATH = "/openapi/v2/media/upload/binary"
QUERY_PATH = "/openapi/v2/query"
H3_T2V_PATH = "/openapi/v2/minimax/hailuo-h3/text-to-video"
H3_I2V_PATH = "/openapi/v2/minimax/hailuo-h3/image-to-video"
H3_MULTIMODAL_PATH = "/openapi/v2/minimax/hailuo-h3/multimodal-to-video"
WAN3_T2V_PATH = "/openapi/v2/alibaba/wan-3.0/text-to-video"
WAN3_I2V_PATH = "/openapi/v2/alibaba/wan-3.0/image-to-video"

SEEDANCE_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "seedance-2.5": {
        "t2v": "/openapi/v2/bytedance/seedance-2.5-token/text-to-video",
        "i2v": "/openapi/v2/bytedance/seedance-2.5-token/image-to-video",
        "multimodal": "/openapi/v2/bytedance/seedance-2.5-token/multimodal-video",
        "max_duration": 30,
        "resolutions": {"480p", "720p", "1080p"},
        "max_images": 30,
        "max_videos": 10,
        "max_audios": 10,
        "is_v25": True,
        "display_name": "bytedance/seedance-2.5 (RunningHub)",
    },
    "seedance-2.5-flash": {
        "t2v": "/openapi/v2/bytedance/seedance-2.5-flash-token/text-to-video",
        "i2v": "/openapi/v2/bytedance/seedance-2.5-flash-token/image-to-video",
        "multimodal": "/openapi/v2/bytedance/seedance-2.5-flash-token/multimodal-video",
        "max_duration": 30,
        "resolutions": {"480p", "720p"},
        "max_images": 10,
        "max_videos": 5,
        "max_audios": 5,
        "is_v25": True,
        "display_name": "bytedance/seedance-2.5-flash (RunningHub)",
    },
    "seedance-2.0": {
        "t2v": "/openapi/v2/rhart-video/sparkvideo-2.0/text-to-video",
        "i2v": "/openapi/v2/rhart-video/sparkvideo-2.0/image-to-video",
        "multimodal": "/openapi/v2/rhart-video/sparkvideo-2.0/multimodal-video",
        "max_duration": 15,
        "resolutions": {"480p", "720p", "1080p"},
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "is_v25": False,
        "display_name": "rhart-video/sparkvideo-2.0 (RunningHub)",
    },
    "seedance-2.0-fast": {
        "t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/text-to-video",
        "i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/image-to-video",
        "multimodal": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/multimodal-video",
        "max_duration": 15,
        "resolutions": {"480p", "720p", "1080p"},
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "is_v25": False,
        "display_name": "rhart-video/sparkvideo-2.0-fast (RunningHub)",
    },
    "seedance-2.0-mini": {
        "t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/text-to-video",
        "i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/image-to-video",
        "multimodal": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/multimodal-video",
        "max_duration": 15,
        "resolutions": {"480p", "720p", "1080p"},
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "is_v25": False,
        "display_name": "seedance-2.0-mini (RunningHub)",
    },
}


def resolve_seedance_spec(model: str) -> dict[str, Any]:
    raw = (model or "").strip().lower()
    for prefix in ("rh:", "runninghub:", "tensor:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    norm = raw.replace("_", "-")
    if norm in SEEDANCE_MODEL_SPECS:
        return SEEDANCE_MODEL_SPECS[norm]
    if "2.5" in norm or "2-5" in norm:
        if "flash" in norm or "fast" in norm:
            return SEEDANCE_MODEL_SPECS["seedance-2.5-flash"]
        return SEEDANCE_MODEL_SPECS["seedance-2.5"]
    if "2.0" in norm or "2-0" in norm:
        if "mini" in norm:
            return SEEDANCE_MODEL_SPECS["seedance-2.0-mini"]
        if "fast" in norm or "flash" in norm:
            return SEEDANCE_MODEL_SPECS["seedance-2.0-fast"]
        return SEEDANCE_MODEL_SPECS["seedance-2.0"]
    return SEEDANCE_MODEL_SPECS["seedance-2.0-mini"]

PENDING_STATUSES = {"QUEUED", "RUNNING", "PENDING", "PROCESSING"}
SUCCESS_STATUSES = {"SUCCESS", "COMPLETED", "SUCCEEDED"}
FAILED_STATUSES = {"FAILED", "FAILURE", "ERROR", "CANCELLED", "CANCELED"}
VALID_RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}


def resolve_rh_key(config: dict[str, Any]) -> str:
    return str(config.get("runninghub_api_key") or os.environ.get("RUNNINGHUB_API_KEY") or "").strip()


def resolve_rh_base_url(config: dict[str, Any]) -> str:
    raw = str(config.get("runninghub_base_url") or DEFAULT_RH_BASE).strip().rstrip("/")
    return raw or DEFAULT_RH_BASE


class RunningHubVideoClient:
    """RunningHub OpenAPI v2 client for Seedance 2.0/2.5 series, Alibaba Wan 3.0 & Hailuo H3."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_RH_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self, json_content: bool = False) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("请先在设置中填写 RunningHub API Key")
        h = {"Authorization": f"Bearer {self.api_key}"}
        if json_content:
            h["Content-Type"] = "application/json"
        return h

    def test_connection(self) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("请先填写 RunningHub API Key")
        resp = requests.post(
            f"{self.base_url}/uc/openapi/accountStatus",
            headers={"Content-Type": "application/json"},
            json={"apikey": self.api_key},
            timeout=15,
        )
        try:
            payload = resp.json()
        except Exception as exc:
            raise ValueError(f"RunningHub 响应异常 (HTTP {resp.status_code})") from exc
        if not resp.ok or payload.get("code") not in (0, 200):
            msg = payload.get("msg") or payload.get("message") or payload.get("errorMessage") or f"HTTP {resp.status_code}"
            raise ValueError(f"鉴权或连接失败：{msg}")
        data = payload.get("data") or {}
        money = data.get("remainMoney")
        currency = data.get("currency") or "CNY"
        points = data.get("remainPoint")
        coins = data.get("remainCoins")
        extras: list[str] = []
        if money is not None:
            extras.append(f"余额 {money} {currency}")
        if points is not None:
            extras.append(f"积分 {int(float(points))}")
        elif coins is not None:
            extras.append(f"RH币 {coins}")
        detail = f"（{' · '.join(extras)}）" if extras else ""
        return {
            "success": True,
            "status_code": resp.status_code,
            "message": f"RunningHub API Key 验证通过{detail}",
            "account": data,
        }

    @staticmethod
    def _unwrap(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError("RunningHub 接口响应格式异常")
        data = payload.get("data")
        if isinstance(data, dict) and any(k in data for k in ("taskId", "status", "results")):
            return data
        return payload

    def upload_local_file(self, path: Path, kind: str) -> str:
        mime = mime_for_suffix(path.suffix, kind)
        with path.open("rb") as fh:
            resp = requests.post(
                f"{self.base_url}{UPLOAD_PATH}",
                headers=self._headers(),
                files={"file": (path.name, fh, mime)},
                timeout=180,
            )
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"RunningHub 素材上传响应异常 (HTTP {resp.status_code})") from exc
        if not resp.ok or payload.get("code") not in (0, 200, None):
            msg = payload.get("message") or payload.get("msg") or resp.text[:200]
            raise RuntimeError(f"RunningHub 素材上传失败：{msg}")
        data = payload.get("data") or {}
        val = (
            data.get("download_url") or data.get("downloadUrl") or data.get("fileUrl") or data.get("url")
            if isinstance(data, dict)
            else data
        )
        val = str(val or "").strip()
        if not val.startswith(("http://", "https://")):
            raise RuntimeError("RunningHub 上传成功但未返回有效 download_url")
        return val

    def _slot_to_rh_url(self, slot: dict[str, Any]) -> str | None:
        url = str(slot.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/api/assets/input/"):
            fname = unquote(url.removeprefix("/api/assets/input/"))
            if fname in BUILTIN_PUBLIC_URLS:
                return BUILTIN_PUBLIC_URLS[fname]
            local_cand = Path(__file__).resolve().parent.parent / "data" / "assets" / fname
            if local_cand.exists() and local_cand.is_file():
                return self.upload_local_file(local_cand, str(slot.get("kind") or "image"))
        if url.startswith("/api/outputs/"):
            rel = unquote(url.removeprefix("/api/outputs/"))
            local_out = Path(__file__).resolve().parent.parent / "data" / "outputs" / rel
            if local_out.exists() and local_out.is_file():
                return self.upload_local_file(local_out, str(slot.get("kind") or "image"))
        raw_path = str(slot.get("path") or "").strip()
        if raw_path:
            p = Path(raw_path)
            if p.exists() and p.is_file():
                if p.name in BUILTIN_PUBLIC_URLS:
                    return BUILTIN_PUBLIC_URLS[p.name]
                return self.upload_local_file(p, str(slot.get("kind") or "image"))
        return None

    def _query_task(self, task_id: str) -> dict[str, Any]:
        resp = requests.post(
            f"{self.base_url}{QUERY_PATH}",
            headers=self._headers(json_content=True),
            json={"taskId": task_id},
            timeout=(15, 60),
        )
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"RunningHub 查询任务异常 (HTTP {resp.status_code})") from exc
        result = self._unwrap(payload)
        if not resp.ok:
            msg = result.get("errorMessage") or payload.get("message") or payload.get("msg") or resp.text[:200]
            raise RuntimeError(f"RunningHub 任务查询失败：{msg}")
        return result

    def _finish_in_background(
        self,
        task_id: str,
        output_dir: Path,
        poll_interval: float,
        timeout_seconds: float,
    ) -> None:
        """Keep polling a paid RunningHub task in background so the finished MP4 is still saved locally."""
        deadline = time.monotonic() + timeout_seconds
        try:
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                result = self._query_task(task_id)
                status = str(result.get("status") or "").upper()
                if status in SUCCESS_STATUSES:
                    items = result.get("results") or []
                    video_url = ""
                    for item in items:
                        if isinstance(item, dict) and (item.get("url") or item.get("fileUrl")):
                            video_url = str(item.get("url") or item.get("fileUrl")).strip()
                            break
                    if video_url:
                        videos_dir = output_dir / "videos"
                        videos_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"rh-{task_id[-10:]}-{uuid4().hex[:6]}.mp4"
                        target = videos_dir / filename
                        with requests.get(video_url, stream=True, timeout=300) as dl:
                            dl.raise_for_status()
                            with target.open("wb") as fh:
                                for chunk in dl.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        fh.write(chunk)
                    return
                if status in FAILED_STATUSES:
                    return
        except Exception:
            return

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
        poll_interval: float = 3.0,
        timeout_seconds: float = 900.0,
        cancel_event: threading.Event | None = None,
        on_task_created: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        m_low = (model or "").lower()
        is_wan3 = "wan-3" in m_low or "wan3" in m_low
        is_h3 = "h3" in m_low or "hailuo" in m_low
        sd_spec = None if (is_wan3 or is_h3) else resolve_seedance_spec(model)

        res_clean = str(resolution or "720p").strip().lower()
        if res_clean not in {"480p", "720p", "1080p"}:
            res_clean = "720p"
        if sd_spec is not None and res_clean not in sd_spec["resolutions"]:
            res_clean = "720p"

        max_dur = sd_spec["max_duration"] if sd_spec is not None else 15
        dur_int = max(4, min(max_dur, int(duration or 5)))
        dur_str = str(dur_int)
        ratio = "adaptive" if aspect in ("", "自适应", "adaptive") else (aspect if aspect in VALID_RATIOS else "adaptive")

        max_imgs = sd_spec["max_images"] if sd_spec is not None else 9
        max_vids = sd_spec["max_videos"] if sd_spec is not None else 3
        max_auds = sd_spec["max_audios"] if sd_spec is not None else 3

        img_urls: list[str] = []
        vid_urls: list[str] = []
        aud_urls: list[str] = []

        for s in slots:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("已中止 RunningHub 视频生成")
            u = self._slot_to_rh_url(s)
            if not u:
                continue
            k = str(s.get("kind") or "image")
            if k == "image" and len(img_urls) < max_imgs:
                img_urls.append(u)
            elif k == "video" and len(vid_urls) < max_vids:
                vid_urls.append(u)
            elif k == "audio" and len(aud_urls) < max_auds:
                aud_urls.append(u)

        if is_wan3:
            wan_res = res_clean.upper()  # 480P / 720P / 1080P
            if mode == "t2v" or not img_urls:
                api_url = f"{self.base_url}{WAN3_T2V_PATH}"
                body: dict[str, Any] = {
                    "prompt": prompt.strip() or "生成一段高质量电影感视频",
                    "resolution": wan_res,
                    "aspectRatio": ratio,
                    "duration": dur_str,
                    "audio": bool(generate_audio),
                    "promptExtend": True,
                    "watermark": False,
                }
            else:
                api_url = f"{self.base_url}{WAN3_I2V_PATH}"
                body = {
                    "prompt": prompt.strip() or "生成一段高质量视频",
                    "firstFrameUrl": img_urls[0],
                    "resolution": wan_res,
                    "duration": dur_str,
                    "audio": bool(generate_audio),
                    "promptExtend": True,
                    "watermark": False,
                }
                if mode == "firstLastFrame" and len(img_urls) >= 2:
                    body["lastFrameUrl"] = img_urls[1]
        elif is_h3:
            h3_res = "2K" if res_clean == "1080p" else "768p"
            if mode == "t2v" or (not img_urls and not vid_urls):
                api_url = f"{self.base_url}{H3_T2V_PATH}"
                body = {
                    "prompt": prompt.strip() or "生成一段高质量视频",
                    "resolution": h3_res,
                    "duration": dur_str,
                    "aspectRatio": ratio,
                }
            elif mode in {"firstFrame", "firstLastFrame"} and img_urls:
                api_url = f"{self.base_url}{H3_I2V_PATH}"
                body = {
                    "prompt": prompt.strip() or None,
                    "resolution": h3_res,
                    "duration": dur_str,
                    "firstFrameUrl": img_urls[0],
                    "lastFrameUrl": img_urls[1] if (mode == "firstLastFrame" and len(img_urls) >= 2) else None,
                }
            else:
                api_url = f"{self.base_url}{H3_MULTIMODAL_PATH}"
                body = {
                    "prompt": prompt.strip() or "生成一段高质量视频",
                    "resolution": h3_res,
                    "duration": dur_str,
                    "imageUrls": img_urls,
                    "videoUrls": vid_urls,
                    "audioUrls": aud_urls,
                    "ratio": ratio,
                }
        else:
            assert sd_spec is not None
            is_v25 = bool(sd_spec["is_v25"])
            use_t2v = mode == "t2v" or (not img_urls and not vid_urls)
            use_i2v = (not use_t2v) and mode in {"firstFrame", "firstLastFrame"} and bool(img_urls)
            if use_t2v:
                api_url = f"{self.base_url}{sd_spec['t2v']}"
                body = {
                    "prompt": prompt.strip() or "生成一段高质量视频",
                    "resolution": res_clean,
                    "duration": dur_str,
                    "generateAudio": bool(generate_audio),
                    "ratio": ratio,
                    "webSearch": False,
                    "returnLastFrame": False,
                    "seed": -1,
                }
            elif use_i2v:
                api_url = f"{self.base_url}{sd_spec['i2v']}"
                body = {
                    "prompt": prompt.strip() or None,
                    "resolution": res_clean,
                    "duration": dur_str,
                    "firstFrameUrl": img_urls[0],
                    "lastFrameUrl": img_urls[1] if (mode == "firstLastFrame" and len(img_urls) >= 2) else None,
                    "generateAudio": bool(generate_audio),
                    "ratio": ratio,
                    "realPersonMode": True,
                    "conversionSlots": ["all"],
                    "returnLastFrame": False,
                    "seed": -1,
                }
            else:
                api_url = f"{self.base_url}{sd_spec['multimodal']}"
                body = {
                    "prompt": prompt.strip() or "生成一段高质量视频",
                    "resolution": res_clean,
                    "duration": dur_str,
                    "imageUrls": img_urls,
                    "videoUrls": vid_urls,
                    "audioUrls": aud_urls,
                    "ratio": ratio,
                    "generateAudio": bool(generate_audio),
                    "realPersonMode": True,
                    "conversionSlots": ["all"],
                    "returnLastFrame": False,
                    "seed": -1,
                }
            if is_v25:
                body["watermark"] = False
                body["bitrateMode"] = "standard"
                body["outputFormat"] = "mp4"
                if not use_i2v:
                    body["webSearch"] = False

        resp = requests.post(api_url, headers=self._headers(json_content=True), json=body, timeout=180)
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"RunningHub 提交任务响应异常 (HTTP {resp.status_code}): {resp.text[:240]}") from exc
        result = self._unwrap(payload)
        if not resp.ok or payload.get("code") not in (0, 200, None) or result.get("errorCode") or result.get("errorMessage"):
            msg = result.get("errorMessage") or result.get("failedReason") or payload.get("message") or payload.get("msg") or resp.text[:240]
            code = result.get("errorCode") or payload.get("code") or ""
            raise RuntimeError(f"RunningHub 提交失败{f' [{code}]' if code else ''}：{msg}")

        task_id = str(result.get("taskId") or "").strip()
        if on_task_created and task_id:
            on_task_created(task_id)

        status = str(result.get("status") or "").upper()
        deadline = time.monotonic() + timeout_seconds
        while status not in SUCCESS_STATUSES:
            if status in FAILED_STATUSES:
                msg = result.get("errorMessage") or result.get("failedReason") or f"状态 {status}"
                raise RuntimeError(f"RunningHub 视频生成失败：{msg}")
            if status and status not in PENDING_STATUSES:
                raise RuntimeError(f"RunningHub 返回异常状态：{status}")
            if not task_id:
                raise RuntimeError(f"RunningHub 未返回 taskId：{payload}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"RunningHub 视频任务超时 (taskId: {task_id})")

            slept = 0.0
            while slept < poll_interval:
                if cancel_event is not None and cancel_event.is_set():
                    # RunningHub cloud tasks cannot be cancelled; keep downloading in background so paid video isn't lost
                    threading.Thread(
                        target=self._finish_in_background,
                        args=(task_id, output_dir, poll_interval, max(60.0, deadline - time.monotonic())),
                        daemon=True,
                    ).start()
                    raise InterruptedError(f"已转入后台继续接收（RunningHub 任务 {task_id} 完成后将自动存入「历史视频」）")
                step = min(0.5, poll_interval - slept)
                time.sleep(step)
                slept += step

            result = self._query_task(task_id)
            status = str(result.get("status") or "").upper()

        items = result.get("results") or []
        video_url = ""
        for item in items:
            if isinstance(item, dict) and (item.get("url") or item.get("fileUrl")):
                video_url = str(item.get("url") or item.get("fileUrl")).strip()
                break
        if not video_url:
            raise RuntimeError(f"RunningHub 任务 {task_id} 成功但未返回视频地址")

        videos_dir = output_dir / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        filename = f"rh-{task_id[-10:]}-{uuid4().hex[:6]}.mp4"
        target = videos_dir / filename
        with requests.get(video_url, stream=True, timeout=300) as dl:
            dl.raise_for_status()
            with target.open("wb") as fh:
                for chunk in dl.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)

        return {
            "task_id": task_id,
            "model": (
                "alibaba/wan-3.0 (RunningHub)"
                if is_wan3
                else (
                    "minimax/hailuo-h3 (RunningHub)"
                    if is_h3
                    else (sd_spec["display_name"] if sd_spec is not None else "seedance-2.0-mini (RunningHub)")
                )
            ),
            "remote_url": video_url,
            "path": target,
            "filename": filename,
            "usage": result.get("usage") or {},
        }
