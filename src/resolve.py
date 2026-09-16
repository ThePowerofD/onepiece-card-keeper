"""Validate the gameplay ids a decklist names.

Decks name cards at gameplay level (`base_card_id`), and since D-030 so does
ownership, so importing no longer has to pick a printing — `default_printing`
and `resolve_base_id` went with D-016. What's left is the one check import
still needs: which ids aren't real cards.

Listing a card's printings now lives in `collection.printings_of`, next to the
ownership it's reported with.
"""

from __future__ import annotations

import sqlite3


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
