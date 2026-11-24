from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests
from PIL import Image, ImageDraw, ImageFont

from .ark_client import ArkClient, resolve_ark_base_url, resolve_ark_key
from .runninghub_client import RunningHubVideoClient, resolve_rh_base_url, resolve_rh_key
from .settings_store import SettingsStore
from .tensor_client import TensorCanvasClient, is_tensor_model, resolve_tensor_base_url, resolve_tensor_key
from .volc_assets import VolcAssetManager, resolve_volc_ak_sk


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aspect_to_size(aspect: str) -> tuple[int, int]:
    mapping = {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "3:2": (1200, 800),
        "2:3": (800, 1200),
        "4:3": (1024, 768),
    }
    return mapping.get(aspect or "16:9", (1280, 720))


class WorkflowRunner:
    """Executes individual canvas nodes using real configured APIs or local preview fallbacks."""

    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._tasks_path = Path(__file__).resolve().parent.parent / "data" / "tasks.json"
        self._tasks: list[dict[str, Any]] = self._load_tasks()
        self._active_runs: dict[str, dict[str, Any]] = {}

    def _load_tasks(self) -> list[dict[str, Any]]:
        if self._tasks_path.exists():
            try:
                data = json.loads(self._tasks_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-200:]
            except Exception:
                pass
        return []

    def _save_tasks(self) -> None:
        try:
            self._tasks_path.parent.mkdir(parents=True, exist_ok=True)
            self._tasks_path.write_text(json.dumps(self._tasks[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def cancel_node(self, node_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._active_runs.get(node_id)
        if not run:
            return {"success": True, "cancelled": False, "message": "当前节点没有正在运行的任务"}
        ev: threading.Event | None = run.get("cancel_event")
        if ev:
            ev.set()
        remote_id = str(run.get("ark_task_id") or "").strip()
        tensor_canvas_id = str(run.get("tensor_canvas_id") or "").strip()
        tensor_session_id = str(run.get("tensor_session_id") or "").strip()
        remote_cancelled = False
        if remote_id:
            cfg = self.settings.load()
            ark_key = resolve_ark_key(cfg)
            if ark_key:
                remote_cancelled = ArkClient(ark_key, resolve_ark_base_url(cfg)).cancel_task(remote_id)
        elif tensor_canvas_id and tensor_session_id:
            cfg = self.settings.load()
            tensor_key = resolve_tensor_key(cfg)
            if tensor_key:
                remote_cancelled = TensorCanvasClient(tensor_key, resolve_tensor_base_url(cfg)).cancel_chat(
                    tensor_canvas_id, tensor_session_id
                )
        return {
            "success": True,
            "cancelled": True,
            "ark_task_id": remote_id or tensor_session_id or None,
            "remote_cancelled": remote_cancelled,
            "message": f"已发送中断指令" + (f"（已同步取消云端任务 {remote_id or tensor_session_id}）" if remote_cancelled else ""),
        }

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._tasks[-limit:]))

    def _record_task(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._tasks.append(entry)
            if len(self._tasks) > 200:
                self._tasks = self._tasks[-200:]
            self._save_tasks()
        return entry

    def _output_url(self, file_path: Path) -> str:
        root = self.settings.output_dir()
        rel = file_path.resolve().relative_to(root).as_posix()
        return f"/api/outputs/{quote(rel)}"

    def _collect_upstream(self, upstream: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
        texts: list[str] = []
        assets: list[dict[str, Any]] = []
        for item in upstream:
            ref = item.get("ref") or {}
            card = item.get("card") or {}
            results = card.get("results") or []
            active_idx = card.get("activeResultIndex")
            selected = None
            if isinstance(results, list) and results:
                if isinstance(active_idx, int) and 0 <= active_idx < len(results):
                    selected = results[active_idx]
                else:
                    selected = results[-1]

            if ref.get("kind") == "text":
                txt = (selected or {}).get("text") or card.get("prompt") or card.get("text") or ""
                if str(txt).strip():
                    texts.append(str(txt).strip())
            else:
                if selected:
                    assets.append(selected)
        return texts, assets

    def _call_llm(self, config: dict[str, Any], system_prompt: str, user_prompt: str) -> str:
        base_url = str(config.get("llm_base_url") or "https://api.deepseek.com/v1").rstrip("/")
        api_key = str(config.get("llm_api_key") or "").strip()
        model = str(config.get("llm_model") or "deepseek-chat").strip()
        if not api_key:
            raise ValueError("未配置 LLM API Key")
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def _render_preview_image(self, title: str, prompt: str, aspect: str, model: str) -> dict[str, Any]:
        w, h = _aspect_to_size(aspect)
        img = Image.new("RGB", (w, h), (15, 15, 17))
        draw = ImageDraw.Draw(img)

        # Subtle RHTV obsidian gradient
        for y in range(0, h, 4):
            ratio = y / max(h, 1)
            r = int(15 + 8 * math.sin(ratio * math.pi))
            g = int(16 + 10 * (1 - abs(ratio - 0.35)))
            b = int(18 + 8 * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b), width=4)

        draw.rounded_rectangle(
            [(32, 32), (w - 32, h - 32)],
            radius=24,
            outline=(204, 255, 0),
            width=3,
            fill=(23, 23, 25),
        )

        font_title = ImageFont.load_default()
        draw.text((64, 68), f"RHTV CANVAS PREVIEW  |  {model}", fill=(204, 255, 0), font=font_title)
        draw.text((64, 102), f"NODE: {title}   ({w}x{h})", fill=(245, 245, 247), font=font_title)

        wrapped = textwrap.wrap(prompt or "No prompt provided", width=52)[:12]
        cy = 152
        for line in wrapped:
            draw.text((64, cy), line, fill=(186, 192, 202), font=font_title)
            cy += 22

        draw.text(
            (64, h - 72),
            "Configure RunningHub API Key in Settings for cloud Seedance video generation.",
            fill=(136, 140, 148),
            font=font_title,
        )

        out_dir = self.settings.output_dir() / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"canvas-{uuid4().hex[:10]}.png"
        target = out_dir / filename
        img.save(target, format="PNG")

        return {
            "kind": "image",
            "label": f"{model} · 预览",
            "name": filename,
            "dims": f"{w}x{h}",
            "path": str(target),
            "url": self._output_url(target),
        }

    def _collect_omni_slots(
        self,
        card: dict[str, Any],
        upstream: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build ordered @图片N / @视频N / @音频N slots matching frontend OmniVideoPanel."""
        buckets: dict[str, list[dict[str, Any]]] = {"image": [], "video": [], "audio": []}
        prefix_map = {"image": "图片", "video": "视频", "audio": "音频"}

        def push(kind: str, info: dict[str, Any]) -> None:
            if kind not in buckets:
                return
            idx = len(buckets[kind]) + 1
            tag = f"@{prefix_map[kind]}{idx}"
            buckets[kind].append({"tag": tag, "kind": kind, "index": idx, **info})

        for item in upstream:
            ref = item.get("ref") or {}
            kind = str(ref.get("kind") or "")
            if kind not in buckets:
                continue
            up_card = item.get("card") or {}
            results = up_card.get("results") or []
            active_idx = up_card.get("activeResultIndex")
            selected = None
            if isinstance(results, list) and results:
                if isinstance(active_idx, int) and 0 <= active_idx < len(results):
                    selected = results[active_idx]
                else:
                    selected = results[-1]
            push(
                kind,
                {
                    "source": "upstream",
                    "name": str(up_card.get("name") or ref.get("label") or "上游素材"),
                    "url": (selected or {}).get("url") or (selected or {}).get("dataUrl") or "",
                    "path": (selected or {}).get("path") or "",
                    "arkAssetId": str(up_card.get("arkAssetId") or "").strip(),
                    "ready": bool(selected),
                },
            )

        for local in card.get("localMedia") or []:
            if not isinstance(local, dict):
                continue
            kind = str(local.get("kind") or "")
            if kind not in buckets:
                continue
            push(
                kind,
                {
                    "source": "local",
                    "name": str(local.get("name") or "本地素材"),
                    "url": str(local.get("url") or ""),
                    "path": str(local.get("path") or ""),
                    "ready": bool(local.get("url") or local.get("path")),
                },
            )

        return [*buckets["image"], *buckets["video"], *buckets["audio"]]

    def _render_omni_video_mp4(
        self,
        title: str,
        prompt: str,
        aspect: str,
        model: str,
        mode: str,
        resolution: str,
        duration: int,
        slots: list[dict[str, Any]],
        matched_tags: list[str],
        generate_audio: bool = True,
    ) -> dict[str, Any] | None:
        """Synthesize a real playable H.264 MP4 from bound @image slides + @audio via imageio_ffmpeg."""
        try:
            import imageio_ffmpeg
            import numpy as np
        except Exception:
            return None

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if not ffmpeg_exe:
            return None

        # Choose snappy output dimensions matching aspect ratio (even numbers for H.264)
        aspect_map = {
            "9:16": (432, 768),
            "16:9": (768, 432),
            "1:1": (576, 576),
            "4:3": (640, 480),
            "3:4": (480, 640),
            "21:9": (840, 360),
        }
        w, h = aspect_map.get(aspect, (432, 768))

        # Collect real image files in @mention order first, then remaining image slots
        slot_by_tag = {s["tag"]: s for s in slots}
        ordered_img_slots: list[dict[str, Any]] = []
        seen_tags: set[str] = set()
        for t in matched_tags:
            s = slot_by_tag.get(t)
            if s and s.get("kind") == "image" and s.get("path"):
                ordered_img_slots.append(s)
                seen_tags.add(t)
        for s in slots:
            if s.get("kind") == "image" and s.get("path") and s["tag"] not in seen_tags:
                ordered_img_slots.append(s)
                seen_tags.add(s["tag"])

        slides: list[tuple[str, Image.Image]] = []
        for s in ordered_img_slots[:8]:
            raw_path = str(s.get("path") or "").strip()
            p = Path(raw_path) if raw_path else None
            if (p is None or not p.exists()) and str(s.get("url") or "").startswith("/api/assets/input/"):
                from urllib.parse import unquote
                fname = unquote(str(s["url"]).removeprefix("/api/assets/input/"))
                cand = Path(__file__).resolve().parent.parent / "data" / "assets" / fname
                if cand.exists() and cand.is_file():
                    p = cand
            if p is not None and p.exists() and p.is_file():
                try:
                    with Image.open(p) as im:
                        rgb = im.convert("RGB")
                        # Cover-crop to target aspect
                        src_ratio = rgb.width / max(rgb.height, 1)
                        dst_ratio = w / max(h, 1)
                        if src_ratio > dst_ratio:
                            new_w = int(rgb.height * dst_ratio)
                            left = (rgb.width - new_w) // 2
                            rgb = rgb.crop((left, 0, left + new_w, rgb.height))
                        else:
                            new_h = int(rgb.width / dst_ratio)
                            top = (rgb.height - new_h) // 2
                            rgb = rgb.crop((0, top, rgb.width, top + new_h))
                        slides.append((f"{s['tag']} · {s['name']}", rgb.resize((int(w * 1.12), int(h * 1.12)), Image.Resampling.LANCZOS)))
                except Exception:
                    pass

        if not slides:
            # Synthesize a sleek obsidian title slide if no local image files exist
            base = Image.new("RGB", (int(w * 1.12), int(h * 1.12)), (18, 20, 24))
            d = ImageDraw.Draw(base)
            d.rounded_rectangle([(24, 24), (base.width - 24, base.height - 24)], radius=20, outline=(204, 255, 0), width=3)
            d.text((48, 60), f"{model} | {mode}", fill=(204, 255, 0))
            d.text((48, 96), title[:40], fill=(245, 245, 247))
            slides.append((title, base))

        # Find first real audio file if generate_audio is enabled
        audio_path: Path | None = None
        if generate_audio:
            for s in slots:
                if s.get("kind") == "audio":
                    raw_path = str(s.get("path") or "").strip()
                    cand = Path(raw_path) if raw_path else None
                    if (cand is None or not cand.exists()) and str(s.get("url") or "").startswith("/api/assets/input/"):
                        from urllib.parse import unquote
                        fname = unquote(str(s["url"]).removeprefix("/api/assets/input/"))
                        alt = Path(__file__).resolve().parent.parent / "data" / "assets" / fname
                        if alt.exists() and alt.is_file():
                            cand = alt
                    if cand is not None and cand.exists() and cand.is_file():
                        audio_path = cand
                        break

        fps = 24
        # Keep local preview snappy (max 6 seconds) while displaying requested duration in metadata
        render_secs = max(3, min(6, int(duration or 5)))
        total_frames = fps * render_secs
        frames_per_slide = max(1, total_frames // len(slides))
        fade_frames = min(10, max(4, frames_per_slide // 5))

        out_dir = self.settings.output_dir() / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"omni-video-{uuid4().hex[:10]}.mp4"
        target = out_dir / filename

        cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{w}x{h}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
        ]
        if audio_path is not None:
            cmd.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "128k", "-shortest"])
        cmd.extend([
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-movflags", "+faststart",
            str(target),
        ])

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert proc.stdin is not None

        def render_slide_frame(im_large: Image.Image, progress: float, slide_idx: int) -> np.ndarray:
            # Smooth Ken Burns pan/zoom inside the 1.12x oversized image
            lw, lh = im_large.size
            max_dx = max(1, lw - w)
            max_dy = max(1, lh - h)
            if slide_idx % 2 == 0:
                x0 = int(max_dx * progress)
                y0 = int(max_dy * 0.5 * (1 - progress))
            else:
                x0 = int(max_dx * (1 - progress))
                y0 = int(max_dy * 0.5 * progress)
            crop = im_large.crop((x0, y0, x0 + w, y0 + h))
            return np.asarray(crop, dtype=np.uint8)

        try:
            for f_idx in range(total_frames):
                s_idx = min(len(slides) - 1, f_idx // frames_per_slide)
                local_f = f_idx - s_idx * frames_per_slide
                prog = min(1.0, local_f / max(frames_per_slide - 1, 1))
                frame = render_slide_frame(slides[s_idx][1], prog, s_idx)

                # Crossfade into next slide near the end of current slide
                if s_idx + 1 < len(slides) and local_f >= frames_per_slide - fade_frames:
                    alpha = (local_f - (frames_per_slide - fade_frames)) / max(fade_frames, 1)
                    next_frame = render_slide_frame(slides[s_idx + 1][1], 0.0, s_idx + 1)
                    frame = (frame.astype(np.float32) * (1.0 - alpha) + next_frame.astype(np.float32) * alpha).astype(np.uint8)

                proc.stdin.write(frame.tobytes())
            proc.stdin.close()
            ret = proc.wait(timeout=45)
        except Exception:
            proc.kill()
            target.unlink(missing_ok=True)
            return None

        if ret != 0 or not target.exists():
            target.unlink(missing_ok=True)
            return None

        return {
            "kind": "video",
            "label": f"{model} · {resolution} {duration}s" + (" · 含配乐" if audio_path else ""),
            "name": filename,
            "dims": f"{w}x{h}",
            "path": str(target),
            "url": self._output_url(target),
        }

    def _render_omni_preview(
        self,
        title: str,
        prompt: str,
        aspect: str,
        model: str,
        mode: str,
        resolution: str,
        duration: int,
        slots: list[dict[str, Any]],
        matched_tags: list[str],
    ) -> dict[str, Any]:
        w, h = _aspect_to_size(aspect)
        img = Image.new("RGB", (w, h), (16, 19, 24))
        draw = ImageDraw.Draw(img)

        for y in range(0, h, 4):
            ratio = y / max(h, 1)
            r = int(20 + 18 * math.sin(ratio * math.pi))
            g = int(18 + 16 * (1 - abs(ratio - 0.4)))
            b = int(32 + 28 * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b), width=4)

        draw.rounded_rectangle(
            [(32, 32), (w - 32, h - 32)],
            radius=24,
            outline=(189, 140, 255),
            width=3,
            fill=(22, 25, 32),
        )

        font = ImageFont.load_default()
        draw.text(
            (64, 62),
            f"OMNI VIDEO PREVIEW  |  {model}  |  mode={mode}  |  {resolution} / {duration}s / {aspect}",
            fill=(244, 224, 77),
            font=font,
        )
        draw.text(
            (64, 92),
            f"NODE: {title}   |   Refs: {len(slots)} attached, {len(matched_tags)} @mentioned",
            fill=(240, 244, 248),
            font=font,
        )

        # Composite up to 4 real local reference image thumbnails if present
        thumb_x = 64
        thumb_y = 128
        thumb_size = 120
        shown_thumbs = 0
        for s in slots:
            if s.get("kind") == "image" and s.get("path") and shown_thumbs < 4:
                p = Path(str(s["path"]))
                if p.exists() and p.is_file():
                    try:
                        with Image.open(p) as ref_im:
                            ref_rgb = ref_im.convert("RGB")
                            ref_rgb.thumbnail((thumb_size, thumb_size))
                            img.paste(ref_rgb, (thumb_x, thumb_y))
                            draw.rectangle(
                                [(thumb_x, thumb_y), (thumb_x + ref_rgb.width, thumb_y + ref_rgb.height)],
                                outline=(244, 224, 77),
                                width=2,
                            )
                            thumb_x += thumb_size + 16
                            shown_thumbs += 1
                    except Exception:
                        pass

        cy = thumb_y + (thumb_size + 24 if shown_thumbs else 10)
        tag_line = "Tags: " + (", ".join(matched_tags) if matched_tags else "(no @tags)")
        draw.text((64, cy), tag_line[:96], fill=(189, 140, 255), font=font)
        cy += 26

        ascii_preview = prompt.encode("ascii", "replace").decode("ascii")
        for line in textwrap.wrap(ascii_preview or "Omni-reference video storyboard frame", width=72)[:8]:
            draw.text((64, cy), line, fill=(196, 206, 218), font=font)
            cy += 20

        draw.text(
            (64, h - 68),
            "Ready for RunningHub / Seedance cloud API. Check Result 2 for full @reference manifest.",
            fill=(136, 148, 165),
            font=font,
        )

        out_dir = self.settings.output_dir() / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"omni-video-{uuid4().hex[:10]}.png"
        target = out_dir / filename
        img.save(target, format="PNG")

        return {
            "kind": "image",
            "label": f"{model} · 全能参考预览 ({resolution}/{duration}s)",
            "name": filename,
            "dims": f"{w}x{h}",
            "path": str(target),
            "url": self._output_url(target),
        }

    def run_node(self, card: dict[str, Any], upstream: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        node_id = str(card.get("id") or "node")
        node_name = str(card.get("name") or card.get("type") or "节点")
        node_type = str(card.get("type") or "prompt")
        config = self.settings.load()
        allow_mock = bool(config.get("allow_mock_fallback", True))

        texts, assets = self._collect_upstream(upstream)
        own_prompt = str(card.get("prompt") or card.get("text") or "").strip()
        combined_prompt = "\n".join([t for t in [*texts, own_prompt] if t]).strip()

        provider = "local"
        results: list[dict[str, Any]] = []
        message = "执行完成"

        try:
            if node_type == "prompt":
                results = [{"kind": "text", "label": "文本输出", "text": combined_prompt or "(空文本)"}]
                message = "文本已就绪"

            elif node_type == "source":
                existing = card.get("results") or []
                if not existing:
                    raise ValueError("请先在素材节点上传图片、视频或音频")
                results = existing
                message = "素材已就绪"

            elif node_type == "storyboard":
                shot_count = max(2, min(8, int(card.get("shotCount") or 4)))
                if config.get("llm_api_key") and card.get("model") != "local-template":
                    provider = f"llm:{config.get('llm_model') or 'deepseek-chat'}"
                    sys_prompt = (
                        f"你是专业分镜导演。请根据用户需求输出 JSON，格式为 "
                        f'{{"shots":[{{"id":1,"title":"镜头标题","prompt":"画面提示词","motion":"动作与运镜"}}]}}，'
                        f"共 {shot_count} 个镜头，只输出合法 JSON。"
                    )
                    content = self._call_llm(config, sys_prompt, combined_prompt or "创作一个品牌 IP 宣传片")
                    results = [{"kind": "text", "label": f"AI 分镜 ({shot_count} 镜头)", "text": content}]
                    message = f"已通过 {provider} 生成 {shot_count} 镜头分镜"
                else:
                    provider = "local-storyboard"
                    theme = combined_prompt or "品牌 IP 宣传片"
                    shots = [
                        {
                            "id": idx + 1,
                            "title": f"镜头 {idx + 1}",
                            "prompt": f"【{theme[:40]}】第 {idx + 1}/{shot_count} 镜：电影级光影构图，主体清晰，细节精致，无字幕水印。",
                            "motion": "缓慢推镜，主体自然呼吸与眼神交流" if idx % 2 == 0 else "平滑横移镜头，光影动态过渡",
                        }
                        for idx in range(shot_count)
                    ]
                    results = [
                        {
                            "kind": "text",
                            "label": f"结构化分镜 ({shot_count} 镜头)",
                            "text": json.dumps({"shots": shots}, ensure_ascii=False, indent=2),
                        }
                    ]
                    message = f"已生成 {shot_count} 镜头结构化分镜（本地模式）"

            elif node_type == "shotExtract":
                raw = texts[0] if texts else own_prompt
                extracted = raw
                try:
                    parsed = json.loads(raw)
                    shots = parsed.get("shots") if isinstance(parsed, dict) else None
                    if isinstance(shots, list) and shots:
                        idx = max(0, int((card.get("params") or {}).get("shotIndex") or 0))
                        shot = shots[min(idx, len(shots) - 1)]
                        extracted = f"{shot.get('prompt', '')}\n运镜：{shot.get('motion', '')}".strip()
                except Exception:
                    pass
                results = [{"kind": "text", "label": "单镜头提示词", "text": extracted or "(未解析到分镜)"}]
                message = "已提取单镜头提示词"

            elif node_type == "image":
                model = str(card.get("model") or "seedream-5-pro")
                aspect = str(card.get("aspect") or "16:9")
                ark_key = resolve_ark_key(config)
                tensor_key = resolve_tensor_key(config)
                preferred = str(config.get("video_provider") or "tensor").strip().lower()
                tensor_only_model = is_tensor_model(model)

                if tensor_only_model and not tensor_key:
                    raise ValueError(f"模型 {model} 需要通过 Tensor (Forge) 渠道调用，请先在右上角「设置」中填写 Tensor Access Key")

                use_tensor_img = (tensor_only_model and bool(tensor_key)) or (
                    model != "local-preview" and preferred == "tensor" and bool(tensor_key)
                ) or (model != "local-preview" and not ark_key and bool(tensor_key))

                if use_tensor_img:
                    tc = TensorCanvasClient(api_key=tensor_key, base_url=resolve_tensor_base_url(config))
                    cancel_ev = threading.Event()
                    run_state: dict[str, Any] = {"cancel_event": cancel_ev, "tensor_canvas_id": "", "tensor_session_id": ""}
                    with self._lock:
                        self._active_runs[node_id] = run_state

                    def _on_tensor_session(cid: str, sid: str) -> None:
                        with self._lock:
                            run_state["tensor_canvas_id"] = cid
                            run_state["tensor_session_id"] = sid

                    try:
                        out = tc.generate_image(
                            model=model,
                            prompt=combined_prompt,
                            aspect=aspect,
                            ref_assets=assets,
                            output_dir=self.settings.output_dir(),
                            cancel_event=cancel_ev,
                            on_session_created=_on_tensor_session,
                        )
                    finally:
                        with self._lock:
                            if self._active_runs.get(node_id) is run_state:
                                self._active_runs.pop(node_id, None)

                    provider = f"tensor:{out['model']}"
                    results = [
                        {
                            "kind": "image",
                            "label": f"{out['model']} · Tensor",
                            "name": out["filename"],
                            "dims": out["size"],
                            "path": str(out["path"]),
                            "url": self._output_url(out["path"]),
                        }
                    ]
                    message = f"Tensor 生图成功（{out['model']}）"
                elif ark_key and model != "local-preview":
                    target_model = str(config.get("ark_image_model") or "").strip() or model
                    ark = ArkClient(api_key=ark_key, base_url=resolve_ark_base_url(config))
                    out = ark.generate_image(
                        model=target_model,
                        prompt=combined_prompt,
                        aspect=aspect,
                        output_dir=self.settings.output_dir(),
                    )
                    provider = f"ark:{out['model']}"
                    results = [
                        {
                            "kind": "image",
                            "label": f"{out['model']} · 火山方舟",
                            "name": out["filename"],
                            "dims": out["size"],
                            "path": str(out["path"]),
                            "url": self._output_url(out["path"]),
                        }
                    ]
                    message = f"火山方舟生图成功（{out['model']}）"
                else:
                    if not allow_mock:
                        raise ValueError("未配置生图 API Key，请在右上角「设置」中填写 Tensor 或火山方舟 API Key")
                    provider = f"preview:{model}"
                    results = [self._render_preview_image(node_name, combined_prompt, aspect, model)]
                    message = f"已生成本地预览图（{model}，填写 Tensor 或火山方舟 API Key 后将调用云端模型）"

            elif node_type == "video":
                model = str(card.get("model") or "seedance-2.0-mini")
                video_mode = str(card.get("videoMode") or "omni")
                resolution = str(card.get("resolution") or "720p")
                duration = int(card.get("duration") or 15)
                aspect = str(card.get("aspect") or "9:16")
                style_preset = str(card.get("stylePreset") or "")
                generate_audio = card.get("generateAudio", True) is not False

                slots = self._collect_omni_slots(card, upstream)
                slot_map = {s["tag"]: s for s in slots}
                mentioned = list(dict.fromkeys(re.findall(r"@(?:图片|视频|音频)\d+", combined_prompt)))
                matched_tags = [t for t in mentioned if t in slot_map]
                missing_tags = [t for t in mentioned if t not in slot_map]

                ark_key = resolve_ark_key(config)
                rh_key = resolve_rh_key(config)
                tensor_key = resolve_tensor_key(config)
                preferred = str(config.get("video_provider") or "tensor").strip().lower()
                tensor_only_model = is_tensor_model(model)
                rh_only_model = (not tensor_only_model) and any(k in model.lower() for k in ("wan-3", "wan3", "hailuo", "h3"))
                if tensor_only_model and not tensor_key:
                    raise ValueError(f"模型 {model} 需要通过 Tensor (Forge) 渠道调用，请先在右上角「设置」中填写 Tensor Access Key")
                if rh_only_model and not rh_key:
                    raise ValueError(f"模型 {model} 需要通过 RunningHub 渠道调用，请先在右上角「设置」中填写 RunningHub API Key")

                use_tensor = (tensor_only_model and bool(tensor_key)) or (
                    not rh_only_model and preferred == "tensor" and bool(tensor_key)
                ) or (not rh_only_model and not rh_key and not ark_key and bool(tensor_key))
                use_rh = (not use_tensor) and (
                    (rh_only_model and bool(rh_key)) or (preferred == "runninghub" and bool(rh_key)) or (not ark_key and bool(rh_key))
                )
                use_ark = (not use_tensor) and (not use_rh) and bool(ark_key)

                if use_tensor:
                    full_prompt = combined_prompt
                    if style_preset and style_preset != "无" and style_preset not in full_prompt:
                        full_prompt = f"{full_prompt}\n画面风格：{style_preset}".strip()

                    tc = TensorCanvasClient(api_key=tensor_key, base_url=resolve_tensor_base_url(config))
                    cancel_ev = threading.Event()
                    run_state: dict[str, Any] = {"cancel_event": cancel_ev, "tensor_canvas_id": "", "tensor_session_id": ""}
                    with self._lock:
                        self._active_runs[node_id] = run_state

                    def _on_tensor_video_session(cid: str, sid: str) -> None:
                        with self._lock:
                            run_state["tensor_canvas_id"] = cid
                            run_state["tensor_session_id"] = sid

                    try:
                        out = tc.generate_video(
                            model=model,
                            prompt=full_prompt,
                            mode=video_mode,
                            resolution=resolution,
                            duration=duration,
                            aspect=aspect,
                            generate_audio=generate_audio,
                            slots=slots,
                            output_dir=self.settings.output_dir(),
                            cancel_event=cancel_ev,
                            on_session_created=_on_tensor_video_session,
                        )
                    finally:
                        with self._lock:
                            if self._active_runs.get(node_id) is run_state:
                                self._active_runs.pop(node_id, None)

                    provider = f"tensor:{out['model']}"
                    video_res = {
                        "kind": "video",
                        "label": f"{out['model']} · {resolution} {duration}s · Tensor",
                        "name": out["filename"],
                        "path": str(out["path"]),
                        "url": self._output_url(out["path"]),
                    }
                    manifest_result = {
                        "kind": "text",
                        "label": f"Tensor 任务 {out['task_id']}",
                        "text": json.dumps(
                            {
                                "provider": "tensor",
                                "canvas_id": out["canvas_id"],
                                "session_id": out["task_id"],
                                "model": out["model"],
                                "mode": video_mode,
                                "resolution": resolution,
                                "duration": duration,
                                "aspect": aspect,
                                "remote_url": out["remote_url"],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                    results = [manifest_result, video_res]
                    message = f"Tensor 生成成功（{out['model']} · 会话 {out['task_id']}）"

                elif use_rh:
                    full_prompt = combined_prompt
                    if style_preset and style_preset != "无" and style_preset not in full_prompt:
                        full_prompt = f"{full_prompt}\n画面风格：{style_preset}".strip()

                    rh = RunningHubVideoClient(api_key=rh_key, base_url=resolve_rh_base_url(config))
                    cancel_ev = threading.Event()
                    run_state: dict[str, Any] = {"cancel_event": cancel_ev, "ark_task_id": ""}
                    with self._lock:
                        self._active_runs[node_id] = run_state

                    try:
                        out = rh.generate_video(
                            model=model,
                            prompt=full_prompt,
                            mode=video_mode,
                            resolution=resolution,
                            duration=duration,
                            aspect=aspect,
                            generate_audio=generate_audio,
                            slots=slots,
                            output_dir=self.settings.output_dir(),
                            cancel_event=cancel_ev,
                        )
                    finally:
                        with self._lock:
                            if self._active_runs.get(node_id) is run_state:
                                self._active_runs.pop(node_id, None)

                    provider = f"runninghub:{out['model']}"
                    video_res = {
                        "kind": "video",
                        "label": f"{out['model']} · {resolution} {duration}s",
                        "name": out["filename"],
                        "path": str(out["path"]),
                        "url": self._output_url(out["path"]),
                    }
                    manifest_result = {
                        "kind": "text",
                        "label": f"RunningHub 任务 {out['task_id']}",
                        "text": json.dumps(
                            {
                                "provider": "runninghub",
                                "task_id": out["task_id"],
                                "model": out["model"],
                                "mode": video_mode,
                                "resolution": resolution,
                                "duration": duration,
                                "aspect": aspect,
                                "realPersonMode": True,
                                "remote_url": out["remote_url"],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                    usage = out.get("usage") or {}
                    cost_val = usage.get("thirdPartyConsumeMoney") or usage.get("consumeMoney")
                    cost_note = f" · 实扣 ¥{cost_val}" if cost_val else ""
                    results = [manifest_result, video_res]
                    message = f"RunningHub 生成成功（{out['model']} · 任务 {out['task_id']}{cost_note}）"

                elif use_ark:
                    target_model = str(config.get("ark_video_model") or "").strip() or model
                    full_prompt = combined_prompt
                    if style_preset and style_preset != "无" and style_preset not in full_prompt:
                        full_prompt = f"{full_prompt}\n画面风格：{style_preset}".strip()

                    ark = ArkClient(api_key=ark_key, base_url=resolve_ark_base_url(config))
                    volc_ak, volc_sk = resolve_volc_ak_sk(config)
                    asset_mgr = VolcAssetManager(ak=volc_ak, sk=volc_sk) if (volc_ak and volc_sk) else None
                    cancel_ev = threading.Event()
                    run_state: dict[str, Any] = {"cancel_event": cancel_ev, "ark_task_id": ""}
                    with self._lock:
                        self._active_runs[node_id] = run_state

                    def _on_task_created(tid: str) -> None:
                        with self._lock:
                            run_state["ark_task_id"] = tid

                    try:
                        out = ark.generate_video(
                            model=target_model,
                            prompt=full_prompt,
                            mode=video_mode,
                            resolution=resolution,
                            duration=duration,
                            aspect=aspect,
                            generate_audio=generate_audio,
                            slots=slots,
                            output_dir=self.settings.output_dir(),
                            cancel_event=cancel_ev,
                            on_task_created=_on_task_created,
                            asset_manager=asset_mgr,
                        )
                    finally:
                        with self._lock:
                            if self._active_runs.get(node_id) is run_state:
                                self._active_runs.pop(node_id, None)
                    provider = f"ark:{out['model']}"
                    video_res = {
                        "kind": "video",
                        "label": f"{out['model']} · {resolution} {duration}s · 火山方舟",
                        "name": out["filename"],
                        "path": str(out["path"]),
                        "url": self._output_url(out["path"]),
                    }
                    manifest_result = {
                        "kind": "text",
                        "label": f"火山方舟任务 {out['task_id']}",
                        "text": json.dumps(
                            {
                                "provider": "volcengine-ark",
                                "task_id": out["task_id"],
                                "model": out["model"],
                                "mode": video_mode,
                                "resolution": resolution,
                                "duration": duration,
                                "aspect": aspect,
                                "mentionedTags": mentioned,
                                "remote_url": out["remote_url"],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                    results = [manifest_result, video_res]
                    message = f"火山方舟生成成功（{out['model']} · 任务 {out['task_id']}）"
                else:
                    if not allow_mock:
                        raise ValueError("未配置火山方舟 API Key，请在右上角「设置」中填入 ARK API Key")

                    provider = f"omni-local:{model}"
                    video_res = self._render_omni_video_mp4(
                        node_name,
                        combined_prompt,
                        aspect if aspect != "自适应" else "9:16",
                        model,
                        video_mode,
                        resolution,
                        duration,
                        slots,
                        matched_tags,
                        generate_audio=generate_audio,
                    )
                    if video_res is None:
                        video_res = self._render_omni_preview(
                            node_name,
                            combined_prompt,
                            aspect if aspect != "自适应" else "9:16",
                            model,
                            video_mode,
                            resolution,
                            duration,
                            slots,
                            matched_tags,
                        )
                    manifest = {
                        "mode": video_mode,
                        "model": model,
                        "resolution": resolution,
                        "duration": duration,
                        "aspect": aspect,
                        "style": style_preset or None,
                        "batchCount": int(card.get("batchCount") or 1),
                        "mentionedTags": mentioned,
                        "missingTags": missing_tags,
                        "boundRefs": [
                            {
                                "tag": s["tag"],
                                "kind": s["kind"],
                                "source": s["source"],
                                "name": s["name"],
                                "ready": s["ready"],
                                "url": s["url"],
                            }
                            for s in slots
                        ],
                        "prompt": combined_prompt,
                    }
                    manifest_result = {
                        "kind": "text",
                        "label": f"全能参考清单 ({len(slots)} 素材 / {len(matched_tags)} 引用)",
                        "text": json.dumps(manifest, ensure_ascii=False, indent=2),
                    }
                    results = [manifest_result, video_res]
                    message = (
                        f"本地预览成片已生成：{len(slots)} 个素材，解析 {len(matched_tags)} 个 @引用（填火山方舟 Key 后自动切换云端）"
                        + (f"（未绑定：{', '.join(missing_tags)}）" if missing_tags else "")
                    )

            elif node_type == "transition":
                model = str(card.get("model") or "h3-first-last")
                if not allow_mock and not config.get("runninghub_api_key"):
                    raise ValueError("未配置 RunningHub API Key，无法发起云端视频生成")
                provider = f"preview:{model}"
                preview = self._render_preview_image(
                    f"{node_name} (Transition Preview)",
                    combined_prompt or "First-last frame transition preview",
                    str(card.get("aspect") or "16:9"),
                    model,
                )
                preview["label"] = f"{model} · 转场预览帧"
                results = [preview]
                message = "已完成转场占位预览（配置 RunningHub API Key 后可接入云端视频）"

            elif node_type in {"audio", "ttsIndex"}:
                model = str(card.get("model") or "h3-audio")
                if not allow_mock and not config.get("runninghub_api_key"):
                    raise ValueError("未配置 RunningHub API Key，无法发起音频/TTS 生成")
                provider = f"preview:{model}"
                results = [
                    {
                        "kind": "text",
                        "label": f"{model} · 音频任务回执",
                        "text": f"[模拟音频回执] 模型: {model}\n内容: {combined_prompt or '(未提供台词/描述)'}",
                    }
                ]
                message = "已生成音频占位回执"

            elif node_type in {"videoEdit", "gallery", "save"}:
                if not assets and not texts:
                    raise ValueError("请先连接至少一个已运行的上游节点")
                if node_type == "save":
                    archive_dir = self.settings.output_dir() / "archived"
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    archived: list[dict[str, Any]] = []
                    for item in assets:
                        src_path = Path(str(item.get("path") or ""))
                        if src_path.exists() and src_path.is_file():
                            dst = archive_dir / f"{uuid4().hex[:6]}-{src_path.name}"
                            shutil.copy2(src_path, dst)
                            archived.append({**item, "path": str(dst), "url": self._output_url(dst), "label": f"已归档 · {src_path.name}"})
                        else:
                            archived.append(item)
                    results = archived or [{"kind": "text", "label": "已归档文本", "text": combined_prompt}]
                    message = f"已归档 {len(results)} 项产物到输出目录"
                else:
                    results = assets if assets else [{"kind": "text", "label": "汇总内容", "text": combined_prompt}]
                    message = f"已汇总 {len(results)} 项上游产物"
            else:
                results = [{"kind": "text", "label": "输出", "text": combined_prompt or "OK"}]

            duration_ms = int((time.perf_counter() - started) * 1000)
            task = self._record_task(
                {
                    "id": f"task-{uuid4().hex[:10]}",
                    "node_id": node_id,
                    "node_name": node_name,
                    "node_type": node_type,
                    "status": "done",
                    "message": message,
                    "provider": provider,
                    "created_at": _now_iso(),
                    "finished_at": _now_iso(),
                    "duration_ms": duration_ms,
                }
            )
            return {"success": True, "results": results, "provider": provider, "message": message, "task": task}
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._record_task(
                {
                    "id": f"task-{uuid4().hex[:10]}",
                    "node_id": node_id,
                    "node_name": node_name,
                    "node_type": node_type,
                    "status": "error",
                    "message": str(exc),
                    "provider": provider,
                    "created_at": _now_iso(),
                    "finished_at": _now_iso(),
                    "duration_ms": duration_ms,
                }
            )
            raise
