"""Orchestrate a full sync: fetch from OptcgAPI → sanitize → insert into SQLite.

Run:
    python -m src.sync
    python -m src.sync --use-cache
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.api_client import fetch_all_promos, fetch_all_set_cards, fetch_all_st_cards
from src.db_setup import DB_PATH, connect
from src.sanitize import (
    detect_keywords,
    extract_printing_variant,
    normalize_colors,
    normalize_counter,
    normalize_null,
    parse_subtypes,
    to_int,
)

FIELD_ALIASES = {
    "card_image_id": ["card_image_id"],
    "card_id":       ["card_set_id", "card_id", "base_card_id"],
    "name":          ["card_name", "name"],
    "category":      ["card_type", "category", "type"],
    "cost":          ["card_cost", "cost"],
    "power":         ["card_power", "power"],
    "counter":       ["counter_amount", "counter"],
    "color":         ["card_color", "color"],
    "sub_types":     ["sub_types", "subtypes"],
    "card_text":     ["card_text", "effect", "text"],
    "set":           ["set_id", "set", "set_code"],
    "rarity":        ["rarity"],
    "image_url":     ["card_image", "image_url", "image"],
}


def pick(row: dict, key: str):
    for alias in FIELD_ALIASES[key]:
        if alias in row:
            return row[alias]
    return None


def load_known_types(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT type_name FROM known_types")]


def sanitize_row(row: dict, known_types: list[str], now: str) -> dict | None:
    """Return sanitized column values, or None if the row should be skipped."""
    raw_image_id = pick(row, "card_image_id")
    card_image_id = normalize_null(raw_image_id)
    if card_image_id is None:
        return None

    base_card_id = normalize_null(pick(row, "card_id")) or card_image_id.split("_")[0]
    category = normalize_null(pick(row, "category"))

    keywords = detect_keywords(pick(row, "card_text"))
    raw_sub_types = pick(row, "sub_types")
    matched_types, leftover = parse_subtypes(raw_sub_types, known_types)

    return {
        "card_image_id": card_image_id,
        "base_card_id": base_card_id,
        "printing_variant": extract_printing_variant(card_image_id),
        "name": normalize_null(pick(row, "name")) or "",
        "category": category or "",
        "cost": to_int(pick(row, "cost")),
        "power": to_int(pick(row, "power")),
        "counter": normalize_counter(category, pick(row, "counter")),
        "color": normalize_colors(pick(row, "color")),
        "card_type": normalize_null(raw_sub_types),
        "effect": normalize_null(pick(row, "card_text")),
        "has_trigger": keywords["has_trigger"],
        "has_blocker": keywords["has_blocker"],
        "has_rush": keywords["has_rush"],
        "has_double_attack": keywords["has_double_attack"],
        "has_banish": keywords["has_banish"],
        "set_code": normalize_null(pick(row, "set")),
        "rarity": normalize_null(pick(row, "rarity")),
        "image_url": normalize_null(pick(row, "image_url")),
        "last_synced": now,
        "_matched_types": matched_types,
        "_leftover_types": leftover,
        "_raw_sub_types": raw_sub_types,
    }


UPSERT_CARD_SQL = """
INSERT OR REPLACE INTO cards (
    card_image_id, base_card_id, printing_variant, name, category, cost, power,
    counter, color, card_type, effect,
    has_trigger, has_blocker, has_rush, has_double_attack, has_banish,
    set_code, rarity, image_url, last_synced
) VALUES (
    :card_image_id, :base_card_id, :printing_variant, :name, :category, :cost, :power,
    :counter, :color, :card_type, :effect,
    :has_trigger, :has_blocker, :has_rush, :has_double_attack, :has_banish,
    :set_code, :rarity, :image_url, :last_synced
)
"""

UPSERT_CARD_TYPE_SQL = """
INSERT OR IGNORE INTO card_types (base_card_id, type_name) VALUES (?, ?)
"""

LOG_SKIPPED_SQL = """
INSERT INTO skipped_cards_log (raw_card_data, reason) VALUES (?, ?)
"""

LOG_UNKNOWN_SQL = """
INSERT INTO unknown_type_log (card_image_id, raw_sub_types) VALUES (?, ?)
"""


def sync(conn: sqlite3.Connection, use_cache: bool = False) -> dict[str, int]:
    known_types = load_known_types(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print("Fetching set cards...")
    set_cards = fetch_all_set_cards(use_cache=use_cache)
    print(f"  ->{len(set_cards)} rows")
    print("Fetching starter deck cards...")
    st_cards = fetch_all_st_cards(use_cache=use_cache)
    print(f"  ->{len(st_cards)} rows")
    print("Fetching promo cards...")
    promos = fetch_all_promos(use_cache=use_cache)
    print(f"  ->{len(promos)} rows")

    all_rows = set_cards + st_cards + promos
    stats = {"total": len(all_rows), "inserted": 0, "skipped": 0, "unknown_types": 0}

    cur = conn.cursor()
    for row in all_rows:
        sanitized = sanitize_row(row, known_types, now)
        if sanitized is None:
            cur.execute(LOG_SKIPPED_SQL, (json.dumps(row), "missing card_image_id"))
            stats["skipped"] += 1
            continue

        cur.execute(UPSERT_CARD_SQL, {k: v for k, v in sanitized.items() if not k.startswith("_")})
        stats["inserted"] += 1

        for type_name in sanitized["_matched_types"]:
            cur.execute(UPSERT_CARD_TYPE_SQL, (sanitized["base_card_id"], type_name))

        if sanitized["_leftover_types"]:
            cur.execute(
                LOG_UNKNOWN_SQL,
                (sanitized["card_image_id"], str(sanitized["_raw_sub_types"])),
            )
            stats["unknown_types"] += 1

    conn.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync OPTCG card data into SQLite.")
    parser.add_argument("--use-cache", action="store_true", help="Use local JSON cache.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        stats = sync(conn, use_cache=args.use_cache)
        print()
        print("=== Sync summary ===")
        print(f"  Total rows fetched: {stats['total']}")
        print(f"  Inserted/updated:   {stats['inserted']}")
        print(f"  Skipped (no id):    {stats['skipped']}")
        print(f"  Unknown sub_types:  {stats['unknown_types']}")
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        print(f"  cards table count:  {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
