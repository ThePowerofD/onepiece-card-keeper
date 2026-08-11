"""Collection commands — what you physically own, at printing level.

Quantities are per `card_image_id` (D-003). Rows are never deleted: a card you
traded away stays at quantity 0 (D-005).

`foil_quantity` exists on the table but is deliberately not exposed here — it
stays 0 through v1 (D-018).
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple


class CollectionError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class Placement(NamedTuple):
    location: str
    quantity: int


class DeckUse(NamedTuple):
    deck_name: str
    quantity: int
    is_physical: bool


class PrintingStatus(NamedTuple):
    card_image_id: str
    printing_variant: str | None
    name: str
    owned: int
    placed: int
    placements: list[Placement]


class CardStatus(NamedTuple):
    base_card_id: str
    printings: list[PrintingStatus]
    total_owned: int
    committed: int
    available: int
    decks: list[DeckUse]

    @property
    def loose(self) -> int:
        """Copies not sleeved into a physical deck."""
        return self.total_owned - self.committed

    @property
    def placed(self) -> int:
        return sum(p.placed for p in self.printings)

    @property
    def unplaced(self) -> int:
        """Loose copies whose storage location hasn't been recorded."""
        return self.loose - self.placed


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------

def _base_card_id(conn: sqlite3.Connection, card_image_id: str) -> str:
    row = conn.execute(
        "SELECT base_card_id FROM cards WHERE card_image_id = ?", (card_image_id,)
    ).fetchone()
    if row is None:
        raise CollectionError(f"unknown printing {card_image_id!r} - not in the card database")
    return row[0]


def _committed(conn: sqlite3.Connection, base_card_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(dc.quantity), 0) FROM deck_cards dc "
        "JOIN decks d ON d.id = dc.deck_id "
        "WHERE d.is_physical = 1 AND dc.base_card_id = ?",
        (base_card_id,),
    ).fetchone()
    return row[0]


def _owned_total(conn: sqlite3.Connection, base_card_id: str, excluding: str | None = None) -> int:
    sql = (
        "SELECT COALESCE(SUM(col.quantity), 0) FROM collection col "
        "JOIN cards c ON c.card_image_id = col.card_image_id "
        "WHERE c.base_card_id = ?"
    )
    params: list = [base_card_id]
    if excluding is not None:
        sql += " AND col.card_image_id != ?"
        params.append(excluding)
    return conn.execute(sql, params).fetchone()[0]


def _guard(conn: sqlite3.Connection, card_image_id: str, new_quantity: int) -> None:
    """Refuse changes that would commit more copies than you own.

    Checked at gameplay level, not printing level: a deck asks for `OP05-097`
    without naming a printing, so any printing you own can satisfy it (D-003).
    Comparing per-printing would false-positive the moment you own an alt art.
    """
    if new_quantity < 0:
        raise CollectionError("quantity cannot be negative")

    base = _base_card_id(conn, card_image_id)
    committed = _committed(conn, base)
    total_before = _owned_total(conn, base)
    total_after = _owned_total(conn, base, excluding=card_image_id) + new_quantity

    # Only block changes that make things *worse*. A collection can already be
    # over-committed — toggling a wishlist deck to physical does it — and in
    # that state adding copies is the fix, so it must never be refused.
    if total_after < committed and total_after < total_before:
        raise CollectionError(
            f"refused: {base} is in physical decks {committed} time(s) but this would "
            f"leave you owning {total_after}. Remove it from a deck first, or set "
            f"that deck to wishlist."
        )


def _current(conn: sqlite3.Connection, card_image_id: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM collection WHERE card_image_id = ?", (card_image_id,)
    ).fetchone()
    return row[0] if row else 0


def write_quantity(conn: sqlite3.Connection, card_image_id: str, quantity: int) -> int:
    """Set a quantity **without committing** — callers own the transaction.

    Deck import needs several collection writes to land atomically with the deck
    itself, so the commit can't happen in here.
    """
    conn.execute(
        "INSERT INTO collection (card_image_id, quantity) VALUES (?, ?) "
        "ON CONFLICT(card_image_id) DO UPDATE SET "
        "quantity = excluded.quantity, updated_at = CURRENT_TIMESTAMP",
        (card_image_id, quantity),
    )
    return quantity


def credit(conn: sqlite3.Connection, card_image_id: str, delta: int) -> int:
    """Add `delta` copies without committing. Returns the new quantity."""
    new_quantity = max(0, _current(conn, card_image_id) + delta)
    return write_quantity(conn, card_image_id, new_quantity)


def _write(conn: sqlite3.Connection, card_image_id: str, quantity: int) -> int:
    write_quantity(conn, card_image_id, quantity)
    conn.commit()
    return quantity


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def add(conn: sqlite3.Connection, card_image_id: str, quantity: int) -> int:
    """Add copies. Creates the row if absent. Returns the new quantity."""
    if quantity < 0:
        raise CollectionError("use remove() to subtract copies")
    new_quantity = _current(conn, card_image_id) + quantity
    _guard(conn, card_image_id, new_quantity)
    return _write(conn, card_image_id, new_quantity)


def set_quantity(conn: sqlite3.Connection, card_image_id: str, quantity: int) -> int:
    """Set an absolute quantity. Returns the new quantity."""
    _guard(conn, card_image_id, quantity)
    return _write(conn, card_image_id, quantity)


def remove(conn: sqlite3.Connection, card_image_id: str, quantity: int) -> int:
    """Subtract copies, flooring at 0. The row is kept at 0, never deleted (D-005)."""
    if quantity < 0:
        raise CollectionError("use add() to add copies")
    new_quantity = max(0, _current(conn, card_image_id) - quantity)
    _guard(conn, card_image_id, new_quantity)
    return _write(conn, card_image_id, new_quantity)


def show(conn: sqlite3.Connection, base_card_id: str) -> CardStatus | None:
    """Full picture for one gameplay card. None if the id isn't in `cards`."""
    printing_rows = conn.execute(
        "SELECT c.card_image_id, c.printing_variant, c.name, COALESCE(col.quantity, 0) "
        "FROM cards c LEFT JOIN collection col ON col.card_image_id = c.card_image_id "
        "WHERE c.base_card_id = ? "
        "ORDER BY (c.printing_variant IS NOT NULL), c.card_image_id",
        (base_card_id,),
    ).fetchall()
    if not printing_rows:
        return None

    printings = []
    for card_image_id, variant, name, owned in printing_rows:
        placements = [
            Placement(loc, qty)
            for loc, qty in conn.execute(
                "SELECT sl.name, cp.quantity FROM collection_placements cp "
                "JOIN storage_locations sl ON sl.id = cp.location_id "
                "WHERE cp.card_image_id = ? AND cp.quantity > 0 ORDER BY sl.name",
                (card_image_id,),
            )
        ]
        printings.append(
            PrintingStatus(
                card_image_id, variant, name, owned,
                sum(p.quantity for p in placements), placements,
            )
        )

    decks = [
        DeckUse(name, qty, bool(is_physical))
        for name, qty, is_physical in conn.execute(
            "SELECT d.name, dc.quantity, d.is_physical FROM deck_cards dc "
            "JOIN decks d ON d.id = dc.deck_id "
            "WHERE dc.base_card_id = ? ORDER BY d.is_physical DESC, d.name",
            (base_card_id,),
        )
    ]

    total_owned = sum(p.owned for p in printings)
    committed = _committed(conn, base_card_id)
    return CardStatus(
        base_card_id, printings, total_owned, committed, total_owned - committed, decks
    )


def list_cards(
    conn: sqlite3.Connection,
    set_code: str | None = None,
    color: str | None = None,
    category: str | None = None,
    owned_only: bool = False,
    limit: int | None = None,
) -> list[tuple]:
    """Browse printings. Returns (card_image_id, name, category, color, owned)."""
    sql = [
        "SELECT c.card_image_id, c.name, c.category, c.color, COALESCE(col.quantity, 0) AS owned",
        "FROM cards c LEFT JOIN collection col ON col.card_image_id = c.card_image_id",
        "WHERE 1=1",
    ]
    params: list = []
    if set_code:
        sql.append("AND (c.set_code = ? OR c.card_image_id LIKE ?)")
        params += [set_code, f"{set_code}-%"]
    if color:
        sql.append("AND c.color LIKE ?")
        params.append(f"%{color}%")
    if category:
        sql.append("AND c.category = ?")
        params.append(category)
    if owned_only:
        sql.append("AND COALESCE(col.quantity, 0) > 0")
    sql.append("ORDER BY c.card_image_id")
    if limit:
        sql.append("LIMIT ?")
        params.append(limit)

    return conn.execute(" ".join(sql), params).fetchall()
