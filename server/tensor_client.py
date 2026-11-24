from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
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
)

DEFAULT_TENSOR_BASE = "https://openapi.tensorart.me/canvas-openapi"
TUSI_TENSOR_BASE = "https://openapi.tusiart.cn/canvas-openapi"

TENSOR_IMAGE_MODELS: dict[str, str] = {
    "tensor:NANO_BANANA_2": "NANO_BANANA_2",
    "tensor:NANO_BANANA_PRO": "NANO_BANANA_PRO",
    "tensor:CHATGPT_IMAGE_2": "CHATGPT_IMAGE_2",
    "tensor:MIDJOURNEY_V7": "MIDJOURNEY_V7",
    "tensor:MIDJOURNEY_NIJI_V7": "MIDJOURNEY_NIJI_V7",
    "tensor:WAN_2_7_IMAGE": "WAN_2_7_IMAGE",
    "tensor:WAN_2_7_IMAGE_PRO": "WAN_2_7_IMAGE_PRO",
}

TENSOR_VIDEO_MODELS: dict[str, str] = {
    "tensor:SEEDANCE_2_5": "DEFAULT",
    "tensor:SEEDANCE_2_5_FLASH": "DEFAULT",
    "tensor:SEEDANCE_2_0": "SEEDANCE_2_0",
    "tensor:SEEDANCE_2_0_FAST": "SEEDANCE_2_0_FAST",
    "tensor:KLING_V3": "KLING_V3",
    "tensor:KLING_V3_OMNI": "KLING_V3_OMNI",
    "tensor:VIDU_Q3_PRO": "VIDU_Q3_PRO",
    "tensor:PIXVERSE_V6": "PIXVERSE_V6",
    "tensor:HAPPYHORSE_1": "HAPPYHORSE_1",
    "tensor:WAN_2_7_T2V": "WAN_2_7_T2V",
    "tensor:WAN_2_7_I2V": "WAN_2_7_I2V",
    "tensor:WAN_2_7_R2V": "WAN_2_7_R2V",
}

TENSOR_PROMPT_MODEL_HINTS: dict[str, str] = {
    "tensor:SEEDANCE_2_5": "Seedance 2.5",
    "tensor:SEEDANCE_2_5_FLASH": "Seedance 2.5 Flash",
}

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


def resolve_tensor_key(config: dict[str, Any]) -> str:
    return str(config.get("tensor_api_key") or os.environ.get("TENSOR_CANVAS_API_KEY") or "").strip()


def resolve_tensor_base_url(config: dict[str, Any]) -> str:
    raw = str(config.get("tensor_base_url") or DEFAULT_TENSOR_BASE).strip().rstrip("/")
    return raw or DEFAULT_TENSOR_BASE


def is_tensor_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("tensor:")


def resolve_pinned_model(model: str, kind: str = "image", has_refs: bool = False) -> str:
    raw = str(model or "").strip()
    if raw in TENSOR_IMAGE_MODELS:
        return TENSOR_IMAGE_MODELS[raw]
    if raw in TENSOR_VIDEO_MODELS:
        return TENSOR_VIDEO_MODELS[raw]
    if raw.lower().startswith("tensor:"):
        return raw.split(":", 1)[1].strip().upper()

    low = raw.lower()
    if kind == "video":
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
            return "WAN_2_7_I2V" if has_refs else "WAN_2_7_T2V"
        return "SEEDANCE_2_0_FAST"
    return "NANO_BANANA_2"


class TensorCanvasClient:
    """Tensor / Forge Canvas OpenAPI v1 client (supports tensorart.me and tusiart.cn)."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_TENSOR_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self, json_content: bool = True, sse: bool = False) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("请先在设置中填写 Tensor (Forge) Access Key")
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Request-Id": str(uuid4()),
        }
        if json_content:
            h["Content-Type"] = "application/json"
        if sse:
            h["Accept"] = "text/event-stream"
        return h

    def _post_json(self, path: str, body: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        resp = requests.post(
            f"{self.base_url}/v1{path}",
            headers=self._headers(json_content=True),
            json=body,
            timeout=timeout,
        )
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

    def test_connection(self) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("请先填写 Tensor (Forge) Access Key")
        data = self._post_json("/canvas/list", {"size": 5}, timeout=15)
        canvas_count = len(data.get("canvasList") or [])
        skills_count = 0
        try:
            resp = requests.get(
                f"{self.base_url}/v1/agent/skills",
                headers=self._headers(json_content=False),
                timeout=15,
            )
            if resp.ok:
                sp = resp.json()
                if str(sp.get("code")) == "0":
                    skills_count = len((sp.get("data") or {}).get("skills") or [])
        except Exception:
            pass
        site = "吐司国内" if "tusiart" in self.base_url else "Tensor 海外"
        return {
            "success": True,
            "status_code": 200,
            "message": f"Tensor ({site}) Access Key 验证通过（画布 {canvas_count} 个 · 可用 Skill {skills_count} 个）",
        }

    def create_canvas(self, name: str = "RH Canvas Run") -> str:
        data = self._post_json("/canvas/create", {"name": name[:80]})
        canvas_id = str((data.get("canvas") or {}).get("id") or "").strip()
        if not canvas_id:
            raise RuntimeError("Tensor 创建画布未返回 canvasId")
        return canvas_id

    def create_session(self, canvas_id: str, title: str = "节点生成") -> str:
        data = self._post_json("/agent/session/create", {"canvasId": canvas_id, "title": title[:80]})
        session_id = str((data.get("session") or {}).get("sessionId") or "").strip()
        if not session_id:
            raise RuntimeError("Tensor 创建会话未返回 sessionId")
        return session_id

    def cancel_chat(self, canvas_id: str, session_id: str) -> bool:
        if not canvas_id or not session_id:
            return False
        try:
            data = self._post_json("/agent/chat/cancel", {"canvasId": canvas_id, "sessionId": session_id}, timeout=10)
            return bool(data.get("aborted"))
        except Exception:
            return False

    def _resolve_local_file(self, item: dict[str, Any]) -> Path | None:
        raw_path = str(item.get("path") or "").strip()
        if raw_path:
            p = Path(raw_path)
            if p.exists() and p.is_file():
                return p
        url = str(item.get("url") or "").strip()
        root = Path(__file__).resolve().parent.parent / "data"
        if url.startswith("/api/assets/input/"):
            fname = unquote(url.removeprefix("/api/assets/input/"))
            cand = root / "assets" / fname
            if cand.exists() and cand.is_file():
                return cand
        if url.startswith("/api/outputs/"):
            rel = unquote(url.removeprefix("/api/outputs/"))
            cand = root / "outputs" / rel
            if cand.exists() and cand.is_file():
                return cand
        return None

    def upload_file(self, file_path: Path) -> dict[str, Any]:
        ext = file_path.suffix.lower()
        file_type = EXT_TO_FILE_TYPE.get(ext)
        if not file_type:
            raise ValueError(f"Tensor 不支持的文件类型：{ext or file_path.name}")
        data = self._post_json("/media/upload/create", {"fileType": file_type}, timeout=20)
        media_id = str(data.get("mediaId") or "").strip()
        upload_url = str(data.get("uploadUrl") or "").strip()
        display_url = str(data.get("displayUrl") or "").strip()
        if not upload_url or not display_url:
            raise RuntimeError("Tensor 上传地址申请失败")

        mime = mime_for_suffix(file_path.suffix, kind_for_suffix(file_path.suffix))
        with file_path.open("rb") as fh:
            put_resp = requests.put(upload_url, headers={"Content-Type": mime}, data=fh, timeout=180)
        if not put_resp.ok:
            raise RuntimeError(f"上传素材到 Tensor 存储失败 (HTTP {put_resp.status_code})")
        file_obj: dict[str, Any] = {
            "url": display_url,
            "mimeType": mime,
            "type": "URL",
        }
        if media_id:
            file_obj["assetId"] = media_id
        return file_obj

    def _consume_sse(
        self,
        url: str,
        body: dict[str, Any],
        artifacts: list[dict[str, Any]],
        texts: list[str],
        cancel_event: threading.Event | None = None,
        canvas_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Consume a Canvas OpenAPI SSE stream. Returns status + pending tool_intent if any."""
        pending_intent: dict[str, Any] | None = None
        done_status = ""
        error_msg = ""

        with requests.post(
            url,
            headers=self._headers(json_content=True, sse=True),
            json=body,
            stream=True,
            timeout=(20, 600),
        ) as resp:
            if not resp.ok:
                try:
                    err_payload = resp.json()
                    msg = err_payload.get("message") or err_payload.get("msg") or resp.text[:300]
                except Exception:
                    msg = resp.text[:300]
                raise RuntimeError(f"Tensor 流式请求失败 (HTTP {resp.status_code})：{msg}")

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype and "text/event-stream" not in ctype:
                payload = resp.json()
                if str(payload.get("code")) != "0":
                    raise RuntimeError(f"Tensor 业务失败：{payload.get('message') or payload}")
                return {"status": "SUCCESS", "pending_intent": None, "error": ""}

            event_name = ""
            data_lines: list[str] = []

            def flush_event() -> bool:
                nonlocal pending_intent, done_status, error_msg, event_name, data_lines
                if not data_lines:
                    event_name = ""
                    return False
                raw_data = "\n".join(data_lines).strip()
                ev = event_name.strip()
                event_name = ""
                data_lines = []
                if not raw_data:
                    return False
                try:
                    obj = json.loads(raw_data)
                except Exception:
                    return False
                etype = str(obj.get("event_type") or ev or "").upper()

                if etype == "DELTA":
                    delta = obj.get("delta") or {}
                    c = delta.get("content")
                    if isinstance(c, str) and c:
                        texts.append(c)
                elif etype == "ARTIFACT":
                    art = obj.get("artifact")
                    if isinstance(art, dict):
                        inner = art.get("artifact") if isinstance(art.get("artifact"), dict) else art
                        artifacts.append(inner)
                elif etype == "TOOL_INTENT_PROPOSED":
                    ti = obj.get("tool_intent") or {}
                    tool_call_id = str(obj.get("tool_call_id") or ti.get("toolCallId") or ti.get("tool_call_id") or "").strip()
                    billing = ti.get("estimatedBilling") or ti.get("estimated_billing") or {}
                    charged = billing.get("chargedEnergy")
                    if charged is None:
                        charged = billing.get("charged_energy")
                    sufficient = ti.get("sufficient")
                    pending_intent = {
                        "tool_call_id": tool_call_id,
                        "charged_energy": charged,
                        "sufficient": sufficient,
                        "raw": ti,
                    }
                elif etype == "MESSAGE_DONE":
                    md = obj.get("message_done") or {}
                    done_status = str(md.get("status") or "").upper()
                    return True
                elif etype == "ERROR":
                    err = obj.get("error") or {}
                    error_msg = str(err.get("message") or err.get("errorMessage") or json.dumps(err, ensure_ascii=False))
                    return True
                return False

            for raw_line in resp.iter_lines(decode_unicode=True):
                if cancel_event and cancel_event.is_set():
                    self.cancel_chat(canvas_id, session_id)
                    raise RuntimeError("任务已取消")
                if raw_line is None:
                    continue
                line = raw_line.rstrip("\r\n")
                if not line:
                    should_stop = flush_event()
                    if should_stop:
                        break
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            flush_event()

        return {
            "status": done_status,
            "pending_intent": pending_intent,
            "error": error_msg,
        }

    def _extract_artifact_urls(self, artifacts: list[dict[str, Any]], expected_kind: str) -> list[str]:
        preferred: list[str] = []
        fallback: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                # Prefer signed/download URLs (rawUrl on R2 without signature returns 403)
                for key in ("downloadUrl", "download_url", "signedUrl", "signed_url"):
                    val = node.get(key)
                    if isinstance(val, str) and val.startswith(("http://", "https://")):
                        preferred.append(val)
                for key in ("displayUrl", "display_url", "mediaUrl", "media_url", "url"):
                    val = node.get(key)
                    if isinstance(val, str) and val.startswith(("http://", "https://")):
                        fallback.append(val)
                for k, v in node.items():
                    if k in ("rawUrl", "raw_url"):
                        continue
                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for art in artifacts:
            walk(art)

        dedup = list(dict.fromkeys([*preferred, *fallback]))
        if expected_kind == "video":
            vid = [u for u in dedup if any(ext in u.lower().split("?")[0] for ext in (".mp4", ".mov", ".webm"))]
            return vid or dedup
        img = [u for u in dedup if any(ext in u.lower().split("?")[0] for ext in (".png", ".jpg", ".jpeg", ".webp"))]
        return img or dedup

    def _poll_workflow_output(
        self,
        canvas_id: str,
        expected_kind: str,
        cancel_event: threading.Event | None = None,
        timeout_sec: int = 300,
    ) -> list[str]:
        """Poll /workflow/graph until generated node finishes and yields signed media URLs."""
        target_type = expected_kind.upper()
        deadline = time.monotonic() + timeout_sec
        saw_node = False

        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("任务已取消")
            try:
                graph = self._post_json("/workflow/graph", {"canvasId": canvas_id}, timeout=20)
            except Exception:
                time.sleep(2.5)
                continue

            nodes = (graph.get("workflow") or {}).get("nodes") or []
            gen_nodes = [
                n for n in nodes
                if str(n.get("type") or "").upper() == target_type
                or str(n.get("subType") or "").upper() == "GENERATE"
            ]
            if not gen_nodes:
                if saw_node:
                    break
                # Wait briefly in case node creation lags slightly behind SSE end
                if time.monotonic() > deadline - timeout_sec + 12:
                    break
                time.sleep(2.0)
                continue

            saw_node = True
            urls: list[str] = []
            running_count = 0
            failed_msgs: list[str] = []

            for n in gen_nodes:
                st = str(n.get("status") or "").upper()
                if st == "SUCCESS":
                    gen_data = (n.get("data") or {}).get("generation") or {}
                    cur_out = gen_data.get("currentOutput")
                    if isinstance(cur_out, dict):
                        urls.extend(self._extract_artifact_urls([cur_out], expected_kind))
                    urls.extend(self._extract_artifact_urls([n.get("data") or {}], expected_kind))
                elif st in ("RUNNING", "READY", "IDLE", "PENDING", "QUEUED"):
                    running_count += 1
                elif st in ("FAILED", "INVALID", "ERROR"):
                    failed_msgs.append(f"{n.get('title') or n.get('id')}: {st}")

            urls = list(dict.fromkeys(urls))
            if urls:
                return urls
            if running_count == 0:
                if failed_msgs:
                    raise RuntimeError(f"Tensor 画布节点生成失败：{'; '.join(failed_msgs)}")
                break

            time.sleep(2.5)

        return []

    def _upload_with_duration_guard(
        self,
        file_path: Path,
        kind: str,
        min_sec: float = MEDIA_MIN_DURATION_SEC,
        max_sec: float = MEDIA_MAX_DURATION_SEC,
    ) -> dict[str, Any]:
        """Upload media; normalize video/audio duration into [min_sec, max_sec] (Seedance 2-15s limit)."""
        if kind not in ("video", "audio"):
            return self.upload_file(file_path)
        with tempfile.TemporaryDirectory(prefix="tensor-dur-") as tmpdir:
            norm_path, _adjusted, _dur = normalize_media_duration_file(
                file_path,
                kind,
                min_sec=min_sec,
                max_sec=max_sec,
                output_dir=Path(tmpdir),
            )
            return self.upload_file(norm_path)

    def _stage_media_nodes(
        self,
        canvas_id: str,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Upload local media files and add them as persistent canvas nodes via /workflow/actions."""
        staged: list[dict[str, Any]] = []
        nodes_in: list[dict[str, Any]] = []
        kind_to_type = {
            "image": ("IMAGE", "image", "PROMPT_REFERENCE_TYPE_IMAGE"),
            "video": ("VIDEO", "video", "PROMPT_REFERENCE_TYPE_VIDEO"),
            "audio": ("AUDIO", "audio", "PROMPT_REFERENCE_TYPE_AUDIO"),
        }
        for idx, item in enumerate(items):
            kind = str(item.get("kind") or "image").lower()
            if kind not in kind_to_type:
                continue
            local_p = self._resolve_local_file(item)
            if local_p is None:
                continue
            ntype, branch, ref_type = kind_to_type[kind]
            up = self._upload_with_duration_guard(local_p, kind)
            node_id = f"ref-{kind}-{idx + 1}"
            tag = str(item.get("tag") or f"@{kind}{idx + 1}").strip()
            title = str(item.get("name") or item.get("label") or tag or local_p.stem)[:60]
            nodes_in.append(
                {
                    "id": node_id,
                    "type": ntype,
                    "subType": "UPLOAD",
                    "title": f"{tag} {title}".strip(),
                    "position": {"x": 80 + (idx % 4) * 220, "y": 80 + (idx // 4) * 220},
                    "data": {branch: {"url": up["url"]}},
                }
            )
            staged.append(
                {
                    "node_id": node_id,
                    "tag": tag,
                    "kind": kind,
                    "ref_type": ref_type,
                    "display_name": f"{tag}({title})" if title and title != tag else tag,
                    "url": up["url"],
                }
            )

        if nodes_in:
            res = self._post_json(
                "/workflow/actions",
                {"canvasId": canvas_id, "actions": [{"action": "add", "nodes": nodes_in}]},
                timeout=60,
            )
            created_nodes = (res.get("created") or {}).get("nodes") or []
            if len(created_nodes) == len(staged):
                for s, cn in zip(staged, created_nodes):
                    if cn.get("id"):
                        s["node_id"] = str(cn["id"])
        return staged

    @staticmethod
    def _build_workflow_context(
        message: str,
        staged: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Convert @图片N / @视频N / @音频N tags into structured workflowContext promptBlocks."""
        if not staged:
            return None
        by_tag = {s["tag"]: s for s in staged if s.get("tag")}
        selected_ids = [s["node_id"] for s in staged]
        blocks: list[dict[str, Any]] = []

        pattern = re.compile(r"@(?:图片|视频|音频)\d+")
        pos = 0
        mentioned_nodes: set[str] = set()
        for m in pattern.finditer(message):
            if m.start() > pos:
                blocks.append({"type": "PROMPT_BLOCK_TYPE_TEXT", "text": message[pos : m.start()]})
            tag = m.group(0)
            target = by_tag.get(tag)
            if target:
                mentioned_nodes.add(target["node_id"])
                blocks.append(
                    {
                        "type": "PROMPT_BLOCK_TYPE_MENTION",
                        "referenceType": target["ref_type"],
                        "displayName": target["display_name"],
                        "nodeKey": target["node_id"],
                    }
                )
            else:
                blocks.append({"type": "PROMPT_BLOCK_TYPE_TEXT", "text": tag})
            pos = m.end()
        if pos < len(message):
            blocks.append({"type": "PROMPT_BLOCK_TYPE_TEXT", "text": message[pos:]})

        unmentioned = [s for s in staged if s["node_id"] not in mentioned_nodes]
        if unmentioned:
            blocks.append({"type": "PROMPT_BLOCK_TYPE_TEXT", "text": "\n参考素材节点："})
            for s in unmentioned:
                blocks.append(
                    {
                        "type": "PROMPT_BLOCK_TYPE_MENTION",
                        "referenceType": s["ref_type"],
                        "displayName": s["display_name"],
                        "nodeKey": s["node_id"],
                    }
                )

        return {
            "selectedNodeIds": selected_ids,
            "promptBlocks": blocks[:256],
        }

    def _run_agent_generation(
        self,
        *,
        expected_kind: str,
        pinned_model: str,
        message: str,
        ref_items: list[dict[str, Any]],
        cancel_event: threading.Event | None = None,
        on_session_created: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        canvas_id = self.create_canvas(name=f"RH-{expected_kind}-{pinned_model}")
        staged = self._stage_media_nodes(canvas_id, ref_items)
        wf_ctx = self._build_workflow_context(message, staged)
        session_id = self.create_session(canvas_id, title=pinned_model)
        if on_session_created:
            on_session_created(canvas_id, session_id)

        artifacts: list[dict[str, Any]] = []
        texts: list[str] = []

        payload: dict[str, Any] = {
            "canvasId": canvas_id,
            "sessionId": session_id,
            "message": message,
            "pinnedModel": pinned_model,
        }
        if wf_ctx:
            payload["workflowContext"] = wf_ctx

        res: dict[str, Any] = {}
        for attempt in range(2):
            artifacts.clear()
            texts.clear()
            if attempt > 0:
                session_id = self.create_session(canvas_id, title=f"{pinned_model}-retry")
                payload["sessionId"] = session_id
                if on_session_created:
                    on_session_created(canvas_id, session_id)

            res = self._consume_sse(
                f"{self.base_url}/v1/agent/chat/stream",
                payload,
                artifacts,
                texts,
                cancel_event=cancel_event,
                canvas_id=canvas_id,
                session_id=session_id,
            )

            # Retry once if Tensor Agent aborted with transient BadRequestError before proposing any tool
            reply_so_far = "".join(texts)
            if (
                attempt == 0
                and not artifacts
                and not res.get("pending_intent")
                and not res.get("error")
                and "BadRequestError" in reply_so_far
            ):
                time.sleep(2.0)
                continue
            break

        # Auto-approve billing confirmation if proposed (up to 3 rounds)
        rounds = 0
        while res.get("pending_intent") and not res.get("error") and rounds < 3:
            rounds += 1
            intent = res["pending_intent"]
            if intent.get("sufficient") is False:
                raise RuntimeError("Tensor 账号 Energy 余额不足，请先在 forge.art 充值后再试")
            tool_call_id = intent.get("tool_call_id") or ""
            charged = intent.get("charged_energy")
            if not tool_call_id:
                break
            confirm_body: dict[str, Any] = {"approved": True}
            if charged is not None:
                confirm_body["expectedEnergy"] = float(charged)
            res = self._consume_sse(
                f"{self.base_url}/v1/agent/tool_intent/{tool_call_id}/respond/stream",
                confirm_body,
                artifacts,
                texts,
                cancel_event=cancel_event,
                canvas_id=canvas_id,
                session_id=session_id,
            )

        if res.get("error"):
            raise RuntimeError(f"Tensor 生成失败：{res['error']}")

        urls = self._extract_artifact_urls(artifacts, expected_kind)

        # Fallback: query /agent/messages or poll /workflow/graph (Agent submits async workflow nodes)
        if not urls:
            try:
                msg_data = self._post_json(
                    "/agent/messages",
                    {"canvasId": canvas_id, "sessionId": session_id, "size": 20},
                    timeout=20,
                )
                for m in msg_data.get("messages") or []:
                    arts = m.get("artifacts") or []
                    if arts:
                        urls.extend(self._extract_artifact_urls(arts, expected_kind))
            except Exception:
                pass
        if not urls:
            poll_timeout = 1200 if expected_kind == "video" else 300
            urls.extend(
                self._poll_workflow_output(
                    canvas_id=canvas_id,
                    expected_kind=expected_kind,
                    cancel_event=cancel_event,
                    timeout_sec=poll_timeout,
                )
            )

        urls = list(dict.fromkeys(urls))
        if not urls:
            reply_preview = "".join(texts).strip()[:280]
            if "BadRequestError" in reply_preview:
                raise RuntimeError(f"Tensor 云端 Agent 响应中断（已自动重试仍失败）：{reply_preview}")
            hint = f"（Agent 回复：{reply_preview}）" if reply_preview else ""
            raise RuntimeError(f"Tensor 未返回生成产物{hint}")

        return {
            "canvas_id": canvas_id,
            "session_id": session_id,
            "pinned_model": pinned_model,
            "urls": urls,
            "reply": "".join(texts).strip(),
            "status": res.get("status") or "SUCCESS",
        }

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
            if asset.get("kind") != "image":
                continue
            if self._resolve_local_file(asset) is not None:
                img_idx += 1
                ref_items.append({**asset, "tag": f"@图片{img_idx}"})

        pinned = resolve_pinned_model(model, kind="image", has_refs=bool(ref_items))
        clean_prompt = (prompt or "").strip() or "生成一张高质量电影感画面"
        aspect_hint = f"，画面比例 {aspect}" if aspect and aspect != "自适应" else ""
        ref_hint = "，结合画布上已选中的参考图节点" if ref_items else ""
        full_msg = f"请直接生成图片（不要反问，立即调用生图工具）{ref_hint}{aspect_hint}：\n{clean_prompt}"

        gen = self._run_agent_generation(
            expected_kind="image",
            pinned_model=pinned,
            message=full_msg,
            ref_items=ref_items,
            cancel_event=cancel_event,
            on_session_created=on_session_created,
        )

        remote_url = gen["urls"][0]
        dl = requests.get(remote_url, timeout=120)
        dl.raise_for_status()

        out_dir = output_dir / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = ".png"
        low_url = remote_url.lower().split("?")[0]
        for cand_ext in (".png", ".jpg", ".jpeg", ".webp"):
            if low_url.endswith(cand_ext):
                ext = cand_ext
                break
        filename = f"tensor-{pinned.lower()}-{uuid4().hex[:8]}{ext}"
        target = out_dir / filename
        target.write_bytes(dl.content)

        dims = aspect
        try:
            with Image.open(target) as im:
                dims = f"{im.width}x{im.height}"
        except Exception:
            pass

        return {
            "model": pinned,
            "filename": filename,
            "path": target,
            "size": dims,
            "remote_url": remote_url,
            "canvas_id": gen["canvas_id"],
            "session_id": gen["session_id"],
        }

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
            for s in slots:
                if self._resolve_local_file(s) is not None:
                    ref_items.append(s)

        pinned = resolve_pinned_model(model, kind="video", has_refs=bool(ref_items))
        model_hint_name = TENSOR_PROMPT_MODEL_HINTS.get(str(model or "").strip())
        if not model_hint_name and "2.5" in str(model or "").lower():
            model_hint_name = "Seedance 2.5 Flash" if "flash" in str(model or "").lower() else "Seedance 2.5"
            pinned = "DEFAULT"
        clean_prompt = (prompt or "").strip() or "生成一段电影感运镜视频"
        hints: list[str] = []
        if model_hint_name:
            hints.append(f"务必使用 {model_hint_name} 模型")
        if aspect and aspect != "自适应":
            hints.append(f"比例 {aspect}")
        if duration:
            hints.append(f"时长约 {duration} 秒")
        if resolution:
            hints.append(f"分辨率 {resolution}")
        if not generate_audio:
            hints.append("静音/不生成音频")
        hint_str = "，".join(hints)
        ref_note = "，使用画布上已选中的所有参考素材节点" if ref_items else ""
        full_msg = f"请直接生成视频（不要反问，立即调用视频生成工具{ref_note}，{hint_str}）：\n{clean_prompt}"

        gen = self._run_agent_generation(
            expected_kind="video",
            pinned_model=pinned,
            message=full_msg,
            ref_items=ref_items,
            cancel_event=cancel_event,
            on_session_created=on_session_created,
        )

        remote_url = gen["urls"][0]
        dl = requests.get(remote_url, timeout=180)
        dl.raise_for_status()

        out_dir = output_dir / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"tensor-{pinned.lower()}-{uuid4().hex[:8]}.mp4"
        target = out_dir / filename
        target.write_bytes(dl.content)

        return {
            "task_id": gen["session_id"],
            "canvas_id": gen["canvas_id"],
            "model": pinned,
            "filename": filename,
            "path": target,
            "remote_url": remote_url,
        }
