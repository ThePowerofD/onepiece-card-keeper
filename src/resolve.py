"""Turn a decklist's gameplay ids into real printings.

Decks name cards at gameplay level (`base_card_id`) but the collection is
printing-level (`card_image_id`), so importing has to choose a printing. These
functions are the bridge (D-003, D-016).
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple


class Printing(NamedTuple):
    card_image_id: str
    printing_variant: str | None
    name: str
    rarity: str | None


# Variant-free printings sort first; ties break on the id so the choice is
# deterministic across runs.
_PRINTING_ORDER = "ORDER BY (printing_variant IS NOT NULL), card_image_id"


def resolve_base_id(conn: sqlite3.Connection, base_card_id: str) -> list[Printing]:
    """Every printing of a gameplay card, base printing first. `[]` if unknown."""
    rows = conn.execute(
        "SELECT card_image_id, printing_variant, name, rarity FROM cards "
        f"WHERE base_card_id = ? {_PRINTING_ORDER}",
        (base_card_id,),
    ).fetchall()
    return [Printing(*row) for row in rows]


def default_printing(conn: sqlite3.Connection, base_card_id: str) -> str | None:
    """The printing a bulk import credits — variant-free when one exists.

    Every gameplay card currently has a variant-free printing (D-014's id
    normalization ensured that), so the fallback below is defensive: if a future
    set ships a card that only exists as a variant, it takes the lowest-sorting
    printing rather than failing. Returns None when the id isn't in `cards`.
    """
    row = conn.execute(
        "SELECT card_image_id FROM cards "
        f"WHERE base_card_id = ? {_PRINTING_ORDER} LIMIT 1",
        (base_card_id,),
    ).fetchone()
    return row[0] if row else None


def unknown_ids(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    """Which of these gameplay ids aren't in `cards`, in the order given.

    Unmatched cards warn but never block an import (D-006).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    if not ordered:
        return []

    placeholders = ",".join("?" * len(ordered))
    found = {
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT base_card_id FROM cards WHERE base_card_id IN ({placeholders})",
            ordered,
        )
    }
    return [cid for cid in ordered if cid not in found]
