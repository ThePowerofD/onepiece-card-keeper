"""Decks — a recipe, and for physical decks the copies sleeved into it.

A deck does two jobs. The **recipe** (`deck_cards`) says what the deck is made
of, at gameplay level, and exists even for a wishlist deck you own nothing of.
The **container** holds the copies actually sleeved into it — ordinary `copies`
rows, the same as a binder's (D-030).

Importing a physical deck writes the recipe *and* places its copies in one
transaction, so entering the decks you already own populates your inventory for
free (D-011). Wishlist decks are recipe-only and hold no copies.

A physical deck can be incomplete, and that is the mechanism rather than a
defect: strip a deck to build another and the hole stays visible until it's
refilled.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NamedTuple

from optcg.backup import auto_snapshot
from optcg.collection import committed_elsewhere, deposit, empty_container, total_owned
from optcg.deck_parser import parse_decklist
from optcg.resolve import unknown_ids
from optcg.storage import deck_container

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


def _copies_in_deck(conn: sqlite3.Connection, deck_id: int) -> dict[str, int]:
    """How many copies of each card are actually sleeved into this deck."""
    return {
        base_card_id: qty
        for base_card_id, qty in conn.execute(
            "SELECT base_card_id, SUM(quantity) FROM copies WHERE deck_id = ? "
            "GROUP BY base_card_id",
            (deck_id,),
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
    if existing_id is not None:
        if on_conflict is None:
            raise DeckNameExists(clean_name, existing_id)
        if on_conflict == "replace":
            replaced = True
            auto_snapshot(conn, "before-deck-replace")

    credited = 0
    try:
        if replaced:
            # The deck row survives so its sleeved copies stay attached to it
            # (D-030). Only the recipe is rewritten.
            deck_id = existing_id
            conn.execute(
                "UPDATE decks SET leader_id = ?, is_physical = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (parsed.leader_id, 1 if is_physical else 0, deck_id),
            )
            conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
        else:
            cur = conn.execute(
                "INSERT INTO decks (name, leader_id, is_physical) VALUES (?, ?, ?)",
                (clean_name, parsed.leader_id, 1 if is_physical else 0),
            )
            deck_id = cur.lastrowid

        container = deck_container(conn, deck_id)
        already = _copies_in_deck(conn, deck_id)

        for base_card_id, qty in entries:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
                (deck_id, base_card_id, qty),
            )

            if not is_physical:
                continue  # a wishlist deck holds no copies

            # Place only the shortfall. Measuring against the copies already in
            # the deck rather than against the previous recipe means a re-import
            # never double-places, and a wishlist deck promoted to physical
            # places everything it's missing (D-022, D-030).
            shortfall = qty - already.get(base_card_id, 0)
            if shortfall <= 0:
                continue
            deposit(conn, base_card_id, shortfall, container)
            credited += shortfall

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
    needed: int            # what the recipe asks for; 0 = sleeved but not in the recipe
    owned: int             # copies of this card anywhere
    in_deck: int           # copies actually sleeved into this deck
    available_to_deck: int
    missing: int           # can't be built: needed beyond what's available
    is_leader: bool

    @property
    def unsleeved(self) -> int:
        """Recipe slots this deck hasn't been filled with yet."""
        return max(0, self.needed - self.in_deck)


class DeckDetail(NamedTuple):
    id: int
    name: str
    leader_id: str | None
    is_physical: bool
    cards: list[DeckCardStatus]
    total_cards: int
    missing_total: int
    sleeved_total: int

    @property
    def is_complete(self) -> bool:
        """Owning enough to build it — the question a wishlist deck asks."""
        return self.missing_total == 0

    @property
    def unsleeved_total(self) -> int:
        """Recipe slots not yet filled with real copies (physical decks)."""
        return sum(c.unsleeved for c in self.cards)

    @property
    def is_built(self) -> bool:
        return self.unsleeved_total == 0


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

    recipe = conn.execute(
        "SELECT dc.base_card_id, dc.quantity, "
        "       COALESCE((SELECT MIN(c.name) FROM cards c "
        "                 WHERE c.base_card_id = dc.base_card_id), dc.base_card_id) "
        "FROM deck_cards dc WHERE dc.deck_id = ? ORDER BY dc.base_card_id",
        (deck_id,),
    ).fetchall()
    sleeved = _copies_in_deck(conn, deck_id)

    # Copies sleeved into the deck that the recipe no longer asks for. Replacing
    # a decklist never pulls cards out of the sleeves (D-030), so they show up
    # here with needed = 0 until they're moved.
    extras = [
        (base_card_id, 0, conn.execute(
            "SELECT COALESCE(MIN(name), ?) FROM cards WHERE base_card_id = ?",
            (base_card_id, base_card_id),
        ).fetchone()[0])
        for base_card_id in sleeved
        if base_card_id not in {r[0] for r in recipe}
    ]

    cards = []
    for base_card_id, needed, card_name in list(recipe) + extras:
        owned = total_owned(conn, base_card_id)
        available_to_deck = owned - committed_elsewhere(conn, base_card_id, deck_id)
        cards.append(
            DeckCardStatus(
                base_card_id=base_card_id,
                name=card_name,
                needed=needed,
                owned=owned,
                in_deck=sleeved.get(base_card_id, 0),
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
        sleeved_total=sum(c.in_deck for c in cards),
    )


class DeckDeletion(NamedTuple):
    name: str
    copies_returned: int


def delete_deck(conn: sqlite3.Connection, ref: str | int) -> DeckDeletion:
    """Delete a deck. Its sleeved copies go back to just-loose; `deck_cards` cascades.

    Snapshots the database first (D-032): the recipe is gone for good.

    Unmaking a deck doesn't lose you cards — they return to the loose pile,
    which is also what makes stripping a deck to build another work (D-030).
    """
    deck_id = resolve_deck(conn, ref)
    name = conn.execute("SELECT name FROM decks WHERE id = ?", (deck_id,)).fetchone()[0]
    auto_snapshot(conn, "before-deck-delete")
    try:
        returned = empty_container(conn, deck_container(conn, deck_id))
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return DeckDeletion(name, returned)


def set_physical(conn: sqlite3.Connection, ref: str | int, physical: bool) -> list[str]:
    """Toggle whether a deck holds real copies. Returns warnings, never blocks.

    Turning a deck back into a wishlist **unmakes** it: the sleeved copies
    return to just-loose. Turning a wishlist deck physical places nothing — you
    haven't sleeved it yet — so it reports as unbuilt until copies are moved in.
    """
    deck_id = resolve_deck(conn, ref)
    try:
        if not physical:
            empty_container(conn, deck_container(conn, deck_id))
        conn.execute(
            "UPDATE decks SET is_physical = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if physical else 0, deck_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if not physical:
        return []

    warnings = []
    for card in show_deck(conn, deck_id).cards:
        if card.missing > 0:
            warnings.append(
                f"{card.base_card_id}: deck needs {card.needed} but only "
                f"{max(0, card.available_to_deck)} available"
            )
        elif card.unsleeved > 0:
            warnings.append(
                f"{card.base_card_id}: {card.unsleeved} of {card.needed} not sleeved "
                f"into this deck yet"
            )
    return warnings
