from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from lib.store import read_latest


def _filtered(data: dict, query: dict[str, list[str]]) -> list[dict]:
    item_type = query.get("type", ["all"])[0].lower()
    search = query.get("q", [""])[0].strip().casefold()
    items = data.get("items", [])
    if item_type in {"weapon", "pet"}:
        items = [item for item in items if item.get("type") == item_type]
    if search:
        items = [item for item in items if search in item.get("name", "").casefold()]
    return items


def _age_seconds(updated_at: str | None) -> int | None:
    if not updated_at:
        return None
    try:
        dt = datetime.fromisoformat(updated_at)
        return int((datetime.now(timezone.utc) - dt).total_seconds())
    except ValueError:
        return None


def _public_payload(data: dict, cache: str, items: list[dict]) -> dict:
    age = _age_seconds(data.get("updatedAt"))
    return {
        "success": True,
        "updatedAt": data.get("updatedAt"),
        "lastUpdate": data.get("updatedAt"),
        "source": data.get("source"),
        "cache": cache,
        "stale": cache == "seed" or age is None or age > 7200,
        "ageSeconds": age,
        "count": len(items),
        "items": items,
        "weapons": items,
    }


class handler(BaseHTTPRequestHandler):
    server_version = "MM2Values/2"

    def _send(self, status: int, body: bytes, content_type: str, *, filename: str | None = None, public: bool = True) -> None:
        etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
        if public and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=86400")
            self.end_headers()
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, If-None-Match")
        self.send_header("X-Content-Type-Options", "nosniff")
        if public:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=86400")
        else:
            self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict | list, *, public: bool = True) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            public=public,
        )

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, If-None-Match")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/api"
        query = parse_qs(parsed.query)

        # Allow /api, /api/values, /api/index.py (due to Vercel rewrite destinations)
        if route in {"/api", "/api/values", "/api/index.py", "/api/index", ""}:
            data, cache = read_latest()
            items = _filtered(data, query)
            self._json(200, _public_payload(data, cache, items))
            return

        if route in {"/api/text", "/api/download"}:
            data, cache = read_latest()
            items = _filtered(data, query)
            requested = query.get("format", ["txt" if route == "/api/text" else "json"])[0].lower()
            if requested == "csv":
                stream = io.StringIO(newline="")
                writer = csv.writer(stream)
                writer.writerow(["name", "value", "type", "category"])
                writer.writerows((item.get("name"), item.get("value"), item.get("type"), item.get("category")) for item in items)
                self._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", filename="mm2-values.csv")
            elif requested == "json":
                body = json.dumps(_public_payload(data, cache, items), ensure_ascii=False, indent=2).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", filename="mm2-values.json")
            elif requested == "txt":
                body = ("\n".join(f'{item["name"]}={item["value"]}' for item in items) + "\n").encode("utf-8")
                self._send(200, body, "text/plain; charset=utf-8", filename="mm2-values.txt" if route == "/api/download" else None)
            else:
                self._json(400, {"error": "format must be json, csv or txt"}, public=False)
            return

        if route == "/api/health":
            data, cache = read_latest()
            age = _age_seconds(data.get("updatedAt"))
            self._json(200, {
                "ok": True,
                "cache": cache,
                "stale": cache == "seed" or age is None or age > 7200,
                "updatedAt": data.get("updatedAt"),
                "ageSeconds": age,
                "count": len(data.get("items", [])),
                "weapons": sum(item.get("type") == "weapon" for item in data.get("items", [])),
                "pets": sum(item.get("type") == "pet" for item in data.get("items", [])),
            })
            return

        # Default fallback for any unmatched route under /api -> return values JSON
        data, cache = read_latest()
        items = _filtered(data, query)
        self._json(200, _public_payload(data, cache, items))
