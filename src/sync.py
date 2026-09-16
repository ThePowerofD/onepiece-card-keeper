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
from src.collisions import resolve_collisions
from src.db_setup import DB_PATH, connect
from src.overrides import apply_overrides
from src.rotation import block_for
from src.sanitize import (
    MAX_PLAUSIBLE_COUNTER,
    detect_keywords,
    extract_printing_variant,
    game_set_from_id,
    normalize_card_image_id,
    normalize_attributes,
    normalize_colors,
    normalize_counter,
    normalize_life,
    normalize_null,
    parse_subtypes,
    printing_kind,
    repair_field_shift,
    strip_printing_suffix,
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
    "attribute":     ["attribute"],
    "life":          ["life"],
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
    card_image_id = normalize_card_image_id(raw_image_id)
    if card_image_id is None:
        return None

    # A row re-identified by resolve_collisions (D-029) carries a synthesised
    # id like 'OP01-016_alternate_art' that the suffix rules can't parse, so it
    # also carries the values they would have produced.
    base_card_id = row.get("_base_card_id") or (
        strip_printing_suffix(pick(row, "card_id")) or strip_printing_suffix(card_image_id)
    )
    category = normalize_null(pick(row, "category"))

    keywords = detect_keywords(pick(row, "card_text"))
    set_code = normalize_null(pick(row, "set"))
    game_set = game_set_from_id(base_card_id)
    if "_printing_variant" in row:
        variant, kind = row["_printing_variant"], row["_printing_kind"]
    else:
        variant = extract_printing_variant(card_image_id)
        kind = printing_kind(variant)
    raw_power, raw_sub_types, repaired = repair_field_shift(
        pick(row, "power"), pick(row, "sub_types")
    )
    matched_types, leftover = parse_subtypes(raw_sub_types, known_types)

    return {
        "card_image_id": card_image_id,
        "base_card_id": base_card_id,
        "printing_variant": variant,
        "printing_kind": kind,
        "name": normalize_null(pick(row, "name")) or "",
        "category": category or "",
        "cost": to_int(pick(row, "cost")),
        "power": to_int(raw_power),
        "counter": normalize_counter(category, pick(row, "counter")),
        "color": normalize_colors(pick(row, "color")),
        "card_type": normalize_null(raw_sub_types),
        "attribute": normalize_attributes(pick(row, "attribute")),
        "life": normalize_life(category, pick(row, "life")),
        "effect": normalize_null(pick(row, "card_text")),
        "has_trigger": keywords["has_trigger"],
        "has_blocker": keywords["has_blocker"],
        "has_rush": keywords["has_rush"],
        "has_double_attack": keywords["has_double_attack"],
        "has_banish": keywords["has_banish"],
        "set_code": set_code,
        "game_set": game_set,
        "rotation_block": block_for(game_set),
        "rarity": normalize_null(pick(row, "rarity")),
        "image_url": normalize_null(pick(row, "image_url")),
        "last_synced": now,
        "_matched_types": matched_types,
        "_leftover_types": leftover,
        "_raw_sub_types": raw_sub_types,
        "_repaired": repaired,
    }


UPSERT_CARD_SQL = """
INSERT OR REPLACE INTO cards (
    card_image_id, base_card_id, printing_variant, printing_kind, name, category, cost, power,
    counter, color, card_type, attribute, life, effect,
    has_trigger, has_blocker, has_rush, has_double_attack, has_banish,
    set_code, game_set, rotation_block, rarity, image_url, last_synced
) VALUES (
    :card_image_id, :base_card_id, :printing_variant, :printing_kind, :name, :category, :cost, :power,
    :counter, :color, :card_type, :attribute, :life, :effect,
    :has_trigger, :has_blocker, :has_rush, :has_double_attack, :has_banish,
    :set_code, :game_set, :rotation_block, :rarity, :image_url, :last_synced
)
"""

UPSERT_CARD_TYPE_SQL = """
INSERT OR IGNORE INTO card_types (base_card_id, type_name) VALUES (?, ?)
"""

# The same card in a different art is the same card, so these must match across
# every printing of a base_card_id (D-031). Name, rarity, image and set_code are
# genuinely per-printing and stay untouched.
INHERITED_FIELDS = (
    "category", "cost", "power", "counter", "color", "card_type",
    "attribute", "life", "effect",
    "has_trigger", "has_blocker", "has_rush", "has_double_attack", "has_banish",
)

# Fields where 0 means "the API didn't fill this in" rather than a real value.
# Only counter: a cost or power of 0 is legitimate, but every base printing
# reading counter 0 against variants reading 1000 turned out to be missing data.
ZERO_IS_EMPTY = {"counter"}


def _is_empty(field: str, value) -> bool:
    if value is None:
        return True
    if field in ZERO_IS_EMPTY and value == 0:
        return True
    return isinstance(value, str) and not value.strip()


def inherit_from_base_printing(conn: sqlite3.Connection) -> dict[str, int]:
    """Make every printing of a card agree on its gameplay fields (D-031).

    The base printing wins, because the set feed is the authoritative print and
    the SP/promo rows are where the typos live. Where the base itself is empty,
    the first printing with a real value supplies it - that case is always the
    base row missing data, never a variant inventing it.

    Returns counts of what changed, plus implausible counters for the summary.
    """
    stats = {"cards_reconciled": 0, "fields_rewritten": 0, "implausible_counters": 0,
             "errata_outranks_base": 0}
    columns = ", ".join(INHERITED_FIELDS)

    # Tripwire, measured before anything is rewritten. The API stamps
    # "officially errata'd" onto the printings it has corrected. If a *variant*
    # carries that stamp and its base doesn't, the base is the stale row and
    # base-wins is backwards for that card. There is no such card in the current
    # feed (of 91 stamped cards, every one has it on the base), which is why the
    # errata marker isn't used as a resolution rule - but if one ever appears,
    # this is the assumption it breaks, so say so rather than silently
    # overwriting the corrected text with the outdated one (D-031).
    stats["errata_outranks_base"] = conn.execute(
        "SELECT COUNT(DISTINCT v.base_card_id) FROM cards v "
        "JOIN cards b ON b.card_image_id = v.base_card_id "
        "WHERE v.card_image_id != v.base_card_id "
        "  AND LOWER(IFNULL(v.effect, '')) LIKE '%errata%' "
        "  AND LOWER(IFNULL(b.effect, '')) NOT LIKE '%errata%'"
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT base_card_id, card_image_id, {columns} FROM cards "
        "ORDER BY base_card_id, (card_image_id != base_card_id), card_image_id"
    ).fetchall()

    by_card: dict[str, list[tuple]] = {}
    for row in rows:
        by_card.setdefault(row[0], []).append(row)

    for base_card_id, printings in by_card.items():
        if len(printings) < 2:
            continue
        # printings[0] is the base row when one exists (ORDER BY above).
        canonical = {}
        for index, field in enumerate(INHERITED_FIELDS, start=2):
            values = [p[index] for p in printings]
            chosen = next((v for v in values if not _is_empty(field, v)), values[0])
            canonical[field] = chosen

        touched = False
        for printing in printings:
            stale = {
                field: canonical[field]
                for index, field in enumerate(INHERITED_FIELDS, start=2)
                if printing[index] != canonical[field]
            }
            if not stale:
                continue
            conn.execute(
                "UPDATE cards SET " + ", ".join(f"{f} = ?" for f in stale)
                + " WHERE card_image_id = ?",
                (*stale.values(), printing[1]),
            )
            stats["fields_rewritten"] += len(stale)
            touched = True
        if touched:
            stats["cards_reconciled"] += 1

    stats["implausible_counters"] = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE counter > ?", (MAX_PLAUSIBLE_COUNTER,)
    ).fetchone()[0]
    return stats

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

    # Tag each row with its feed: the resolver prefers non-promo rows as the
    # base printing, and the promo feed is where most reused ids come from.
    all_rows = [
        {**row, "_source": source}
        for source, feed in (("set", set_cards), ("st", st_cards), ("promos", promos))
        for row in feed
    ]
    all_rows, collision_stats = resolve_collisions(all_rows)
    stats = {
        "total": len(all_rows) + collision_stats["exact_duplicates"],
        "inserted": 0,
        "skipped": 0,
        "unknown_types": 0,
        "repaired": 0,
        "collisions": collision_stats["collisions"],
        "exact_duplicates": collision_stats["exact_duplicates"],
        "re_identified": sum(
            collision_stats[k] for k in ("by_filename", "by_name", "by_name_and_set", "by_ordinal")
        ),
    }

    cur = conn.cursor()
    for row in all_rows:
        sanitized = sanitize_row(row, known_types, now)
        if sanitized is None:
            cur.execute(LOG_SKIPPED_SQL, (json.dumps(row), "missing card_image_id"))
            stats["skipped"] += 1
            continue

        cur.execute(UPSERT_CARD_SQL, {k: v for k, v in sanitized.items() if not k.startswith("_")})
        stats["inserted"] += 1
        if sanitized["_repaired"]:
            stats["repaired"] += 1

        for type_name in sanitized["_matched_types"]:
            cur.execute(UPSERT_CARD_TYPE_SQL, (sanitized["base_card_id"], type_name))

        if sanitized["_leftover_types"]:
            cur.execute(
                LOG_UNKNOWN_SQL,
                (sanitized["card_image_id"], str(sanitized["_raw_sub_types"])),
            )
            stats["unknown_types"] += 1

    # Same card, different art: reconcile the gameplay fields, then apply the
    # hand-maintained corrections for what the feed gets wrong outright (D-031).
    stats.update(inherit_from_base_printing(conn))
    stats["overridden"] = apply_overrides(conn)

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
        print(f"  Field-shift repairs:{stats['repaired']}")
        print(f"  Inherited fields:   {stats['fields_rewritten']} "
              f"across {stats['cards_reconciled']} cards (D-031)")
        if stats["overridden"]:
            print(f"  Manual overrides:   {stats['overridden']} rows")
        if stats["errata_outranks_base"]:
            print(f"  WARNING: {stats['errata_outranks_base']} card(s) have an errata'd "
                  f"variant but an un-errata'd base - check before trusting base-wins (D-031)")
        if stats["implausible_counters"]:
            print(f"  WARNING: {stats['implausible_counters']} card(s) have a counter "
                  f"above {MAX_PLAUSIBLE_COUNTER} - likely power leaked into the field")
        print(f"  Id collisions:      {stats['collisions']} "
              f"({stats['re_identified']} printings re-identified, "
              f"{stats['exact_duplicates']} exact duplicates dropped - D-029)")
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        print(f"  cards table count:  {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
