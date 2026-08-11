"""Deck import — the bulk-entry path that bootstraps a collection (D-011).

Importing a physical deck creates the deck *and* credits the collection in one
transaction, so entering the decks you already own populates your inventory for
free. Wishlist decks (`is_physical = False`) record what you want and never
touch the collection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NamedTuple

from src.collection import credit
from src.deck_parser import parse_decklist
from src.resolve import default_printing, unknown_ids

MAX_COPIES = 4          # warned, never enforced (D-004)
STANDARD_DECK_SIZE = 50  # excluding the leader


class DeckError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class DeckNameExists(DeckError):
    """A deck of this name already exists; the caller must choose what to do."""

    def __init__(self, name: str, deck_id: int):
        super().__init__(
            f"a deck named {name!r} already exists - replace it, or import as a second deck"
        )
        self.name = name
        self.deck_id = deck_id


class ImportResult(NamedTuple):
    deck_id: int
    name: str
    is_physical: bool
    leader_id: str | None
    distinct_cards: int
    total_cards: int
    collection_credited: int
    replaced: bool
    warnings: list[str]


def find_deck(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name.strip(),)).fetchone()
    return row[0] if row else None


def _deck_quantities(conn: sqlite3.Connection, deck_id: int) -> dict[str, int]:
    return {
        base_card_id: qty
        for base_card_id, qty in conn.execute(
            "SELECT base_card_id, quantity FROM deck_cards WHERE deck_id = ?", (deck_id,)
        )
    }


def import_decklist(
    conn: sqlite3.Connection,
    text: str,
    name: str,
    is_physical: bool = False,
    on_conflict: str | None = None,
) -> ImportResult:
    """Import a Limitless decklist.

    `on_conflict` decides what happens when the name is taken (D-017):
        None        raise DeckNameExists so the caller can ask
        "replace"   delete the old deck and take this list as the truth
        "new"       create a second deck with the same name

    The leader is stored in `decks.leader_id` *and* as a `deck_cards` row, so a
    sleeved leader is locked out of availability like any other card.

    Everything lands in one transaction, or nothing does.
    """
    clean_name = name.strip()
    if not clean_name:
        raise DeckError("deck name cannot be empty")
    if on_conflict not in (None, "replace", "new"):
        raise DeckError(f"unknown conflict policy {on_conflict!r}")

    parsed = parse_decklist(text)
    warnings = list(parsed.warnings)

    entries: list[tuple[str, int]] = [(c.base_card_id, c.quantity) for c in parsed.cards]
    if parsed.leader_id:
        entries.insert(0, (parsed.leader_id, 1))
    if not entries:
        raise DeckError("decklist contains no recognisable cards")

    missing = unknown_ids(conn, [cid for cid, _ in entries])
    if missing:
        warnings.append(
            f"{len(missing)} card id(s) not in the card database, skipped: {', '.join(missing)}"
        )
        entries = [(cid, qty) for cid, qty in entries if cid not in set(missing)]
    if not entries:
        raise DeckError("none of the cards in this decklist are in the card database")

    for base_card_id, qty in entries:
        if qty > MAX_COPIES:
            warnings.append(f"{base_card_id}: {qty} copies exceeds the usual {MAX_COPIES}")

    non_leader_total = sum(qty for cid, qty in entries if cid != parsed.leader_id)
    if non_leader_total != STANDARD_DECK_SIZE:
        warnings.append(
            f"deck has {non_leader_total} cards excluding the leader, expected {STANDARD_DECK_SIZE}"
        )

    existing_id = find_deck(conn, clean_name)
    replaced = False
    previous: dict[str, int] = {}
    if existing_id is not None:
        if on_conflict is None:
            raise DeckNameExists(clean_name, existing_id)
        if on_conflict == "replace":
            previous = _deck_quantities(conn, existing_id)
            replaced = True

    credited = 0
    try:
        if replaced:
            # ON DELETE CASCADE clears deck_cards with it.
            conn.execute("DELETE FROM decks WHERE id = ?", (existing_id,))

        cur = conn.execute(
            "INSERT INTO decks (name, leader_id, is_physical) VALUES (?, ?, ?)",
            (clean_name, parsed.leader_id, 1 if is_physical else 0),
        )
        deck_id = cur.lastrowid

        for base_card_id, qty in entries:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
                (deck_id, base_card_id, qty),
            )

            if not is_physical:
                continue  # a wishlist deck never touches the collection

            # On replace, credit only the increase. Cards dropped from the list
            # aren't removed from the collection — you still physically own
            # them, they just became loose again.
            delta = qty - previous.get(base_card_id, 0)
            if delta <= 0:
                continue

            printing = default_printing(conn, base_card_id)
            if printing is None:  # unreachable: unknown ids were filtered above
                warnings.append(f"{base_card_id}: no printing found, collection not credited")
                continue
            credit(conn, printing, delta)
            credited += delta

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ImportResult(
        deck_id=deck_id,
        name=clean_name,
        is_physical=is_physical,
        leader_id=parsed.leader_id,
        distinct_cards=len(entries),
        total_cards=sum(qty for _, qty in entries),
        collection_credited=credited,
        replaced=replaced,
        warnings=warnings,
    )


def import_deck(
    conn: sqlite3.Connection,
    path: str | Path,
    name: str | None = None,
    is_physical: bool = False,
    on_conflict: str | None = None,
) -> ImportResult:
    """Import a decklist from a file. Defaults the deck name to the filename."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckError(f"could not read {file_path}: {exc}") from exc
    return import_decklist(conn, text, name or file_path.stem, is_physical, on_conflict)
