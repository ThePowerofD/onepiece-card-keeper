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


# --------------------------------------------------------------------------
# management
# --------------------------------------------------------------------------

class DeckSummary(NamedTuple):
    id: int
    name: str
    leader_id: str | None
    is_physical: bool
    distinct_cards: int
    total_cards: int


class DeckCardStatus(NamedTuple):
    base_card_id: str
    name: str
    needed: int
    owned: int
    available_to_deck: int
    missing: int
    is_leader: bool


class DeckDetail(NamedTuple):
    id: int
    name: str
    leader_id: str | None
    is_physical: bool
    cards: list[DeckCardStatus]
    total_cards: int
    missing_total: int

    @property
    def is_complete(self) -> bool:
        return self.missing_total == 0


def resolve_deck(conn: sqlite3.Connection, ref: str | int) -> int:
    """Find a deck by id or name.

    Names aren't unique — D-017 lets you import a second physical copy under the
    same name — so an ambiguous name is an error telling you to use the id.
    """
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        deck_id = int(ref)
        if conn.execute("SELECT 1 FROM decks WHERE id = ?", (deck_id,)).fetchone():
            return deck_id
        raise DeckError(f"no deck with id {deck_id}")

    rows = conn.execute("SELECT id FROM decks WHERE name = ?", (str(ref).strip(),)).fetchall()
    if not rows:
        raise DeckError(f"no deck named {ref!r}")
    if len(rows) > 1:
        ids = ", ".join(str(r[0]) for r in rows)
        raise DeckError(f"{len(rows)} decks are named {ref!r} - use an id instead ({ids})")
    return rows[0][0]


def list_decks(conn: sqlite3.Connection) -> list[DeckSummary]:
    rows = conn.execute(
        "SELECT d.id, d.name, d.leader_id, d.is_physical, "
        "       COUNT(dc.id), COALESCE(SUM(dc.quantity), 0) "
        "FROM decks d LEFT JOIN deck_cards dc ON dc.deck_id = d.id "
        "GROUP BY d.id, d.name, d.leader_id, d.is_physical "
        "ORDER BY d.is_physical DESC, d.name"
    ).fetchall()
    return [
        DeckSummary(did, name, leader, bool(physical), distinct, total)
        for did, name, leader, physical, distinct, total in rows
    ]


def show_deck(conn: sqlite3.Connection, ref: str | int) -> DeckDetail:
    """Per-card owned vs missing for one deck.

    Uses availability **form 2** (DESIGN §3): a deck's own cards count as
    available to itself, so only *other* physical decks subtract. Get this wrong
    and every deck you've actually built reports as incomplete.
    """
    deck_id = resolve_deck(conn, ref)
    name, leader_id, is_physical = conn.execute(
        "SELECT name, leader_id, is_physical FROM decks WHERE id = ?", (deck_id,)
    ).fetchone()

    rows = conn.execute(
        """
        SELECT dc.base_card_id,
               COALESCE((SELECT MIN(c.name) FROM cards c
                         WHERE c.base_card_id = dc.base_card_id), dc.base_card_id),
               dc.quantity,
               COALESCE((SELECT SUM(col.quantity) FROM collection col
                         JOIN cards c2 ON c2.card_image_id = col.card_image_id
                         WHERE c2.base_card_id = dc.base_card_id), 0),
               COALESCE((SELECT SUM(o.quantity) FROM deck_cards o
                         JOIN decks od ON od.id = o.deck_id
                         WHERE o.base_card_id = dc.base_card_id
                           AND od.is_physical = 1
                           AND od.id != ?), 0)
        FROM deck_cards dc
        WHERE dc.deck_id = ?
        ORDER BY dc.base_card_id
        """,
        (deck_id, deck_id),
    ).fetchall()

    cards = []
    for base_card_id, card_name, needed, owned, committed_elsewhere in rows:
        available_to_deck = owned - committed_elsewhere
        cards.append(
            DeckCardStatus(
                base_card_id=base_card_id,
                name=card_name,
                needed=needed,
                owned=owned,
                available_to_deck=available_to_deck,
                missing=max(0, needed - available_to_deck),
                is_leader=(base_card_id == leader_id),
            )
        )

    # Leader first, then by id — matches how a decklist reads.
    cards.sort(key=lambda c: (not c.is_leader, c.base_card_id))

    return DeckDetail(
        id=deck_id,
        name=name,
        leader_id=leader_id,
        is_physical=bool(is_physical),
        cards=cards,
        total_cards=sum(c.needed for c in cards),
        missing_total=sum(c.missing for c in cards),
    )


def delete_deck(conn: sqlite3.Connection, ref: str | int) -> str:
    """Delete a deck. `deck_cards` cascades; the collection is untouched.

    You still own the cards - they simply stop being committed.
    """
    deck_id = resolve_deck(conn, ref)
    name = conn.execute("SELECT name FROM decks WHERE id = ?", (deck_id,)).fetchone()[0]
    conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    conn.commit()
    return name


def set_physical(conn: sqlite3.Connection, ref: str | int, physical: bool) -> list[str]:
    """Toggle whether a deck locks its cards. Returns warnings, never blocks.

    Flipping a wishlist deck to physical can commit more copies than you own -
    that's a legitimate state to be in briefly (you're about to buy the cards),
    so it warns rather than refusing.
    """
    deck_id = resolve_deck(conn, ref)
    conn.execute(
        "UPDATE decks SET is_physical = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (1 if physical else 0, deck_id),
    )
    conn.commit()

    if not physical:
        return []

    warnings = []
    for card in show_deck(conn, deck_id).cards:
        if card.missing > 0:
            warnings.append(
                f"{card.base_card_id}: deck needs {card.needed} but only "
                f"{max(0, card.available_to_deck)} available"
            )
    return warnings
