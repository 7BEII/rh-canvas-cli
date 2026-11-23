from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

VOLC_ARK_OPENAPI_HOST = "ark.cn-beijing.volcengineapi.com"
VOLC_ARK_SERVICE = "ark"
VOLC_ARK_REGION = "cn-beijing"
VOLC_ARK_VERSION = "2024-01-01"
DEFAULT_GROUP_NAME = "rh-canvas-auto"


def resolve_volc_ak_sk(config: dict[str, Any]) -> tuple[str, str]:
    ak = str(config.get("volc_ak") or os.environ.get("VOLC_ACCESSKEY") or os.environ.get("VOLC_AK") or "").strip()
    sk = str(config.get("volc_sk") or os.environ.get("VOLC_SECRETKEY") or os.environ.get("VOLC_SK") or "").strip()
    return ak, sk


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sign_volc_v4(
    *,
    ak: str,
    sk: str,
    host: str,
    service: str,
    region: str,
    method: str,
    path: str,
    query: dict[str, str],
    body_bytes: bytes,
) -> dict[str, str]:
    """Compute standard Volcengine OpenAPI V4 HMAC-SHA256 headers."""
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    content_type = "application/json; charset=utf-8"

    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}"
        for k, v in sorted(query.items())
    )
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = "\n".join(
        [method.upper(), path, canonical_query, canonical_headers, signed_headers, payload_hash]
    )

    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    k_date = _hmac_sha256(sk.encode("utf-8"), short_date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Content-Type": content_type,
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }


class VolcAssetManager:
    """Automatic Volcengine Ark Private Asset Library (CreateAssetGroup / CreateAsset / GetAsset)."""

    def __init__(
        self,
        ak: str,
        sk: str,
        cache_path: Path | None = None,
        region: str = VOLC_ARK_REGION,
        project_name: str = "default",
    ) -> None:
        self.ak = ak.strip()
        self.sk = sk.strip()
        self.region = region
        self.project_name = project_name
        self.cache_path = cache_path or (Path(__file__).resolve().parent.parent / "data" / "ark_asset_cache.json")
        self._lock = threading.RLock()

    def _load_cache(self) -> dict[str, Any]:
        with self._lock:
            if self.cache_path.exists():
                try:
                    data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
            return {"group_id": "", "assets": {}}

    def _save_cache(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_cached_asset_id(self, source_url: str) -> str | None:
        cache = self._load_cache()
        val = (cache.get("assets") or {}).get(source_url)
        return str(val).strip() if val else None

    def _call_action(self, action: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        if not self.ak or not self.sk:
            raise ValueError("未配置火山引擎 IAM AK / SK，无法自动录入私域资产库")
        query = {"Action": action, "Version": VOLC_ARK_VERSION}
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = _sign_volc_v4(
            ak=self.ak,
            sk=self.sk,
            host=VOLC_ARK_OPENAPI_HOST,
            service=VOLC_ARK_SERVICE,
            region=self.region,
            method="POST",
            path="/",
            query=query,
            body_bytes=body_bytes,
        )
        url = f"https://{VOLC_ARK_OPENAPI_HOST}/?Action={action}&Version={VOLC_ARK_VERSION}"
        resp = requests.post(url, headers=headers, data=body_bytes, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            raise ValueError(f"火山资产库 {action} 响应异常 (HTTP {resp.status_code}): {resp.text[:240]}")

        meta = data.get("ResponseMetadata") or {}
        err = meta.get("Error")
        if resp.status_code >= 400 or err:
            code = err.get("Code", "") if isinstance(err, dict) else ""
            msg = err.get("Message", str(err)) if isinstance(err, dict) else str(data)
            raise ValueError(f"火山资产库 {action} 失败 [{code}]: {msg}")
        return data.get("Result") or {}

    def ensure_group(self) -> str:
        cache = self._load_cache()
        if cache.get("group_id"):
            return str(cache["group_id"])

        # Try listing existing groups first to reuse DEFAULT_GROUP_NAME
        try:
            listed = self._call_action(
                "ListAssetGroups",
                {"Filter": {"GroupType": "AIGC"}, "ProjectName": self.project_name},
            )
            items = listed.get("Items") or listed.get("AssetGroups") or []
            for item in items:
                if isinstance(item, dict) and item.get("Name") == DEFAULT_GROUP_NAME and item.get("Id"):
                    gid = str(item["Id"])
                    cache["group_id"] = gid
                    self._save_cache(cache)
                    return gid
        except Exception:
            pass

        created = self._call_action(
            "CreateAssetGroup",
            {
                "Name": DEFAULT_GROUP_NAME,
                "GroupType": "AIGC",
                "Description": "RH Canvas Studio 自动私域人像资产组",
                "ProjectName": self.project_name,
            },
        )
        gid = str(created.get("Id") or "").strip()
        if not gid:
            raise ValueError(f"创建火山资产组未返回 Id：{created}")
        cache["group_id"] = gid
        self._save_cache(cache)
        return gid

    def enroll_asset(
        self,
        *,
        public_url: str,
        name: str = "reference-asset",
        asset_type: str = "Image",
        poll_interval: float = 2.0,
        timeout_seconds: float = 120.0,
        cancel_event: threading.Event | None = None,
    ) -> str:
        cached = self.get_cached_asset_id(public_url)
        if cached:
            return cached

        group_id = self.ensure_group()
        safe_name = (name or "asset")[:60]
        created = self._call_action(
            "CreateAsset",
            {
                "GroupId": group_id,
                "Name": safe_name,
                "URL": public_url,
                "AssetType": asset_type,
                "ProjectName": self.project_name,
            },
        )
        asset_id = str(created.get("Id") or "").strip()
        if not asset_id:
            raise ValueError(f"CreateAsset 未返回资产 Id：{created}")

        deadline = time.monotonic() + timeout_seconds
        last_status = "Processing"
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("已中止火山资产录入")
            time.sleep(poll_interval)
            info = self._call_action("GetAsset", {"Id": asset_id, "ProjectName": self.project_name})
            last_status = str(info.get("Status") or "")
            if last_status == "Active":
                cache = self._load_cache()
                cache.setdefault("assets", {})[public_url] = asset_id
                self._save_cache(cache)
                return asset_id
            if last_status in {"Failed", "Deleted"}:
                err_obj = info.get("Error") or {}
                err_msg = (
                    f"[{err_obj.get('Code', '')}] {err_obj.get('Message', '')}"
                    if isinstance(err_obj, dict)
                    else str(err_obj)
                )
                raise ValueError(f"火山资产审核未通过 ({asset_id}): {err_msg or last_status}")

        raise TimeoutError(f"火山资产录入超时 ({asset_id}，当前状态: {last_status})")
