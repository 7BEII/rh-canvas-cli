from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class SettingsStore:
    """Standalone local settings storage for API keys and output paths."""

    def __init__(self, path: str | Path, default_output_dir: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.default_output_dir = Path(default_output_dir).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _defaults(self) -> dict[str, Any]:
        return {
            "video_provider": "runninghub",
            "ark_api_key": "",
            "ark_base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "ark_video_model": "",
            "ark_image_model": "",
            "volc_ak": "",
            "volc_sk": "",
            "runninghub_api_key": "",
            "runninghub_base_url": "https://www.runninghub.cn",
            "tensor_api_key": "",
            "tensor_base_url": "https://openapi.tensorart.me/canvas-openapi",
            "llm_api_key": "",
            "llm_base_url": "https://api.deepseek.com/v1",
            "llm_model": "deepseek-chat",
            "comfyui_url": "http://127.0.0.1:8188",
            "output_dir": str(self.default_output_dir),
            "allow_mock_fallback": True,
        }

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = self._defaults()
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data.update(raw)
                except Exception:
                    pass
            return data

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.load()
            for key in self._defaults():
                if key in updates:
                    current[key] = updates[key]
            out_dir = Path(str(current.get("output_dir") or self.default_output_dir)).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            current["output_dir"] = str(out_dir.resolve())
            self.path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            return current

    def output_dir(self) -> Path:
        cfg = self.load()
        target = Path(str(cfg.get("output_dir") or self.default_output_dir)).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target
