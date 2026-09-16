"""Tests for gameplay-field inheritance and manual overrides (D-031).

The same card in a different art is the same card, so its gameplay fields must
agree across every printing. The API often disagrees with itself: 748 variants
differed from their base printing before this pass, 65 of them on counter.

Run:
    python -m pytest tests/test_inheritance.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.db_setup import create_schema
from src.overrides import apply_overrides
from src.sync import inherit_from_base_printing


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add(conn, card_image_id, base_card_id, **fields):
    fields.setdefault("name", "Card")
    fields.setdefault("category", "Character")
    columns = ", ".join(["card_image_id", "base_card_id", *fields])
    marks = ", ".join("?" * (2 + len(fields)))
    conn.execute(
        f"INSERT INTO cards ({columns}) VALUES ({marks})",
        (card_image_id, base_card_id, *fields.values()),
    )
    conn.commit()


def counters(conn, base_card_id):
    return dict(conn.execute(
        "SELECT card_image_id, counter FROM cards WHERE base_card_id = ? "
        "ORDER BY card_image_id", (base_card_id,)
    ))


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_the_base_printing_wins_a_genuine_conflict(self):
        """Nami: base and three printings say 2000, the SP says 1000."""
        add(self.conn, "OP01-016", "OP01-016", counter=2000)
        add(self.conn, "OP01-016_p2", "OP01-016", counter=1000)
        inherit_from_base_printing(self.conn)
        self.assertEqual(counters(self.conn, "OP01-016"),
                         {"OP01-016": 2000, "OP01-016_p2": 2000})

    def test_an_empty_base_takes_the_variants_real_value(self):
        """P-055: base reads counter 0, five printings read 1000."""
        add(self.conn, "P-055", "P-055", counter=0)
        add(self.conn, "P-055_p2", "P-055", counter=1000)
        add(self.conn, "P-055_r1", "P-055", counter=1000)
        inherit_from_base_printing(self.conn)
        self.assertEqual(set(counters(self.conn, "P-055").values()), {1000})

    def test_a_real_base_beats_an_empty_variant(self):
        """OP01-041: base 1000, three variants 0. Majority would pick 0."""
        add(self.conn, "OP01-041", "OP01-041", counter=1000)
        for v in ("p5", "p7", "r1"):
            add(self.conn, f"OP01-041_{v}", "OP01-041", counter=0)
        inherit_from_base_printing(self.conn)
        self.assertEqual(set(counters(self.conn, "OP01-041").values()), {1000})

    def test_zero_counts_as_empty_only_for_counter(self):
        """A cost or power of 0 is a real value and must survive."""
        add(self.conn, "X-001", "X-001", cost=0, power=0)
        add(self.conn, "X-001_p1", "X-001", cost=3, power=5000)
        inherit_from_base_printing(self.conn)
        row = self.conn.execute(
            "SELECT cost, power FROM cards WHERE card_image_id = 'X-001_p1'"
        ).fetchone()
        self.assertEqual(row, (0, 0))

    def test_errata_text_propagates_to_every_printing(self):
        """The API marks errata on some printings only; all of them are current."""
        add(self.conn, "OP01-016", "OP01-016",
            effect="Look at 5 cards.  This card has been officially errata'd.")
        add(self.conn, "OP01-016_p2", "OP01-016", effect="Look at 5 cards.")
        inherit_from_base_printing(self.conn)
        effects = [r[0] for r in self.conn.execute(
            "SELECT effect FROM cards WHERE base_card_id = 'OP01-016'")]
        self.assertTrue(all("errata" in e for e in effects))

    def test_keyword_booleans_follow_the_effect(self):
        add(self.conn, "X-001", "X-001", effect="[Blocker] text", has_blocker=1)
        add(self.conn, "X-001_p1", "X-001", effect="text", has_blocker=0)
        inherit_from_base_printing(self.conn)
        flags = [r[0] for r in self.conn.execute(
            "SELECT has_blocker FROM cards WHERE base_card_id = 'X-001'")]
        self.assertEqual(flags, [1, 1])

    def test_per_printing_fields_are_left_alone(self):
        """Name, rarity, set_code and image genuinely differ between printings."""
        add(self.conn, "X-001", "X-001", name="Nami", rarity="R", set_code="OP-01")
        add(self.conn, "X-001_p1", "X-001", name="Nami (Parallel)", rarity="PR",
            set_code="PRB-01")
        inherit_from_base_printing(self.conn)
        row = self.conn.execute(
            "SELECT name, rarity, set_code FROM cards WHERE card_image_id = 'X-001_p1'"
        ).fetchone()
        self.assertEqual(row, ("Nami (Parallel)", "PR", "PRB-01"))

    def test_a_card_with_one_printing_is_untouched(self):
        add(self.conn, "X-001", "X-001", counter=1000)
        stats = inherit_from_base_printing(self.conn)
        self.assertEqual(stats["cards_reconciled"], 0)

    def test_already_consistent_cards_are_not_counted(self):
        add(self.conn, "X-001", "X-001", counter=1000)
        add(self.conn, "X-001_p1", "X-001", counter=1000)
        stats = inherit_from_base_printing(self.conn)
        self.assertEqual(stats["cards_reconciled"], 0)
        self.assertEqual(stats["fields_rewritten"], 0)

    def test_it_is_idempotent(self):
        add(self.conn, "X-001", "X-001", counter=2000)
        add(self.conn, "X-001_p1", "X-001", counter=1000)
        inherit_from_base_printing(self.conn)
        again = inherit_from_base_printing(self.conn)
        self.assertEqual(again["fields_rewritten"], 0)

    def test_implausible_counters_are_reported_not_rewritten(self):
        """Every counter above 2000 in the feed equals the card's power."""
        add(self.conn, "OP06-051", "OP06-051", counter=4000, power=4000)
        stats = inherit_from_base_printing(self.conn)
        self.assertEqual(stats["implausible_counters"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT counter FROM cards WHERE card_image_id = 'OP06-051'"
            ).fetchone()[0],
            4000,
        )


class ErrataTripwireTests(unittest.TestCase):
    """The one shape in which base-wins would be backwards (D-031)."""

    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_silent_when_the_base_carries_the_errata(self):
        add(self.conn, "OP01-016", "OP01-016", effect="text errata'd")
        add(self.conn, "OP01-016_p2", "OP01-016", effect="text")
        stats = inherit_from_base_printing(self.conn)
        self.assertEqual(stats["errata_outranks_base"], 0)

    def test_flags_a_variant_errata_against_an_un_errata_d_base(self):
        add(self.conn, "X-001", "X-001", effect="old text")
        add(self.conn, "X-001_p1", "X-001",
            effect="new text  This card has been officially errata'd.")
        stats = inherit_from_base_printing(self.conn)
        self.assertEqual(stats["errata_outranks_base"], 1)

    def test_silent_when_neither_has_it(self):
        add(self.conn, "X-001", "X-001", effect="text")
        add(self.conn, "X-001_p1", "X-001", effect="other")
        self.assertEqual(
            inherit_from_base_printing(self.conn)["errata_outranks_base"], 0
        )


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_applies_to_every_printing_of_the_card(self):
        add(self.conn, "OP08-001", "OP08-001", category="Leader", life=1)
        add(self.conn, "OP08-001_p1", "OP08-001", category="Leader", life=1)
        changed = apply_overrides(self.conn, {"OP08-001": {"life": 4}})
        self.assertEqual(changed, 2)
        lives = {r[0] for r in self.conn.execute(
            "SELECT life FROM cards WHERE base_card_id = 'OP08-001'")}
        self.assertEqual(lives, {4})

    def test_several_fields_at_once(self):
        add(self.conn, "X-001", "X-001", counter=9999, power=1)
        apply_overrides(self.conn, {"X-001": {"counter": 1000, "power": 5000}})
        self.assertEqual(
            self.conn.execute(
                "SELECT counter, power FROM cards WHERE card_image_id = 'X-001'"
            ).fetchone(),
            (1000, 5000),
        )

    def test_an_unknown_card_changes_nothing(self):
        self.assertEqual(apply_overrides(self.conn, {"ZZ99-999": {"life": 4}}), 0)

    def test_an_empty_table_is_a_no_op(self):
        self.assertEqual(apply_overrides(self.conn, {}), 0)

    def test_the_shipped_table_only_names_real_columns(self):
        """A typo'd column name would fail at sync time, not here."""
        from src.overrides import CARD_OVERRIDES

        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(cards)")}
        for base_card_id, fields in CARD_OVERRIDES.items():
            for column in fields:
                self.assertIn(column, columns, msg=f"{base_card_id}.{column}")


if __name__ == "__main__":
    unittest.main()
