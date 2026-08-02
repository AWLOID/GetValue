from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from .parser import load_seed

CACHE_KEY = "mm2-values:latest"


def _redis_config() -> tuple[str, str] | None:
    url = os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    return url.rstrip("/"), token


def _redis(command: list[str]) -> Any:
    config = _redis_config()
    if not config:
        raise RuntimeError("persistent cache is not configured")
    url, token = config
    request = Request(
        url,
        data=json.dumps(command).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result")


def read_latest() -> tuple[dict[str, Any], str]:
    try:
        raw = _redis(["GET", CACHE_KEY])
        if raw:
            return json.loads(raw), "persistent"
    except Exception:
        pass
    return load_seed(), "seed"


def write_latest(data: dict[str, Any]) -> bool:
    try:
        _redis(["SET", CACHE_KEY, json.dumps(data, ensure_ascii=False, separators=(",", ":"))])
        return True
    except Exception:
        return False
