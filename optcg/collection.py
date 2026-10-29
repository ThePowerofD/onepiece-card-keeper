"""Ownership — the set of copies you hold, and where each one is (D-030).

There is no stored total. Every copy sits in exactly one container (a storage
location or a physical deck) and how many you own is `SUM(quantity)` over those
rows. Keeping a separate count would be a second number that has to agree.

`card_image_id` on a copy names the art. `NULL` means it hasn't been sorted yet
— an honest "four of these, I haven't looked". Setting the art on some copies
splits the row; the total never moves.

Every write is an add at the edge, a move between containers, or a remove. A
move that asks for more than the source holds is an error that writes nothing:
every card is accounted for, so a negative pile is never the right answer.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

from optcg.storage import Container, loose_container


class CollectionError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class NotThere(CollectionError):
    """The container named doesn't hold the copies asked for.

    Carries where the card's copies actually are, so a caller can say which
    container to name instead - the usual cause is forgetting that copies
    imported with a deck sit in the deck, not in just-loose.
    """

    def __init__(self, message: str, elsewhere: list["Holding"]):
        super().__init__(message)
        self.elsewhere = elsewhere


class Holding(NamedTuple):
    """Copies of one card, of one art, in one container."""
    container: str
    kind: str                     # a location kind, or "deck"
    card_image_id: str | None     # None = art not sorted yet
    quantity: int

    @property
    def art(self) -> str:
        return self.card_image_id or "unsorted"


class ArtTotal(NamedTuple):
    card_image_id: str | None     # None = art not sorted yet
    printing_variant: str | None
    name: str | None
    quantity: int


class Printing(NamedTuple):
    card_image_id: str
    printing_variant: str | None
    printing_kind: str | None
    name: str


class CardStatus(NamedTuple):
    base_card_id: str
    name: str
    total_owned: int
    committed: int                # copies sleeved into physical decks
    arts: list[ArtTotal]          # what you own, by art; unsorted first
    holdings: list[Holding]       # what you own, by container
    printings: list[Printing]     # every printing that exists, owned or not

    @property
    def loose(self) -> int:
        """Copies not sleeved into a physical deck."""
        return self.total_owned - self.committed

    @property
    def unsorted(self) -> int:
        return sum(a.quantity for a in self.arts if a.card_image_id is None)


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------

def _require_base_card(conn: sqlite3.Connection, base_card_id: str) -> str:
    """Confirm the gameplay id exists, and return the card's name."""
    row = conn.execute(
        "SELECT MIN(name) FROM cards WHERE base_card_id = ?", (base_card_id,)
    ).fetchone()
    if row is None or row[0] is None:
        raise CollectionError(f"unknown card {base_card_id!r} - not in the card database")
    return row[0]


def resolve_art(conn: sqlite3.Connection, base_card_id: str, art: str | None) -> str | None:
    """Turn an --art argument into a card_image_id, or None for unsorted.

    Accepts a full printing id ('OP05-097_p1'), a bare variant ('p1'), or the
    word 'base' for the variant-free printing. Refuses an art belonging to a
    different card rather than silently recording the wrong printing.
    """
    if art is None:
        return None
    clean = str(art).strip()
    if not clean or clean.lower() in ("unsorted", "none"):
        return None
    candidates = [base_card_id] if clean.lower() == "base" else [clean, f"{base_card_id}_{clean}"]

    for candidate in candidates:
        row = conn.execute(
            "SELECT card_image_id, base_card_id FROM cards WHERE card_image_id = ?",
            (candidate,),
        ).fetchone()
        if row is None:
            continue
        if row[1] != base_card_id:
            raise CollectionError(
                f"{row[0]} is a printing of {row[1]}, not of {base_card_id}"
            )
        return row[0]

    known = [p.printing_variant or "base" for p in printings_of(conn, base_card_id)]
    raise CollectionError(
        f"{base_card_id} has no printing {clean!r} - known arts: {', '.join(known) or 'none'}"
    )


def printings_of(conn: sqlite3.Connection, base_card_id: str) -> list[Printing]:
    """Every printing of a gameplay card, base printing first."""
    return [
        Printing(*row)
        for row in conn.execute(
            "SELECT card_image_id, printing_variant, printing_kind, name FROM cards "
            "WHERE base_card_id = ? ORDER BY (printing_variant IS NOT NULL), card_image_id",
            (base_card_id,),
        )
    ]


def _slot_quantity(
    conn: sqlite3.Connection, base_card_id: str, art: str | None, container: Container
) -> int:
    row = conn.execute(
        "SELECT quantity FROM copies WHERE base_card_id = ? "
        "AND card_image_id IS ? AND location_id IS ? AND deck_id IS ?",
        (base_card_id, art, container.location_id, container.deck_id),
    ).fetchone()
    return row[0] if row else 0


def _describe_holdings(
    conn: sqlite3.Connection, base_card_id: str, container: Container
) -> str:
    rows = conn.execute(
        "SELECT card_image_id, quantity FROM copies WHERE base_card_id = ? "
        "AND location_id IS ? AND deck_id IS ? ORDER BY (card_image_id IS NOT NULL), card_image_id",
        (base_card_id, container.location_id, container.deck_id),
    ).fetchall()
    if not rows:
        return "nothing"
    return ", ".join(f"{qty} {art or 'unsorted'}" for art, qty in rows)


def unsorted_copies(conn: sqlite3.Connection, base_card_id: str) -> int:
    """Copies of this card whose art hasn't been recorded yet, wherever they are."""
    return conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM copies "
        "WHERE base_card_id = ? AND card_image_id IS NULL",
        (base_card_id,),
    ).fetchone()[0]


def holdings_of(conn: sqlite3.Connection, base_card_id: str) -> list[Holding]:
    """Every container holding copies of this card, art by art. Locations first."""
    return [
        Holding(name, kind, art, quantity)
        for name, kind, art, quantity in conn.execute(
            "SELECT COALESCE(sl.name, d.name), CASE WHEN cp.deck_id IS NULL THEN sl.kind "
            "       ELSE 'deck' END, cp.card_image_id, cp.quantity "
            "FROM copies cp "
            "LEFT JOIN storage_locations sl ON sl.id = cp.location_id "
            "LEFT JOIN decks d ON d.id = cp.deck_id "
            "WHERE cp.base_card_id = ? "
            "ORDER BY (cp.deck_id IS NOT NULL), COALESCE(sl.name, d.name), cp.card_image_id",
            (base_card_id,),
        )
    ]


def _deposit(
    conn: sqlite3.Connection, base_card_id: str, art: str | None,
    container: Container, quantity: int,
) -> None:
    """Add copies to a slot. No commit — callers own the transaction."""
    existing = _slot_quantity(conn, base_card_id, art, container)
    if existing:
        conn.execute(
            "UPDATE copies SET quantity = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE base_card_id = ? AND card_image_id IS ? "
            "AND location_id IS ? AND deck_id IS ?",
            (existing + quantity, base_card_id, art, container.location_id, container.deck_id),
        )
    else:
        conn.execute(
            "INSERT INTO copies (base_card_id, card_image_id, location_id, deck_id, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            (base_card_id, art, container.location_id, container.deck_id, quantity),
        )


def _withdraw(
    conn: sqlite3.Connection, base_card_id: str, art: str | None,
    container: Container, quantity: int,
) -> None:
    """Take copies out of a slot, or refuse. No commit — callers own the transaction.

    Refusing rather than flooring at zero is the point of D-030: if the pile
    doesn't hold what you're taking, the miscount is upstream and writing a
    smaller number would hide it.
    """
    have = _slot_quantity(conn, base_card_id, art, container)
    if have < quantity:
        message = (
            f"{container.name} has {have} {art or 'unsorted'} {base_card_id}, "
            f"not {quantity} (holds: {_describe_holdings(conn, base_card_id, container)})"
        )
        here = (container.name, container.kind)
        elsewhere = [h for h in holdings_of(conn, base_card_id) if (h.container, h.kind) != here]
        if elsewhere:
            places = ", ".join(f"{h.kind} {h.container!r} {h.quantity} {h.art}" for h in elsewhere)
            message += f". Your {base_card_id} copies are in: {places}"
        raise NotThere(message, elsewhere)
    remaining = have - quantity
    if remaining:
        conn.execute(
            "UPDATE copies SET quantity = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE base_card_id = ? AND card_image_id IS ? "
            "AND location_id IS ? AND deck_id IS ?",
            (remaining, base_card_id, art, container.location_id, container.deck_id),
        )
    else:
        conn.execute(
            "DELETE FROM copies WHERE base_card_id = ? AND card_image_id IS ? "
            "AND location_id IS ? AND deck_id IS ?",
            (base_card_id, art, container.location_id, container.deck_id),
        )


def _check_quantity(quantity: int) -> int:
    if quantity <= 0:
        raise CollectionError("quantity must be a positive number of copies")
    return quantity


def deposit(
    conn: sqlite3.Connection,
    base_card_id: str,
    quantity: int,
    container: Container,
    art: str | None = None,
) -> None:
    """Add copies **without committing** — callers own the transaction.

    Deck import needs its copies to land atomically with the deck itself, so
    the commit can't happen in here.
    """
    _check_quantity(quantity)
    _deposit(conn, base_card_id, resolve_art(conn, base_card_id, art), container, quantity)


def empty_container(conn: sqlite3.Connection, container: Container) -> int:
    """Move everything in a container to just-loose. No commit.

    Used when a location or deck is deleted: you still own the cards, so they
    fall back to the loose pile rather than vanishing with their container.
    Returns the number of copies moved.
    """
    destination = loose_container(conn)
    rows = conn.execute(
        "SELECT base_card_id, card_image_id, quantity FROM copies "
        "WHERE location_id IS ? AND deck_id IS ?",
        (container.location_id, container.deck_id),
    ).fetchall()
    moved = 0
    for base_card_id, art, quantity in rows:
        _withdraw(conn, base_card_id, art, container, quantity)
        _deposit(conn, base_card_id, art, destination, quantity)
        moved += quantity
    return moved


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def add(
    conn: sqlite3.Connection,
    base_card_id: str,
    quantity: int,
    art: str | None = None,
    to: Container | None = None,
) -> int:
    """Record copies you've acquired. Returns the new total owned.

    They land in just-loose unless a container is named, and unsorted unless an
    art is given — you can say what you own before you've looked at the art.
    """
    _check_quantity(quantity)
    _require_base_card(conn, base_card_id)
    art_id = resolve_art(conn, base_card_id, art)
    container = to or loose_container(conn)
    _deposit(conn, base_card_id, art_id, container, quantity)
    conn.commit()
    return total_owned(conn, base_card_id)


def remove(
    conn: sqlite3.Connection,
    base_card_id: str,
    quantity: int,
    art: str | None = None,
    frm: Container | None = None,
) -> int:
    """Copies leaving the collection — sold, traded, lost. Returns the new total.

    Taken from just-loose unless a container is named. No history is kept: the
    owner asked for current state only, so a removed copy is simply gone.
    """
    _check_quantity(quantity)
    _require_base_card(conn, base_card_id)
    art_id = resolve_art(conn, base_card_id, art)
    container = frm or loose_container(conn)
    _withdraw(conn, base_card_id, art_id, container, quantity)
    conn.commit()
    return total_owned(conn, base_card_id)


def move(
    conn: sqlite3.Connection,
    base_card_id: str,
    quantity: int,
    frm: Container,
    to: Container,
    art: str | None = None,
) -> int:
    """Move copies between containers. Returns the quantity now at the destination.

    Loaning a card is this: a move into a location of kind 'loan'. The copy is
    still owned, it's just somewhere else.
    """
    _check_quantity(quantity)
    _require_base_card(conn, base_card_id)
    if frm == to:
        raise CollectionError(f"{frm.name} is both the source and the destination")
    art_id = resolve_art(conn, base_card_id, art)
    try:
        _withdraw(conn, base_card_id, art_id, frm, quantity)
        _deposit(conn, base_card_id, art_id, to, quantity)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _slot_quantity(conn, base_card_id, art_id, to)


def set_art(
    conn: sqlite3.Connection,
    base_card_id: str,
    quantity: int,
    art: str,
    container: Container | None = None,
    frm_art: str | None = None,
) -> int:
    """Sort copies: say which printing some of them are. Returns copies still unsorted.

    Splits the slot — the total in the container never changes. Recording art
    where the copies are is the whole point: you decide the art by looking at
    the card, and you look at it where it sits.
    """
    _check_quantity(quantity)
    _require_base_card(conn, base_card_id)
    where = container or loose_container(conn)
    source_art = resolve_art(conn, base_card_id, frm_art)
    target_art = resolve_art(conn, base_card_id, art)
    if target_art is None:
        raise CollectionError("set_art needs a printing - use 'base' for the plain art")
    if target_art == source_art:
        raise CollectionError(f"those copies are already {target_art}")
    try:
        _withdraw(conn, base_card_id, source_art, where, quantity)
        _deposit(conn, base_card_id, target_art, where, quantity)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _slot_quantity(conn, base_card_id, None, where)


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

def total_owned(conn: sqlite3.Connection, base_card_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM copies WHERE base_card_id = ?",
        (base_card_id,),
    ).fetchone()[0]


def committed(conn: sqlite3.Connection, base_card_id: str) -> int:
    """Copies sleeved into physical decks — owned, but not free to use elsewhere."""
    return conn.execute(
        "SELECT COALESCE(SUM(cp.quantity), 0) FROM copies cp "
        "JOIN decks d ON d.id = cp.deck_id "
        "WHERE d.is_physical = 1 AND cp.base_card_id = ?",
        (base_card_id,),
    ).fetchone()[0]


def committed_elsewhere(conn: sqlite3.Connection, base_card_id: str, deck_id: int) -> int:
    """Copies in *other* physical decks. A deck never competes with itself."""
    return conn.execute(
        "SELECT COALESCE(SUM(cp.quantity), 0) FROM copies cp "
        "JOIN decks d ON d.id = cp.deck_id "
        "WHERE d.is_physical = 1 AND d.id != ? AND cp.base_card_id = ?",
        (deck_id, base_card_id),
    ).fetchone()[0]


def show(conn: sqlite3.Connection, base_card_id: str) -> CardStatus | None:
    """Full picture for one gameplay card. None if the id isn't in `cards`."""
    printings = printings_of(conn, base_card_id)
    if not printings:
        return None

    arts = [
        ArtTotal(art, variant, name, quantity)
        for art, variant, name, quantity in conn.execute(
            "SELECT cp.card_image_id, c.printing_variant, c.name, SUM(cp.quantity) "
            "FROM copies cp LEFT JOIN cards c ON c.card_image_id = cp.card_image_id "
            "WHERE cp.base_card_id = ? "
            "GROUP BY cp.card_image_id, c.printing_variant, c.name "
            "ORDER BY (cp.card_image_id IS NOT NULL), cp.card_image_id",
            (base_card_id,),
        )
    ]

    holdings = holdings_of(conn, base_card_id)

    return CardStatus(
        base_card_id=base_card_id,
        name=printings[0].name,
        total_owned=sum(a.quantity for a in arts),
        committed=committed(conn, base_card_id),
        arts=arts,
        holdings=holdings,
        printings=printings,
    )


class OwnedRow(NamedTuple):
    base_card_id: str
    card_image_id: str | None     # None = art not sorted yet
    name: str
    category: str
    color: str | None
    quantity: int


def owned_rows(
    conn: sqlite3.Connection,
    set_code: str | None = None,
    color: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> list[OwnedRow]:
    """What you own: one row per card and art, unsorted copies as their own row.

    The collection view (D-039). Unlike `list_cards` it never shows a printing
    you don't have - browsing the whole game is `list_cards`' job.
    """
    sql = [
        "SELECT cp.base_card_id, cp.card_image_id, COALESCE(p.name, cp.base_card_id),",
        "       COALESCE(p.category, ''), p.color, SUM(cp.quantity)",
        "FROM copies cp",
        # Unsorted copies are described by the card's base printing.
        "LEFT JOIN cards p ON p.card_image_id = COALESCE(cp.card_image_id, cp.base_card_id)",
        "WHERE 1=1",
    ]
    params: list = []
    if set_code:
        sql.append("AND (p.game_set = ? OR p.set_code = ? OR cp.base_card_id LIKE ?)")
        params += [set_code, set_code, f"{set_code}-%"]
    if color:
        sql.append("AND p.color LIKE ?")
        params.append(f"%{color}%")
    if category:
        sql.append("AND p.category = ?")
        params.append(category)
    if q:
        # The same matching as `find`: words in any order, dots ignored.
        from optcg.resolve import find_cards

        ids = [m.base_card_id for m in find_cards(conn, q)]
        if not ids:
            return []
        sql.append(f"AND cp.base_card_id IN ({','.join('?' * len(ids))})")
        params += ids
    sql.append("GROUP BY cp.base_card_id, cp.card_image_id "
               "ORDER BY cp.base_card_id, (cp.card_image_id IS NOT NULL), cp.card_image_id")
    return [OwnedRow(*row) for row in conn.execute(" ".join(sql), params)]


def list_cards(
    conn: sqlite3.Connection,
    set_code: str | None = None,
    color: str | None = None,
    category: str | None = None,
    owned_only: bool = False,
    limit: int | None = None,
    q: str | None = None,
    offset: int | None = None,
) -> list[tuple]:
    """Browse printings.

    Returns (card_image_id, name, category, color, owned, card_owned) where
    `owned` counts copies recorded as *that* art and `card_owned` counts every
    copy of the gameplay card, sorted or not. They differ whenever the art
    hasn't been recorded, which is a normal state, not a gap.
    """
    sql = [
        "SELECT c.card_image_id, c.name, c.category, c.color,",
        "       COALESCE((SELECT SUM(cp.quantity) FROM copies cp",
        "                 WHERE cp.card_image_id = c.card_image_id), 0) AS owned,",
        "       COALESCE((SELECT SUM(cp2.quantity) FROM copies cp2",
        "                 WHERE cp2.base_card_id = c.base_card_id), 0) AS card_owned",
        "FROM cards c WHERE 1=1",
    ]
    params: list = []
    if set_code:
        sql.append("AND (c.game_set = ? OR c.set_code = ? OR c.card_image_id LIKE ?)")
        params += [set_code, set_code, f"{set_code}-%"]
    if color:
        sql.append("AND c.color LIKE ?")
        params.append(f"%{color}%")
    if category:
        sql.append("AND c.category = ?")
        params.append(category)
    if q:
        sql.append("AND c.name LIKE ?")
        params.append(f"%{q}%")
    if owned_only:
        sql.append("AND card_owned > 0")
    sql.append("ORDER BY c.card_image_id")
    if limit:
        sql.append("LIMIT ?")
        params.append(limit)
        if offset:
            sql.append("OFFSET ?")
            params.append(offset)

    return conn.execute(" ".join(sql), params).fetchall()
