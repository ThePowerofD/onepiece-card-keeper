"""Set rotation blocks (D-027).

Block is printed on every card but absent from OptcgAPI - all 19 fields it
serves were checked and none carries it - so it cannot be synced. It is a
property of a *set*, so one hand-maintained mapping derives it for every card
in that set, the same pattern as SEED_TYPES in known_types.py.

`SET_BLOCKS` is deliberately empty, now for one reason rather than two:

1. A trusted set -> block mapping. Guessing it yields confidently wrong
   legality answers, which is worse than none.
2. ~~Consistent set codes.~~ Cleared by D-028. Keys are `game_set` values
   derived from `base_card_id` - 53 stable prefixes ('OP05', 'ST30', 'EB04',
   'P') - not the 68 product spellings the API serves in `set_code`.

Until then `rotation_block` stays NULL and `is_rotation_legal` returns None,
which reads as "unknown" rather than "illegal".
"""

from __future__ import annotations

# game_set -> block number (1-5). Empty until a trusted mapping exists.
SET_BLOCKS: dict[str, int] = {}

# Block 1 has rotated out; anything above it is currently legal.
ROTATION_LEGAL_FROM = 2


def block_for(game_set: str | None) -> int | None:
    """The rotation block a set belongs to, or None if unmapped.

    Keyed on `game_set` (D-028) - 'OP05', derived from base_card_id - not on
    the API's `set_code`, which names the product a printing came in. A reprint
    of an OP05 card inside the PRB-01 booster rotates with OP05, not with the
    booster that reprinted it.
    """
    if not game_set:
        return None
    return SET_BLOCKS.get(game_set)


def is_rotation_legal(block: int | None) -> bool | None:
    """True/False for a known block, None when the block isn't known.

    Tri-state on purpose: with SET_BLOCKS empty, returning False would mark the
    entire collection illegal.
    """
    if block is None:
        return None
    return block >= ROTATION_LEGAL_FROM
