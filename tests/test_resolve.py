"""Unit tests for optcg/resolve.py.

D-030 removed `default_printing` and `resolve_base_id` along with D-016, so
only the unknown-id check remains here; printings are listed by
`collection.printings_of` and covered in test_collection.py.

Run:
    python -m pytest tests/test_resolve.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from optcg.db_setup import create_schema
from optcg.resolve import unknown_ids


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
