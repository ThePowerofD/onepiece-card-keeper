"""Parse Limitless TCG decklist paste format.

Pure functions — no DB access, no side effects, never raises on bad input.
Malformed lines become warnings so a decklist with one typo still imports
(D-006 style: warn, don't block).

Input looks like:

    Leader: 1 Monkey D. Luffy OP05-001
    DON!!: x10
    Character:
    4 Monkey D. Luffy OP05-097
    3 Roronoa Zoro OP05-020
    Event:
    2 Gum-Gum Giant OP05-060

Section headers may carry a card on the same line ("Leader: 1 ... OP05-001") or
stand alone with cards on following lines.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from src.sanitize import strip_printing_suffix

# Headers that introduce a section. DON!! is recognised only so it can be
# discarded — DON cards are out of scope entirely (D-002).
SECTION_HEADERS = {"leader", "don!!", "don", "character", "event", "stage"}
DON_HEADERS = {"don!!", "don"}

# "4", "x4" and "4x" all mean four.
QUANTITY_RE = re.compile(r"^(?:x(\d+)|(\d+)x?)$", re.IGNORECASE)

# OP05-097, ST01-001, P-029, PRB01-001, optionally with a printing suffix.
CARD_ID_RE = re.compile(r"^[A-Za-z]+\d*-\d+(?:[_-][A-Za-z]+\d+)?$")


class DeckCard(NamedTuple):
    base_card_id: str
    quantity: int
    name: str


class ParsedDeck(NamedTuple):
    leader_id: str | None
    cards: list[DeckCard]
    warnings: list[str]


def parse_quantity(token: str) -> int | None:
    """'4', 'x4', '4x' -> 4. Anything else -> None."""
    m = QUANTITY_RE.match(token.strip())
    if not m:
        return None
    value = int(m.group(1) or m.group(2))
    return value if value > 0 else None


def split_header(line: str) -> tuple[str | None, str]:
    """Split 'Leader: 1 Luffy OP05-001' into ('leader', '1 Luffy OP05-001').

    Returns (None, line) when the line carries no recognised section header.
    """
    if ":" not in line:
        return None, line
    head, _, rest = line.partition(":")
    key = head.strip().lower()
    if key in SECTION_HEADERS:
        return key, rest.strip()
    return None, line


def parse_card_line(line: str) -> tuple[DeckCard | None, str | None]:
    """Parse '4 Monkey D. Luffy OP05-097' into a DeckCard.

    Returns (card, None) on success or (None, reason) on failure. The card id is
    the last token, and is reduced to its gameplay identity — a pasted printing
    like 'OP05-097_p1' becomes 'OP05-097', because decks are gameplay-level (D-003).
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None, "expected '<quantity> <name> <card id>'"

    quantity = parse_quantity(tokens[0])
    if quantity is None:
        return None, f"unreadable quantity {tokens[0]!r}"

    raw_id = tokens[-1]
    if not CARD_ID_RE.match(raw_id):
        return None, f"unreadable card id {raw_id!r}"

    base_card_id = strip_printing_suffix(raw_id)
    name = " ".join(tokens[1:-1]).strip()
    return DeckCard(base_card_id, quantity, name), None


def parse_decklist(text: str) -> ParsedDeck:
    """Parse a full decklist. Never raises.

    The leader is returned separately and is NOT included in `cards`.
    Duplicate ids across lines are summed into one entry.
    """
    leader_id: str | None = None
    order: list[str] = []
    totals: dict[str, DeckCard] = {}
    warnings: list[str] = []
    section: str | None = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        header, remainder = split_header(line)

        if header is not None:
            if header in DON_HEADERS:
                # 'DON!!: x10' discards only itself; a bare 'DON!!:' header
                # discards until the next section. Never leave `section` stuck
                # on DON, or a list without a 'Character:' header loses cards.
                if not remainder:
                    section = header
                continue
            section = header
            if not remainder:
                continue  # standalone header, cards follow
            body = remainder
        else:
            if section in DON_HEADERS:
                continue
            body = line

        card, reason = parse_card_line(body)
        if card is None:
            warnings.append(f"line {lineno}: {reason} - {line!r}")
            continue

        if section == "leader":
            if leader_id is None:
                leader_id = card.base_card_id
            else:
                warnings.append(f"line {lineno}: extra leader {card.base_card_id} ignored")
            # A leader section holds exactly one card. Clear it, or the next
            # bare line gets misread as a second leader and dropped.
            section = None
            continue

        existing = totals.get(card.base_card_id)
        if existing is None:
            order.append(card.base_card_id)
            totals[card.base_card_id] = card
        else:
            totals[card.base_card_id] = existing._replace(
                quantity=existing.quantity + card.quantity
            )

    if leader_id is None:
        warnings.append("no leader found in decklist")

    return ParsedDeck(leader_id, [totals[cid] for cid in order], warnings)
