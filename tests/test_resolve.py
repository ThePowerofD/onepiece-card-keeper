"""Unit tests for src/resolve.py.

Run:
    python -m pytest tests/test_resolve.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.db_setup import create_schema
from src.resolve import default_printing, resolve_base_id, unknown_ids


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add(conn, card_image_id, base_card_id, variant=None, name="Card", rarity="C"):
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, "
        "category, rarity) VALUES (?, ?, ?, ?, 'Character', ?)",
        (card_image_id, base_card_id, variant, name, rarity),
    )


class ResolveBaseIdTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_single_printing(self):
        add(self.conn, "OP05-020", "OP05-020", None, "Zoro")
        printings = resolve_base_id(self.conn, "OP05-020")
        self.assertEqual(len(printings), 1)
        self.assertEqual(printings[0].card_image_id, "OP05-020")
        self.assertIsNone(printings[0].printing_variant)

    def test_many_printings_base_first(self):
        # P-029 really has 7 printings in the live data
        add(self.conn, "P-029_r2", "P-029", "r2")
        add(self.conn, "P-029_p3", "P-029", "p3")
        add(self.conn, "P-029", "P-029", None)
        add(self.conn, "P-029_p4", "P-029", "p4")

        ids = [p.card_image_id for p in resolve_base_id(self.conn, "P-029")]
        self.assertEqual(ids[0], "P-029")
        self.assertEqual(ids, ["P-029", "P-029_p3", "P-029_p4", "P-029_r2"])

    def test_unknown_id_returns_empty(self):
        self.assertEqual(resolve_base_id(self.conn, "XX99-999"), [])

    def test_carries_name_and_rarity(self):
        add(self.conn, "OP05-097_p1", "OP05-097", "p1", "Luffy (Parallel)", "SR")
        p = resolve_base_id(self.conn, "OP05-097")[0]
        self.assertEqual(p.name, "Luffy (Parallel)")
        self.assertEqual(p.rarity, "SR")


class DefaultPrintingTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_prefers_variant_free_printing(self):
        add(self.conn, "OP05-097_p1", "OP05-097", "p1")
        add(self.conn, "OP05-097", "OP05-097", None)
        add(self.conn, "OP05-097_p2", "OP05-097", "p2")
        self.assertEqual(default_printing(self.conn, "OP05-097"), "OP05-097")

    def test_falls_back_to_lowest_sorting_when_no_base_printing(self):
        # Defensive path — no live card hits this after D-014, but a future set
        # could ship one that only exists as a variant.
        add(self.conn, "P-057_p1", "P-057", "p1")
        add(self.conn, "P-057_p3", "P-057", "p3")
        self.assertEqual(default_printing(self.conn, "P-057"), "P-057_p1")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(default_printing(self.conn, "XX99-999"))

    def test_deterministic_regardless_of_insert_order(self):
        add(self.conn, "OP05-097_p2", "OP05-097", "p2")
        add(self.conn, "OP05-097_p1", "OP05-097", "p1")
        first = default_printing(self.conn, "OP05-097")

        other = make_db()
        add(other, "OP05-097_p1", "OP05-097", "p1")
        add(other, "OP05-097_p2", "OP05-097", "p2")
        second = default_printing(other, "OP05-097")
        other.close()

        self.assertEqual(first, second)


class UnknownIdsTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add(self.conn, "OP05-097", "OP05-097")
        add(self.conn, "OP05-020", "OP05-020")

    def tearDown(self):
        self.conn.close()

    def test_all_known(self):
        self.assertEqual(unknown_ids(self.conn, ["OP05-097", "OP05-020"]), [])

    def test_reports_only_the_missing_ones_in_order(self):
        result = unknown_ids(self.conn, ["ZZ01-001", "OP05-097", "YY02-002"])
        self.assertEqual(result, ["ZZ01-001", "YY02-002"])

    def test_deduplicates(self):
        self.assertEqual(unknown_ids(self.conn, ["ZZ01-001", "ZZ01-001"]), ["ZZ01-001"])

    def test_empty_input(self):
        self.assertEqual(unknown_ids(self.conn, []), [])

    def test_matches_on_gameplay_id_not_printing(self):
        add(self.conn, "OP05-030_p1", "OP05-030", "p1")
        self.assertEqual(unknown_ids(self.conn, ["OP05-030"]), [])


if __name__ == "__main__":
    unittest.main()
