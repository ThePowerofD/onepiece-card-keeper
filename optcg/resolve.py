"""Turn what a person or a decklist names into gameplay ids.

Decks name cards at gameplay level (`base_card_id`), and since D-030 so does
ownership, so importing no longer has to pick a printing — `default_printing`
and `resolve_base_id` went with D-016. Import needs one check: which ids
aren't real cards.

People also name cards by name (D-037). A name is often shared by several
different cards — 366 names are, and 'luffy' matches 91 — so lookup returns
every match and leaves choosing to the caller, which can ask.

Listing a card's printings lives in `collection.printings_of`, next to the
ownership it's reported with.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from typing import NamedTuple


class CardMatch(NamedTuple):
    base_card_id: str
    name: str             # the base printing's, without the API's '(060)' tag
    category: str
    color: str | None
    cost: int | None
    owned: int


# The API tags many names with their number or id - 'Vinsmoke Ichiji (060)',
# 'Monkey.D.Luffy (ST31-004)' - which the id beside it already says.
_NUMBER_TAG = re.compile(r"\s*\((\d{3}|[A-Z]+\d*-\d{3})\)$")


def _words(text: str) -> str:
    """Lowercase, dots and dashes as spaces: 'Monkey.D.Luffy' -> 'monkey d luffy'."""
    return " ".join(re.sub(r"[.\-_/]", " ", text.lower()).split())


def exact_card(conn: sqlite3.Connection, text: str) -> str | None:
    """The gameplay id `text` names exactly, in any capitalisation, or None."""
    row = conn.execute(
        "SELECT base_card_id FROM cards WHERE base_card_id = ? LIMIT 1",
        (text.strip().upper(),),
    ).fetchone()
    return row[0] if row else None


def find_cards(conn: sqlite3.Connection, text: str) -> list[CardMatch]:
    """Every card whose name contains all the words of `text`, by id.

    Any printing's name counts - 'jolly roger' finds the card whose foil is
    called that - but each card appears once, described by its base printing.
    """
    query = _words(text).split()
    if not query:
        return []

    matched: set[str] = set()
    for base_card_id, name in conn.execute("SELECT base_card_id, name FROM cards"):
        words = _words(name)
        if all(q in words for q in query):
            matched.add(base_card_id)
    return card_matches(conn, matched)


def card_matches(conn: sqlite3.Connection, ids: set[str]) -> list[CardMatch]:
    """Describe these gameplay ids, sorted by id. Ids not in the catalog are left out."""
    if not ids:
        return []
    matched = set(ids)
    owned = dict(conn.execute(
        "SELECT base_card_id, SUM(quantity) FROM copies GROUP BY base_card_id"
    ))
    details: dict[str, tuple] = {}
    # Base printings sort last, so their details are the ones that stick.
    for row in conn.execute(
        "SELECT base_card_id, name, category, color, cost FROM cards "
        "ORDER BY (card_image_id = base_card_id)"
    ):
        if row[0] in matched:
            details[row[0]] = row[1:]
    return [
        CardMatch(base, _NUMBER_TAG.sub("", name), category, color, cost, owned.get(base, 0))
        for base in sorted(matched & set(details))
        for name, category, color, cost in [details[base]]
    ]


def corrected_query(conn: sqlite3.Connection, text: str) -> str | None:
    """`text` with each misspelt word replaced by the closest word in a card
    name - 'ichjii' -> 'ichiji' - or None if no correction finds any card.

    Words are corrected one at a time rather than whole names compared:
    'ichjii' looks nothing like 'vinsmoke ichiji', but a lot like 'ichiji'.
    """
    vocabulary = {
        word
        for (name,) in conn.execute("SELECT DISTINCT name FROM cards")
        for word in _words(name).split()
    }
    fixed = []
    for word in _words(text).split():
        if word in vocabulary:
            fixed.append(word)
            continue
        close = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.75)
        if not close:
            return None
        fixed.append(close[0])
    suggestion = " ".join(fixed)
    if suggestion == _words(text) or not find_cards(conn, suggestion):
        return None
    return suggestion


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
