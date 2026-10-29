"""Containers — the places copies live (D-025, D-030, D-040).

A copy is in exactly one container: a storage place (a binder, a bulk box, the
loose pile, a friend on loan, ...) or a physical deck. Decks keep their own
table because a deck has a leader and a recipe that no binder has, but as a
*container* they behave identically, so `resolve_container` returns either.

What a place can be is its **type**, a list the owner adds to (D-040). In the
schema the column is still `kind`; only the word people see changed.

`just-loose` is the built-in loose pile: where copies go when no container is
named, and where they come from when none is named. It can't be renamed or
deleted, because every operation with no container falls back to it.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from typing import NamedTuple

from optcg.db_setup import BUILTIN_STORAGE_TYPES, DEFAULT_LOOSE_LOCATION


class StorageError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class AmbiguousLocation(StorageError):
    """A bare name matched several containers; the caller must choose."""

    def __init__(self, name: str, kinds: list[str]):
        super().__init__(
            f"{name!r} exists as {' and as '.join(kinds)} - say which with --type"
        )
        self.name = name
        self.kinds = kinds


class NoSuchPlace(StorageError):
    """No storage place or deck has this name. Carries close names, for a 'did you mean'."""

    def __init__(self, message: str, name: str, close: list["Container"]):
        super().__init__(message)
        self.name = name
        self.close = close


class UnknownType(StorageError):
    """A storage type that doesn't exist yet. Carries close types."""

    def __init__(self, name: str, close: list[str], known: list[str]):
        hint = f" - did you mean {close[0]!r}?" if close else ""
        super().__init__(
            f"{name!r} isn't a storage type{hint} (types: {', '.join(known)}; "
            f"add one with `storage type add`)"
        )
        self.name = name
        self.close = close


#: Decks are the fifth category (D-025) and live in `decks`, but `--type deck`
#: resolves one as a container wherever a place can be named.
DECK_KIND = "deck"

_TYPE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Container(NamedTuple):
    """Somewhere copies can sit. Exactly one of the two ids is set."""
    location_id: int | None
    deck_id: int | None
    kind: str
    name: str

    @property
    def is_deck(self) -> bool:
        return self.deck_id is not None


class LocationSummary(NamedTuple):
    id: int
    name: str
    kind: str
    notes: str | None
    distinct_cards: int
    total_cards: int


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# types
# --------------------------------------------------------------------------

class TypeSummary(NamedTuple):
    name: str
    builtin: bool
    places: int


def storage_types(conn: sqlite3.Connection) -> list[str]:
    return [name for (name,) in conn.execute("SELECT name FROM storage_types ORDER BY name")]


def type_summaries(conn: sqlite3.Connection) -> list[TypeSummary]:
    return [
        TypeSummary(name, bool(builtin), places)
        for name, builtin, places in conn.execute(
            "SELECT t.name, t.builtin, COUNT(sl.id) FROM storage_types t "
            "LEFT JOIN storage_locations sl ON sl.kind = t.name "
            "GROUP BY t.name ORDER BY t.name"
        )
    ]


def normalize_type(name: str) -> str:
    """'Booster Box' -> 'booster-box': one word, so it can follow --type unquoted."""
    return "-".join(name.strip().lower().split())


def add_type(conn: sqlite3.Connection, name: str) -> str:
    """Add a storage type. Returns the name as stored."""
    clean = normalize_type(name)
    if not _TYPE_NAME.match(clean):
        raise StorageError(
            f"{name!r} can't be a type name - use letters, numbers and dashes, e.g. deck-box"
        )
    if clean == DECK_KIND:
        raise StorageError("'deck' is taken: decks are their own thing (`deck import`)")
    if clean in storage_types(conn):
        raise StorageError(f"{clean!r} is already a storage type")
    conn.execute("INSERT INTO storage_types (name) VALUES (?)", (clean,))
    conn.commit()
    return clean


def _require_kind(conn: sqlite3.Connection, kind: str, allow_deck: bool = False) -> str:
    """The stored form of a type the owner typed, or UnknownType with suggestions."""
    clean = normalize_type(kind)
    if allow_deck and clean == DECK_KIND:
        return clean
    known = storage_types(conn)
    if clean not in known:
        raise UnknownType(clean, difflib.get_close_matches(clean, known, n=1), known)
    return clean


def _location_id(conn: sqlite3.Connection, name: str, kind: str | None = None) -> int:
    container = resolve_container(conn, name, kind, allow_deck=False)
    return container.location_id


def loose_container(conn: sqlite3.Connection) -> Container:
    """The built-in loose pile, created with the schema."""
    row = conn.execute(
        "SELECT id FROM storage_locations WHERE name = ? AND kind = 'loose'",
        (DEFAULT_LOOSE_LOCATION,),
    ).fetchone()
    if row is None:
        raise StorageError(
            f"the built-in {DEFAULT_LOOSE_LOCATION!r} location is missing - "
            f"run `optcg setup` to recreate it"
        )
    return Container(row[0], None, "loose", DEFAULT_LOOSE_LOCATION)


def deck_container(conn: sqlite3.Connection, deck_id: int) -> Container:
    row = conn.execute("SELECT name FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise StorageError(f"no deck with id {deck_id}")
    return Container(None, deck_id, DECK_KIND, row[0])


def resolve_container(
    conn: sqlite3.Connection,
    name: str | None = None,
    kind: str | None = None,
    allow_deck: bool = True,
) -> Container:
    """Find a container by name. No name means the built-in loose pile.

    A bare name may match a location *and* a deck, or two locations of
    different kinds. Rather than guess, that raises and asks for `--kind` —
    the same refusal-to-guess as D-017.
    """
    if name is None or not str(name).strip():
        return loose_container(conn)
    clean = str(name).strip()

    if kind is not None:
        kind = _require_kind(conn, kind, allow_deck)
        if kind == DECK_KIND:
            rows = conn.execute("SELECT id FROM decks WHERE name = ?", (clean,)).fetchall()
            if not rows:
                raise NoSuchPlace(f"no deck named {clean!r}", clean,
                                  _close_places(conn, clean, allow_deck))
            if len(rows) > 1:
                ids = ", ".join(str(r[0]) for r in rows)
                raise StorageError(
                    f"{len(rows)} decks are named {clean!r} - use an id instead ({ids})"
                )
            return Container(None, rows[0][0], DECK_KIND, clean)
        row = conn.execute(
            "SELECT id FROM storage_locations WHERE name = ? AND kind = ?", (clean, kind)
        ).fetchone()
        if row is None:
            raise NoSuchPlace(f"no {kind} named {clean!r} - create it with `storage add`",
                              clean, _close_places(conn, clean, allow_deck))
        return Container(row[0], None, kind, clean)

    matches = [
        Container(loc_id, None, loc_kind, clean)
        for loc_id, loc_kind in conn.execute(
            "SELECT id, kind FROM storage_locations WHERE name = ? ORDER BY kind", (clean,)
        )
    ]
    if allow_deck:
        matches += [
            Container(None, deck_id, DECK_KIND, clean)
            for (deck_id,) in conn.execute(
                "SELECT id FROM decks WHERE name = ? ORDER BY id", (clean,)
            )
        ]

    if not matches:
        what = "storage place or deck" if allow_deck else "storage place"
        raise NoSuchPlace(f"no {what} named {clean!r} - create it with `storage add`",
                          clean, _close_places(conn, clean, allow_deck))
    if len(matches) > 1:
        raise AmbiguousLocation(clean, [m.kind for m in matches])
    return matches[0]


def _close_places(conn: sqlite3.Connection, name: str, allow_deck: bool) -> list[Container]:
    """Existing places whose name looks like a typo of `name`, closest first."""
    places = [Container(loc_id, None, kind, loc_name) for loc_id, loc_name, kind in
              conn.execute("SELECT id, name, kind FROM storage_locations")]
    if allow_deck:
        places += [Container(None, deck_id, DECK_KIND, deck_name) for deck_id, deck_name in
                   conn.execute("SELECT id, name FROM decks")]
    by_lower = {}
    for p in places:
        by_lower.setdefault(p.name.lower(), []).append(p)
    close = difflib.get_close_matches(name.lower(), list(by_lower), n=3, cutoff=0.6)
    return [p for c in close for p in by_lower[c]]


# --------------------------------------------------------------------------
# locations
# --------------------------------------------------------------------------

def _refuse_if_builtin(name: str, verb: str) -> None:
    if name.strip() == DEFAULT_LOOSE_LOCATION:
        raise StorageError(
            f"{DEFAULT_LOOSE_LOCATION!r} is built in and cannot be {verb} - "
            f"every command with no container named falls back to it"
        )


def add_location(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    notes: str | None = None,
) -> int:
    """Create a storage place of a type that exists (D-040)."""
    clean = name.strip()
    if not clean:
        raise StorageError("a storage place needs a name")
    kind = _require_kind(conn, kind)
    try:
        cur = conn.execute(
            "INSERT INTO storage_locations (name, kind, notes) VALUES (?, ?, ?)",
            (clean, kind, notes),
        )
    except sqlite3.IntegrityError:
        raise StorageError(f"a {kind} named {clean!r} already exists") from None
    conn.commit()
    return cur.lastrowid


def list_locations(conn: sqlite3.Connection, kind: str | None = None) -> list[LocationSummary]:
    sql = [
        "SELECT sl.id, sl.name, sl.kind, sl.notes, ",
        "       COUNT(DISTINCT cp.base_card_id), ",
        "       COALESCE(SUM(cp.quantity), 0) ",
        "FROM storage_locations sl ",
        "LEFT JOIN copies cp ON cp.location_id = sl.id ",
    ]
    params: list = []
    if kind is not None:
        kind = _require_kind(conn, kind)
        sql.append("WHERE sl.kind = ? ")
        params.append(kind)
    sql.append("GROUP BY sl.id, sl.name, sl.kind, sl.notes ORDER BY sl.kind, sl.name")
    return [LocationSummary(*row) for row in conn.execute("".join(sql), params)]


def rename_location(
    conn: sqlite3.Connection, old_name: str, new_name: str, kind: str | None = None
) -> None:
    clean = new_name.strip()
    if not clean:
        raise StorageError("location name cannot be empty")
    _refuse_if_builtin(old_name, "renamed")
    _refuse_if_builtin(clean, "used as a new name")
    loc_id = _location_id(conn, old_name, kind)
    try:
        conn.execute("UPDATE storage_locations SET name = ? WHERE id = ?", (clean, loc_id))
    except sqlite3.IntegrityError:
        raise StorageError(f"a location named {clean!r} already exists") from None
    conn.commit()


def delete_location(conn: sqlite3.Connection, name: str, kind: str | None = None) -> int:
    """Delete a location. Its copies move to just-loose; you still own them.

    Returns how many copies were moved. The foreign key has no cascade for
    exactly this reason — deleting a binder must never destroy the cards in it.
    Snapshots the database first (D-032).
    """
    _refuse_if_builtin(name, "deleted")
    container = resolve_container(conn, name, kind, allow_deck=False)
    # Imported here, not at module scope: collection and backup both import
    # this module, so a top-level import would be circular.
    from optcg.backup import auto_snapshot
    from optcg.collection import empty_container

    auto_snapshot(conn, "before-location-delete")
    try:
        moved = empty_container(conn, container)
        conn.execute("DELETE FROM storage_locations WHERE id = ?", (container.location_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return moved


# --------------------------------------------------------------------------
# contents
# --------------------------------------------------------------------------

def contents(
    conn: sqlite3.Connection,
    location: str | None = None,
    limit: int | None = None,
    kind: str | None = None,
) -> list[tuple]:
    """What's in a container: (base_card_id, card_image_id, name, quantity).

    `card_image_id` is None for copies whose art hasn't been sorted yet.
    """
    container = resolve_container(conn, location, kind)
    sql = (
        "SELECT cp.base_card_id, cp.card_image_id, "
        "       COALESCE(c.name, (SELECT MIN(c2.name) FROM cards c2 "
        "                         WHERE c2.base_card_id = cp.base_card_id)), "
        "       cp.quantity "
        "FROM copies cp LEFT JOIN cards c ON c.card_image_id = cp.card_image_id "
        "WHERE cp.location_id IS ? AND cp.deck_id IS ? "
        "ORDER BY cp.base_card_id, (cp.card_image_id IS NOT NULL), cp.card_image_id"
    )
    params: list = [container.location_id, container.deck_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
