"""Storage locations — where the loose cards physically are (D-019).

`collection.quantity` stays the single source of truth for how many you own.
Placements only describe where the copies that aren't sleeved into a deck are
sitting. Placement is gradual bookkeeping across thousands of cards, so it must
never be a precondition for recording ownership: over-placement **warns**, it
does not block.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple


class StorageError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class LocationSummary(NamedTuple):
    id: int
    name: str
    notes: str | None
    distinct_cards: int
    total_cards: int


class PlaceResult(NamedTuple):
    quantity: int
    warning: str | None


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------

def _location_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM storage_locations WHERE name = ?", (name.strip(),)
    ).fetchone()
    if row is None:
        raise StorageError(f"no storage location named {name!r} - create it with `location add`")
    return row[0]


def _require_card(conn: sqlite3.Connection, card_image_id: str) -> str:
    row = conn.execute(
        "SELECT base_card_id FROM cards WHERE card_image_id = ?", (card_image_id,)
    ).fetchone()
    if row is None:
        raise StorageError(f"unknown printing {card_image_id!r} - not in the card database")
    return row[0]


def _overplacement_warning(conn: sqlite3.Connection, card_image_id: str, base_card_id: str) -> str | None:
    """Flag bookkeeping that can't be physically true. Never blocks."""
    owned = conn.execute(
        "SELECT COALESCE(quantity, 0) FROM collection WHERE card_image_id = ?",
        (card_image_id,),
    ).fetchone()
    owned = owned[0] if owned else 0
    placed = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM collection_placements WHERE card_image_id = ?",
        (card_image_id,),
    ).fetchone()[0]

    if placed > owned:
        return (
            f"{card_image_id}: {placed} placed but only {owned} owned - "
            f"check the quantities"
        )

    # Gameplay-level check: copies sleeved into physical decks aren't loose, so
    # they can't also be in a binder.
    total_owned = conn.execute(
        "SELECT COALESCE(SUM(col.quantity), 0) FROM collection col "
        "JOIN cards c ON c.card_image_id = col.card_image_id WHERE c.base_card_id = ?",
        (base_card_id,),
    ).fetchone()[0]
    committed = conn.execute(
        "SELECT COALESCE(SUM(dc.quantity), 0) FROM deck_cards dc "
        "JOIN decks d ON d.id = dc.deck_id WHERE d.is_physical = 1 AND dc.base_card_id = ?",
        (base_card_id,),
    ).fetchone()[0]
    total_placed = conn.execute(
        "SELECT COALESCE(SUM(cp.quantity), 0) FROM collection_placements cp "
        "JOIN cards c ON c.card_image_id = cp.card_image_id WHERE c.base_card_id = ?",
        (base_card_id,),
    ).fetchone()[0]

    loose = total_owned - committed
    if total_placed > loose:
        return (
            f"{base_card_id}: {total_placed} placed but only {loose} loose "
            f"({committed} sleeved in physical decks)"
        )
    return None


# --------------------------------------------------------------------------
# locations
# --------------------------------------------------------------------------

def add_location(conn: sqlite3.Connection, name: str, notes: str | None = None) -> int:
    clean = name.strip()
    if not clean:
        raise StorageError("location name cannot be empty")
    try:
        cur = conn.execute(
            "INSERT INTO storage_locations (name, notes) VALUES (?, ?)", (clean, notes)
        )
    except sqlite3.IntegrityError:
        raise StorageError(f"a location named {clean!r} already exists") from None
    conn.commit()
    return cur.lastrowid


def list_locations(conn: sqlite3.Connection) -> list[LocationSummary]:
    rows = conn.execute(
        # SUM(CASE ...) rather than COUNT(...) FILTER: FILTER needs SQLite 3.30+
        # and this should run anywhere Python does.
        "SELECT sl.id, sl.name, sl.notes, "
        "       COALESCE(SUM(CASE WHEN cp.quantity > 0 THEN 1 ELSE 0 END), 0), "
        "       COALESCE(SUM(cp.quantity), 0) "
        "FROM storage_locations sl "
        "LEFT JOIN collection_placements cp ON cp.location_id = sl.id "
        "GROUP BY sl.id, sl.name, sl.notes ORDER BY sl.name"
    ).fetchall()
    return [LocationSummary(*row) for row in rows]


def rename_location(conn: sqlite3.Connection, old_name: str, new_name: str) -> None:
    clean = new_name.strip()
    if not clean:
        raise StorageError("location name cannot be empty")
    loc_id = _location_id(conn, old_name)
    try:
        conn.execute("UPDATE storage_locations SET name = ? WHERE id = ?", (clean, loc_id))
    except sqlite3.IntegrityError:
        raise StorageError(f"a location named {clean!r} already exists") from None
    conn.commit()


def delete_location(conn: sqlite3.Connection, name: str) -> int:
    """Delete a location. Placements cascade; ownership is untouched.

    Returns how many placement rows went with it.
    """
    loc_id = _location_id(conn, name)
    count = conn.execute(
        "SELECT COUNT(*) FROM collection_placements WHERE location_id = ?", (loc_id,)
    ).fetchone()[0]
    conn.execute("DELETE FROM storage_locations WHERE id = ?", (loc_id,))
    conn.commit()
    return count


# --------------------------------------------------------------------------
# placements
# --------------------------------------------------------------------------

def place(conn: sqlite3.Connection, card_image_id: str, location: str, quantity: int) -> PlaceResult:
    """Record that `quantity` copies of a printing sit in `location`.

    Absolute, not additive — placing 2 twice leaves 2, so re-running a stocktake
    is safe. Quantity 0 removes the placement row.
    """
    if quantity < 0:
        raise StorageError("quantity cannot be negative")

    base_card_id = _require_card(conn, card_image_id)
    loc_id = _location_id(conn, location)

    if quantity == 0:
        conn.execute(
            "DELETE FROM collection_placements WHERE card_image_id = ? AND location_id = ?",
            (card_image_id, loc_id),
        )
    else:
        conn.execute(
            "INSERT INTO collection_placements (card_image_id, location_id, quantity) "
            "VALUES (?, ?, ?) ON CONFLICT(card_image_id, location_id) DO UPDATE SET "
            "quantity = excluded.quantity, updated_at = CURRENT_TIMESTAMP",
            (card_image_id, loc_id, quantity),
        )
    conn.commit()
    return PlaceResult(quantity, _overplacement_warning(conn, card_image_id, base_card_id))


def unplace(
    conn: sqlite3.Connection, card_image_id: str, location: str, quantity: int | None = None
) -> int:
    """Remove copies from a location. `quantity=None` removes all of them.

    Returns the quantity remaining at that location.
    """
    _require_card(conn, card_image_id)
    loc_id = _location_id(conn, location)

    row = conn.execute(
        "SELECT quantity FROM collection_placements WHERE card_image_id = ? AND location_id = ?",
        (card_image_id, loc_id),
    ).fetchone()
    if row is None:
        return 0

    remaining = 0 if quantity is None else max(0, row[0] - quantity)
    if remaining == 0:
        conn.execute(
            "DELETE FROM collection_placements WHERE card_image_id = ? AND location_id = ?",
            (card_image_id, loc_id),
        )
    else:
        conn.execute(
            "UPDATE collection_placements SET quantity = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE card_image_id = ? AND location_id = ?",
            (remaining, card_image_id, loc_id),
        )
    conn.commit()
    return remaining


def contents(conn: sqlite3.Connection, location: str, limit: int | None = None) -> list[tuple]:
    """What's in a location: (card_image_id, name, quantity)."""
    loc_id = _location_id(conn, location)
    sql = (
        "SELECT cp.card_image_id, c.name, cp.quantity FROM collection_placements cp "
        "JOIN cards c ON c.card_image_id = cp.card_image_id "
        "WHERE cp.location_id = ? AND cp.quantity > 0 ORDER BY cp.card_image_id"
    )
    params: list = [loc_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
