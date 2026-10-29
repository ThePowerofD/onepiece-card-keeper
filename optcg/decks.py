"""Decks — a recipe, and for physical decks the copies in it.

A deck does two jobs. The **recipe** (`deck_cards`) says what the deck is made
of, at gameplay level, and exists even for a wishlist deck you own nothing of.
The **container** holds the copies actually in the deck — ordinary `copies`
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
from optcg.collection import (
    adjust_recipe,
    committed_elsewhere,
    deposit,
    empty_container,
    move,
    take_any,
    total_owned,
)
from optcg.deck_parser import parse_decklist
from optcg.resolve import unknown_ids
from optcg.storage import Container, deck_container, loose_container

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
    returned_to_loose: int = 0     # copies a replace took out of the deck (D-041)


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
    """How many copies of each card are actually in this deck."""
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
    leader in the deck is locked out of availability like any other card.

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
    returned = 0
    try:
        if replaced:
            # The deck row survives so the copies in it stay attached to it
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

        # A physical deck is exactly what's in it (D-041): cards the new list
        # runs fewer of - or dropped - leave the deck for just-loose. You still
        # own them (D-022); they just aren't in this deck any more.
        new_counts = {cid: qty for cid, qty in entries}
        if replaced and not is_physical:
            # Replaced by a wishlist: a wishlist holds no cards (D-010).
            returned += empty_container(conn, container)
            already = {}
        elif replaced:
            loose = loose_container(conn)
            for base_card_id, have in already.items():
                excess = have - new_counts.get(base_card_id, 0)
                if excess > 0:
                    for art, piece in take_any(conn, base_card_id, excess, container):
                        deposit(conn, base_card_id, piece, loose, art)
                    returned += excess
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
        returned_to_loose=returned,
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
    needed: int            # what the list asks for; 0 = in the deck but not listed
    owned: int             # copies of this card anywhere
    in_deck: int           # copies actually in this deck
    available_to_deck: int
    missing: int           # can't be built: needed beyond what's available
    is_leader: bool


class DeckDetail(NamedTuple):
    id: int
    name: str
    leader_id: str | None
    is_physical: bool
    cards: list[DeckCardStatus]
    total_cards: int
    missing_total: int
    in_deck_total: int

    @property
    def is_complete(self) -> bool:
        """Owning enough to build it — the question a wishlist deck asks."""
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

    recipe = conn.execute(
        "SELECT dc.base_card_id, dc.quantity, "
        "       COALESCE((SELECT MIN(c.name) FROM cards c "
        "                 WHERE c.base_card_id = dc.base_card_id), dc.base_card_id) "
        "FROM deck_cards dc WHERE dc.deck_id = ? ORDER BY dc.base_card_id",
        (deck_id,),
    ).fetchall()
    in_deck = _copies_in_deck(conn, deck_id)

    # Copies in the deck that its list doesn't ask for. Since D-041 every
    # change keeps the two equal, so only a database from before can have
    # these; they show with needed = 0 rather than being hidden.
    extras = [
        (base_card_id, 0, conn.execute(
            "SELECT COALESCE(MIN(name), ?) FROM cards WHERE base_card_id = ?",
            (base_card_id, base_card_id),
        ).fetchone()[0])
        for base_card_id in in_deck
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
                in_deck=in_deck.get(base_card_id, 0),
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
        in_deck_total=sum(c.in_deck for c in cards),
    )


class DeckDeletion(NamedTuple):
    name: str
    copies_returned: int


def delete_deck(conn: sqlite3.Connection, ref: str | int) -> DeckDeletion:
    """Delete a deck. The copies in it go back to just-loose; `deck_cards` cascades.

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
    """Turn a physical deck into a wishlist, or an empty wishlist into a physical deck.

    Turning a deck into a wishlist **unmakes** it: its cards return to
    just-loose and its list stays, as the plan. The other way is refused for a
    wishlist with cards on its list: a physical deck is exactly what's in it
    (D-041), so it can't start out empty with a list of cards it doesn't hold.
    Gathering a wishlist's cards into a deck is a screen for the app (PHASE_2
    M3); until then, build it with `new_deck` and `add_to_deck`.
    """
    deck_id = resolve_deck(conn, ref)
    if physical:
        listed = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM deck_cards WHERE deck_id = ?", (deck_id,)
        ).fetchone()[0]
        is_physical = conn.execute(
            "SELECT is_physical FROM decks WHERE id = ?", (deck_id,)).fetchone()[0]
        if listed and not is_physical:
            raise DeckError(
                f"a wishlist with {listed} cards on its list can't become a physical deck "
                f"holding none of them. Build the real deck with `deck new` and `deck add`, "
                f"or import its list as a physical deck if the cards are new to you"
            )
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
    return []


# --------------------------------------------------------------------------
# editing a deck (D-041)
# --------------------------------------------------------------------------

def new_deck(conn: sqlite3.Connection, name: str, is_physical: bool = True) -> int:
    """An empty deck to build up with `add_to_deck`. Returns its id."""
    clean = name.strip()
    if not clean:
        raise DeckError("deck name cannot be empty")
    existing = find_deck(conn, clean)
    if existing is not None:
        raise DeckError(f"a deck named {clean!r} already exists (id {existing})")
    cur = conn.execute("INSERT INTO decks (name, is_physical) VALUES (?, ?)",
                       (clean, 1 if is_physical else 0))
    conn.commit()
    return cur.lastrowid


def _is_physical(conn: sqlite3.Connection, deck_id: int) -> bool:
    return bool(conn.execute("SELECT is_physical FROM decks WHERE id = ?", (deck_id,)).fetchone()[0])


def _listed(conn: sqlite3.Connection, deck_id: int, base_card_id: str) -> int:
    row = conn.execute("SELECT quantity FROM deck_cards WHERE deck_id = ? AND base_card_id = ?",
                       (deck_id, base_card_id)).fetchone()
    return row[0] if row else 0


def add_to_deck(
    conn: sqlite3.Connection,
    ref: str | int,
    base_card_id: str,
    quantity: int,
    frm: Container | None = None,
    art: str | None = None,
) -> int:
    """Put cards into a deck. Returns how many of the card the deck now runs.

    A physical deck takes real copies, from just-loose unless another place is
    named, and its list grows with them. A wishlist only changes its list.
    """
    if quantity <= 0:
        raise DeckError("quantity must be a positive number of copies")
    deck_id = resolve_deck(conn, ref)
    if _is_physical(conn, deck_id):
        move(conn, base_card_id, quantity, frm or loose_container(conn),
             deck_container(conn, deck_id), art)  # the list follows the copies
    else:
        if frm is not None:
            raise DeckError("a wishlist deck holds no cards - leave out --from to add to its list")
        adjust_recipe(conn, deck_id, base_card_id, quantity)
        conn.commit()
    return _listed(conn, deck_id, base_card_id)


def remove_from_deck(
    conn: sqlite3.Connection,
    ref: str | int,
    base_card_id: str,
    quantity: int,
    to: Container | None = None,
    art: str | None = None,
) -> int:
    """Take cards out of a deck. Returns how many of the card the deck still runs.

    From a physical deck the copies go to just-loose unless another place is
    named - you still own them - and the list shrinks with them. With no art
    named, unsorted copies leave first. A wishlist only changes its list.
    """
    if quantity <= 0:
        raise DeckError("quantity must be a positive number of copies")
    deck_id = resolve_deck(conn, ref)
    name = conn.execute("SELECT name FROM decks WHERE id = ?", (deck_id,)).fetchone()[0]
    listed = _listed(conn, deck_id, base_card_id)
    if listed < quantity:
        raise DeckError(f"{name!r} runs {listed} {base_card_id}, not {quantity}")

    if not _is_physical(conn, deck_id):
        if to is not None:
            raise DeckError("a wishlist deck holds no cards - leave out --to")
        adjust_recipe(conn, deck_id, base_card_id, -quantity)
        conn.commit()
        return _listed(conn, deck_id, base_card_id)

    destination = to or loose_container(conn)
    container = deck_container(conn, deck_id)
    if art is not None:
        # One named art: `move` checks it's really there, and both lists follow.
        move(conn, base_card_id, quantity, container, destination, art)
        return _listed(conn, deck_id, base_card_id)

    if destination.deck_id is not None and not _is_physical(conn, destination.deck_id):
        raise DeckError(f"{destination.name!r} is a wishlist deck - it holds no cards")
    try:
        for piece_art, piece in take_any(conn, base_card_id, quantity, container):
            deposit(conn, base_card_id, piece, destination, piece_art)
        adjust_recipe(conn, deck_id, base_card_id, -quantity)
        if destination.deck_id is not None:
            adjust_recipe(conn, destination.deck_id, base_card_id, quantity)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _listed(conn, deck_id, base_card_id)
