"""DashScope transport with redacted errors and auditable request usage."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone

import httpx

from src.core.model_env import load_model_env
from src.core.settings import resolve_path

_LOCK = threading.Lock()
DEFAULT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class DashScopeError(RuntimeError):
    pass


class DashScopeClient:
    def __init__(self, settings, section):
        load_model_env()
        self.api_key = section.api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not self.api_key or self.api_key.startswith("${"):
            raise ValueError("DASHSCOPE_API_KEY is missing from the project environment")
        self.base_url = (
            section.base_url or os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_URL
        ).rstrip("/")
        self.root = resolve_path(settings.vector_store.persist_directory).parent / "model_calls"
        self.root.mkdir(parents=True, exist_ok=True)

    def record(self, row):
        row = {"at": datetime.now(timezone.utc).isoformat(), **row}
        with _LOCK:
            with (self.root / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def post(self, path: str, payload: dict, identity: dict, trace=None) -> dict:
        for attempt in range(3):
            started = time.perf_counter()
            status, code, data = None, "transport_error", {}
            try:
                with httpx.Client(timeout=90, trust_env=False) as client:
                    response = client.post(
                        self.base_url + path,
                        json=payload,
                        headers={"Authorization": "Bearer " + self.api_key},
                    )
                status = response.status_code
                data = response.json()
                code = data.get("error", {}).get("code", "invalid_response")
            except (httpx.HTTPError, ValueError):
                pass
            ok = status == 200 and not data.get("error")
            row = {
                **identity,
                "model": payload["model"],
                "endpoint": self.base_url,
                "attempt": attempt + 1,
                "status": status,
                "success": ok,
                "cache_hit": False,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "usage": data.get("usage"),
                "request_id": data.get("id"),
                "error_code": None if ok else code,
            }
            self.record(row)
            if trace is not None:
                trace.record_stage("dashscope_" + identity["kind"], row)
            if ok:
                return data
            if status not in (None, 429, 500, 502, 503, 504) or attempt == 2:
                raise DashScopeError(
                    f"DashScope request failed: HTTP {status}, code={code}"
                ) from None
            time.sleep(2**attempt)
        raise AssertionError("unreachable")
