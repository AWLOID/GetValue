from __future__ import annotations

import html as html_lib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://supremevalues.com"
WEAPON_CATEGORIES = (
    "uniques", "ancients", "vintages", "chromas", "godlies",
    "legendaries", "rares", "uncommons", "commons",
)
ALL_CATEGORIES = WEAPON_CATEGORIES + ("pets",)
CATEGORY_LABELS = {name: name.title() for name in ALL_CATEGORIES}
USER_AGENT = "MM2ValuesCache/2.0 (+public read-only value index; hourly refresh)"


class ParseError(RuntimeError):
    pass


def _clean(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html_lib.unescape(fragment).strip()


def _numeric_value(raw: str) -> int | None:
    if re.search(r"\bT\d\b", raw, re.I) or raw.lower().startswith("x"):
        return None
    match = re.search(r"([\d,.]+)\s*([kmb])?", raw, re.I)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((match.group(2) or "").lower(), 1)
    return int(number * multiplier)


def fetch_category(category: str, attempts: int = 3) -> str:
    url = f"{BASE_URL}/mm2/{category}"
    last_error = "unknown error"
    for attempt in range(attempts):
        request = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
            "Referer": f"{BASE_URL}/mm2/",
        })
        try:
            with urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="replace")
            lowered = body.casefold()
            if len(body) < 5_000 or "incapsula incident" in lowered or "request unsuccessful" in lowered:
                raise ParseError("source returned a blocking/interstitial page")
            return body
        except (HTTPError, URLError, TimeoutError, ParseError) as exc:
            last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep((2 ** attempt) + random.random())
    raise ParseError(f"{category}: {last_error}")


def parse_category(category: str, document: str) -> list[dict[str, Any]]:
    chunks = document.split('class="itemcolumn"')[1:]
    items: list[dict[str, Any]] = []
    item_type = "pet" if category == "pets" else "weapon"
    for chunk in chunks:
        card = chunk[:5_000]
        name_match = re.search(r'<div class="itemhead">(.*?)</div>', card, re.S)
        value_match = re.search(r'<b class="itemvalue[^\"]*">(.*?)</b>', card, re.S)
        if not name_match or not value_match:
            continue
        name, value = _clean(name_match.group(1)), _clean(value_match.group(1))
        if not name or not value or value.upper() == "N/A":
            continue
        val_num = _numeric_value(value)
        # Skip bogus 1,000,000 placeholders in Godlies/other categories for items like Batwing
        if val_num is not None and val_num >= 1000000 and name.lower() in {"batwing", "black luger", "mortal blade"}:
            continue

        items.append({
            "name": name,
            "value": value,
            "valueNumber": val_num,
            "type": item_type,
            "category": CATEGORY_LABELS[category],
        })
    if not items:
        raise ParseError(f"{category}: no valued items found")
    return items


def _items_by_category(data: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in ALL_CATEGORIES}
    if not data:
        return grouped
    label_to_category = {label.casefold(): key for key, label in CATEGORY_LABELS.items()}
    for item in data.get("items", []):
        category = label_to_category.get(str(item.get("category", "")).casefold())
        if category:
            grouped[category].append(item)
    return grouped


def scrape_all(previous: dict[str, Any] | None = None) -> dict[str, Any]:
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    old = _items_by_category(previous)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_category, category): category for category in ALL_CATEGORIES}
        for future in as_completed(futures):
            category = futures[future]
            try:
                results[category] = parse_category(category, future.result())
            except Exception as exc:
                errors[category] = str(exc)
                if old.get(category):
                    results[category] = old[category]

    missing = [category for category in ALL_CATEGORIES if not results.get(category)]
    if missing:
        raise ParseError("no usable data for: " + ", ".join(missing))

    # Deduplicate items across categories preferring actual numeric values
    raw_items = [item for category in ALL_CATEGORIES for item in results[category]]
    deduped: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        key = item["name"].strip().lower()
        if key not in deduped:
            deduped[key] = item
        else:
            existing = deduped[key]
            if existing.get("valueNumber") is None and item.get("valueNumber") is not None:
                deduped[key] = item

    items = sorted(
        list(deduped.values()),
        key=lambda item: (item["type"], -(item["valueNumber"] if item["valueNumber"] is not None else -1), item["name"].casefold()),
    )
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": BASE_URL,
        "partial": bool(errors),
        "errors": errors,
        "items": items,
    }


def load_seed() -> dict[str, Any]:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed.json"
    return json.loads(seed_path.read_text(encoding="utf-8"))
