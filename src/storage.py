"""Containers — the places copies live (D-025, D-030).

A copy is in exactly one container: a storage location (storage / binder /
loose / loan) or a physical deck. Decks keep their own table because a deck has
a leader and a recipe that no binder has, but as a *container* they behave
identically, so `resolve_container` returns either.

`just-loose` is the built-in loose pile: where copies go when no container is
named, and where they come from when none is named. It can't be renamed or
deleted, because every operation with no container falls back to it.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

from src.db_setup import DEFAULT_LOOSE_LOCATION


class StorageError(Exception):
    """A requested change was refused. Carries a message fit to print."""


class AmbiguousLocation(StorageError):
    """A bare name matched several containers; the caller must choose."""

    def __init__(self, name: str, kinds: list[str]):
        super().__init__(
            f"{name!r} exists as {' and as '.join(kinds)} - say which with --kind"
        )
        self.name = name
        self.kinds = kinds


#: The four storage classes. Decks are the fifth category (D-025) and live in
#: `decks`, but `--kind deck` resolves one as a container.
LOCATION_KINDS = ("storage", "binder", "loose", "loan")
DECK_KIND = "deck"
CONTAINER_KINDS = LOCATION_KINDS + (DECK_KIND,)


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

def _require_kind(kind: str, allow_deck: bool = False) -> str:
    allowed = CONTAINER_KINDS if allow_deck else LOCATION_KINDS
    if kind not in allowed:
        raise StorageError(f"unknown kind {kind!r} - one of {', '.join(allowed)}")
    return kind


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
            f"run `python -m src.db_setup` to recreate it"
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
        _require_kind(kind, allow_deck)
        if kind == DECK_KIND:
            rows = conn.execute("SELECT id FROM decks WHERE name = ?", (clean,)).fetchall()
            if not rows:
                raise StorageError(f"no deck named {clean!r}")
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
            raise StorageError(f"no {kind} named {clean!r} - create it with `location add`")
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
        raise StorageError(
            f"no location or deck named {clean!r} - create it with `location add`"
            if allow_deck
            else f"no storage location named {clean!r} - create it with `location add`"
        )
    if len(matches) > 1:
        raise AmbiguousLocation(clean, [m.kind for m in matches])
    return matches[0]


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
    kind: str = "storage",
    notes: str | None = None,
) -> int:
    clean = name.strip()
    if not clean:
        raise StorageError("location name cannot be empty")
    _require_kind(kind)
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
        _require_kind(kind)
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
    from src.backup import auto_snapshot
    from src.collection import empty_container

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
