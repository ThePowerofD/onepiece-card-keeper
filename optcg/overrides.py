"""Hand-maintained corrections for cards the API gets wrong (D-031).

Applied after sync, after base-printing inheritance, keyed on `base_card_id` so
a correction reaches every printing of the card. Same pattern as `SEED_TYPES` in
known_types.py and `SET_BLOCKS` in rotation.py: a small dict the owner edits,
rather than a rule nothing can derive.

Only put a card here when the value is *wrong*, not merely inconsistent. A
variant disagreeing with its base is handled automatically by inheritance;
this is for when every printing agrees and every printing is wrong.

Keys are column names in `cards`. A re-sync re-applies them, so corrections
survive `--reset`.
"""

from __future__ import annotations

CARD_OVERRIDES: dict[str, dict[str, object]] = {
    # OP08-001 Tony Tony.Chopper - the API reports life 1. Leaders in this game
    # carry 4 or 5, and the row also arrived with a shifted power (repaired by
    # repair_field_shift). Left out until the correct value is confirmed against
    # the physical card; adding a guessed number would be worse than the typo.
    # "OP08-001": {"life": 4},
}


def apply_overrides(conn, overrides: dict[str, dict[str, object]] | None = None) -> int:
    """Write the corrections into `cards`. Returns how many rows changed.

    No commit - the caller owns the transaction.
    """
    table = CARD_OVERRIDES if overrides is None else overrides
    changed = 0
    for base_card_id, fields in table.items():
        if not fields:
            continue
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cur = conn.execute(
            f"UPDATE cards SET {assignments} WHERE base_card_id = ?",
            (*fields.values(), base_card_id),
        )
        changed += cur.rowcount
    return changed
