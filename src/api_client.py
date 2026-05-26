"""OptcgAPI HTTP client.

Provides reusable functions for fetching card data:
    fetch_all_set_cards()   — booster set cards (/api/allSetCards/)
    fetch_all_st_cards()    — starter deck cards (/api/allSTCards/)
    fetch_all_promos()      — promo cards (/api/promos/filtered/?rarity=PR)
    fetch_card_by_id(id)    — single card lookup (/api/sets/card/{id}/)

Base URL defaults to https://optcgapi.com. Override via OPTCG_API_BASE env var.

CLI:
    python -m src.api_client                 # print count + 1 sample card
    python -m src.api_client --use-cache     # cache raw JSON to data/cache/
    python -m src.api_client --card OP05-097 # fetch one card
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_BASE_URL = os.environ.get("OPTCG_API_BASE", "https://optcgapi.com").rstrip("/")
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

ALL_SET_CARDS_PATH = "/api/allSetCards/"
ALL_ST_CARDS_PATH = "/api/allSTCards/"
PROMOS_FILTERED_PATH = "/api/promos/filtered/"
CARD_BY_SET_PATH = "/api/sets/card/{card_id}/"

DEFAULT_TIMEOUT = 30


class ApiError(RuntimeError):
    pass


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def _get_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    """GET with one retry on network error."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(1.0)
                continue
            raise ApiError(f"Failed to GET {url}: {exc}") from exc
    raise ApiError(f"Failed to GET {url}: {last_exc}")


def _unwrap_list(payload: Any) -> list[dict]:
    """Accept a bare list or a wrapped object with a known list key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "cards", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ApiError(f"Unexpected response shape: {type(payload).__name__}")


def _load_or_fetch(path: str, cache_name: str, use_cache: bool, base_url: str) -> list[dict]:
    cache_file = _cache_path(cache_name)
    if use_cache and cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as f:
            return _unwrap_list(json.load(f))

    payload = _get_json(f"{base_url}{path}")
    if use_cache:
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
    return _unwrap_list(payload)


def fetch_all_set_cards(use_cache: bool = False, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    return _load_or_fetch(ALL_SET_CARDS_PATH, "all_set_cards", use_cache, base_url)


def fetch_all_st_cards(use_cache: bool = False, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    return _load_or_fetch(ALL_ST_CARDS_PATH, "all_st_cards", use_cache, base_url)


def fetch_all_promos(use_cache: bool = False, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    cache_file = _cache_path("all_promos")
    if use_cache and cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as f:
            return _unwrap_list(json.load(f))
    url = f"{base_url}{PROMOS_FILTERED_PATH}"
    payload = _get_json(f"{url}?rarity=PR")
    if use_cache:
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
    return _unwrap_list(payload)


def fetch_card_by_id(card_id: str, base_url: str = DEFAULT_BASE_URL) -> dict | None:
    url = f"{base_url}{CARD_BY_SET_PATH.format(card_id=card_id)}"
    try:
        payload = _get_json(url)
    except ApiError as exc:
        if "404" in str(exc):
            return None
        raise
    if isinstance(payload, dict):
        for key in ("data", "card"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                return inner
        return payload
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the OptcgAPI client.")
    parser.add_argument("--use-cache", action="store_true", help="Read/write JSON cache.")
    parser.add_argument("--card", type=str, help="Fetch a single card by id.")
    parser.add_argument("--base", type=str, default=DEFAULT_BASE_URL, help="Override base URL.")
    args = parser.parse_args()

    if args.card:
        card = fetch_card_by_id(args.card, base_url=args.base)
        print(json.dumps(card, indent=2) if card else f"No card found for {args.card}")
        return

    print(f"Base URL: {args.base}")
    set_cards = fetch_all_set_cards(use_cache=args.use_cache, base_url=args.base)
    print(f"Set cards: {len(set_cards)}")
    st_cards = fetch_all_st_cards(use_cache=args.use_cache, base_url=args.base)
    print(f"Starter deck cards: {len(st_cards)}")
    if set_cards:
        print("Sample card:")
        print(json.dumps(set_cards[0], indent=2))
    promos = fetch_all_promos(use_cache=args.use_cache, base_url=args.base)
    print(f"Promo cards: {len(promos)}")
    print(f"Total: {len(set_cards) + len(st_cards) + len(promos)}")


if __name__ == "__main__":
    main()
