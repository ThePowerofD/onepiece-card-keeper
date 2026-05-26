"""Create or reset the SQLite schema for the OPTCG manager.

Run:
    python -m src.db_setup            # create if missing (idempotent)
    python -m src.db_setup --reset    # drop everything and recreate
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
        name               TEXT NOT NULL,
        category           TEXT NOT NULL,
        cost               INTEGER,
        power              INTEGER,
        counter            INTEGER,
        color              TEXT,
        card_type          TEXT,
        effect             TEXT,
        has_trigger        BOOLEAN NOT NULL DEFAULT 0,
        has_blocker        BOOLEAN NOT NULL DEFAULT 0,
        has_rush           BOOLEAN NOT NULL DEFAULT 0,
        has_double_attack  BOOLEAN NOT NULL DEFAULT 0,
        has_banish         BOOLEAN NOT NULL DEFAULT 0,
        set_code           TEXT,
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
    CREATE TABLE IF NOT EXISTS collection (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        card_image_id  TEXT NOT NULL REFERENCES cards(card_image_id),
        quantity       INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(card_image_id)
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
    CREATE TABLE IF NOT EXISTS app_settings (
        key    TEXT PRIMARY KEY,
        value  TEXT
    )
    """,
]

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
]

TABLE_NAMES = [
    "deck_cards",
    "decks",
    "collection",
    "skipped_cards_log",
    "unknown_type_log",
    "card_types",
    "known_types",
    "cards",
    "app_settings",
]


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def drop_all(conn: sqlite3.Connection) -> None:
    for name in TABLE_NAMES:
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.commit()


def create_schema(conn: sqlite3.Connection) -> None:
    for stmt in TABLES:
        conn.execute(stmt)
    for stmt in INDEXES:
        conn.execute(stmt)
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
            print(f"Dropping existing tables in {args.db}...")
            drop_all(conn)
        print(f"Creating schema in {args.db}...")
        create_schema(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        print(f"Tables present: {', '.join(tables)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
