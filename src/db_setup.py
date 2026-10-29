"""Create or reset the SQLite schema for the OPTCG manager.

Run:
    python -m src.db_setup            # create if missing (idempotent)
    python -m src.db_setup --reset    # drop everything and recreate

--reset snapshots the database into data/backups/ first whenever it holds
decks, copies or locations (D-032).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "optcg.db"

TABLES: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS cards (
        card_image_id      TEXT PRIMARY KEY,
        base_card_id       TEXT NOT NULL,
        printing_variant   TEXT,
        printing_kind      TEXT,
        name               TEXT NOT NULL,
        category           TEXT NOT NULL,
        cost               INTEGER,
        power              INTEGER,
        counter            INTEGER,
        color              TEXT,
        card_type          TEXT,
        attribute          TEXT,
        life               INTEGER,
        effect             TEXT,
        has_trigger        BOOLEAN NOT NULL DEFAULT 0,
        has_blocker        BOOLEAN NOT NULL DEFAULT 0,
        has_rush           BOOLEAN NOT NULL DEFAULT 0,
        has_double_attack  BOOLEAN NOT NULL DEFAULT 0,
        has_banish         BOOLEAN NOT NULL DEFAULT 0,
        set_code           TEXT,
        game_set           TEXT,
        rotation_block     INTEGER,
        rarity             TEXT,
        image_url          TEXT,
        last_synced        TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_types (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        base_card_id TEXT NOT NULL,
        type_name    TEXT NOT NULL,
        UNIQUE(base_card_id, type_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS known_types (
        type_name   TEXT PRIMARY KEY,
        added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS unknown_type_log (
        log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        card_image_id   TEXT NOT NULL,
        raw_sub_types   TEXT NOT NULL,
        detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved        BOOLEAN NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skipped_cards_log (
        log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_card_data   TEXT,
        reason          TEXT NOT NULL,
        detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS storage_locations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'storage'
                   CHECK(kind IN ('storage', 'binder', 'loose', 'loan')),
        notes      TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- Name is unique per kind, not globally: a box and a binder may both
        -- reasonably be called 'Box 1'. Decks are the fifth category and live
        -- in `decks`, not here.
        UNIQUE(name, kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decks (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        leader_id    TEXT,
        is_physical  BOOLEAN NOT NULL DEFAULT 0,
        notes        TEXT,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deck_cards (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id      INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        base_card_id TEXT NOT NULL,
        quantity     INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
        UNIQUE(deck_id, base_card_id)
    )
    """,
    """
    -- Ownership is the set of copies, and every copy is in exactly one
    -- container: a storage location or a physical deck (D-030). The total you
    -- own is SUM(quantity); there is no separate count to keep in agreement.
    --
    -- card_image_id NULL means the art hasn't been sorted yet. Setting the art
    -- on some copies splits the row; the total never changes.
    --
    -- Neither foreign key cascades on purpose: deleting a location or a deck
    -- must *move* its copies to just-loose, never destroy them, so the FK
    -- refuses until the caller has done that.
    CREATE TABLE IF NOT EXISTS copies (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        base_card_id  TEXT NOT NULL,
        card_image_id TEXT REFERENCES cards(card_image_id),
        location_id   INTEGER REFERENCES storage_locations(id),
        deck_id       INTEGER REFERENCES decks(id),
        quantity      INTEGER NOT NULL CHECK(quantity > 0),
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CHECK ((location_id IS NULL) != (deck_id IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key    TEXT PRIMARY KEY,
        value  TEXT
    )
    """,
]

#: The built-in loose pile. Copies land here when no container is named, and
#: are taken from here when none is named (D-030). Not renamable or deletable;
#: the owner can add other loose locations alongside it.
DEFAULT_LOOSE_LOCATION = "just-loose"

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_cards_base_card_id ON cards(base_card_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_category ON cards(category)",
    "CREATE INDEX IF NOT EXISTS idx_cards_color ON cards(color)",
    "CREATE INDEX IF NOT EXISTS idx_cards_set_code ON cards(set_code)",
    "CREATE INDEX IF NOT EXISTS idx_cards_trigger ON cards(has_trigger)",
    "CREATE INDEX IF NOT EXISTS idx_cards_blocker ON cards(has_blocker)",
    "CREATE INDEX IF NOT EXISTS idx_cards_rush ON cards(has_rush)",
    "CREATE INDEX IF NOT EXISTS idx_cards_double_attack ON cards(has_double_attack)",
    "CREATE INDEX IF NOT EXISTS idx_cards_banish ON cards(has_banish)",
    "CREATE INDEX IF NOT EXISTS idx_card_types_base_card_id ON card_types(base_card_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_types_type_name ON card_types(type_name)",
    "CREATE INDEX IF NOT EXISTS idx_copies_base ON copies(base_card_id)",
    "CREATE INDEX IF NOT EXISTS idx_copies_location ON copies(location_id)",
    "CREATE INDEX IF NOT EXISTS idx_copies_deck ON copies(deck_id)",
    # One row per (card, art, container). COALESCE is required: SQLite treats
    # NULLs as distinct in a plain UNIQUE, so unsorted rows would duplicate.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_copies_slot ON copies("
    "  base_card_id, COALESCE(card_image_id, ''),"
    "  COALESCE(location_id, -1), COALESCE(deck_id, -1))",
    "CREATE INDEX IF NOT EXISTS idx_locations_kind ON storage_locations(kind)",
]

# Availability is computed, never stored (D-009) - in Python, by
# `collection.show()` and `decks.show_deck()`. There is no view: D-026 removed
# `available_cards` because SQL at printing level could not express a
# gameplay-level commitment without double-subtracting it.
VIEWS: list[str] = []

# Dropped on --reset but never created, so databases predating D-026 lose the
# stale view instead of keeping a wrong one nothing reads. Delete this once no
# such database is in use.
LEGACY_VIEW_NAMES = ["available_cards"]

# Drop order: children before parents, so foreign keys never block a reset.
TABLE_NAMES = [
    "copies",
    "deck_cards",
    "decks",
    "storage_locations",
    "skipped_cards_log",
    "unknown_type_log",
    "card_types",
    "known_types",
    "cards",
    "app_settings",
]

# Dropped on --reset but never created: `collection` and `collection_placements`
# were replaced by `copies` in D-030. Listed first so they go before the tables
# they reference. Delete this once no database predating D-030 is in use.
LEGACY_TABLE_NAMES = ["collection_placements", "collection"]


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def drop_all(conn: sqlite3.Connection) -> None:
    for name in LEGACY_VIEW_NAMES:
        conn.execute(f"DROP VIEW IF EXISTS {name}")
    for name in LEGACY_TABLE_NAMES + TABLE_NAMES:
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.commit()


def create_schema(conn: sqlite3.Connection) -> None:
    for stmt in TABLES:
        conn.execute(stmt)
    for stmt in INDEXES:
        conn.execute(stmt)
    for stmt in VIEWS:
        conn.execute(stmt)
    conn.execute(
        "INSERT OR IGNORE INTO storage_locations (name, kind, notes) VALUES (?, 'loose', ?)",
        (DEFAULT_LOOSE_LOCATION, "built-in: copies with no container named"),
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the OPTCG SQLite schema.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables before recreating the schema.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Path to the SQLite file (default: {DB_PATH}).",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        if args.reset:
            # Imported here: backup imports this module for the loose pile's name.
            from src.backup import auto_snapshot

            saved = auto_snapshot(conn, "before-reset")
            if saved:
                print(f"Saved your decks and collection to {saved}")
            print(f"Dropping existing tables in {args.db}...")
            drop_all(conn)
        print(f"Creating schema in {args.db}...")
        create_schema(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        views = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        )]
        print(f"Tables present: {', '.join(tables)}")
        print(f"Views present: {', '.join(views) if views else '(none - D-026)'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
