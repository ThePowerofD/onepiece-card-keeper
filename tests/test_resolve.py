"""Unit tests for optcg/resolve.py.

D-030 removed `default_printing` and `resolve_base_id` along with D-016.
What's here: the unknown-id check, and looking cards up by name (D-037).
Printings are listed by `collection.printings_of`, tested in test_collection.py.

Run:
    python -m pytest tests/test_resolve.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from optcg.db_setup import create_schema
from optcg.resolve import card_matches, corrected_query, exact_card, find_cards, unknown_ids


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


class NameLookupTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add(self.conn, "OP06-060", "OP06-060", name="Vinsmoke Ichiji (060)")
        add(self.conn, "OP06-060_p2", "OP06-060", "p2",
            "Vinsmoke Ichiji (OP06-060) (Jolly Roger Foil)")
        add(self.conn, "OP06-061", "OP06-061", name="Vinsmoke Ichiji (061)")
        add(self.conn, "OP06-072", "OP06-072", name="Cosette")
        add(self.conn, "ST31-004", "ST31-004", name="Monkey.D.Luffy (ST31-004)")
        add(self.conn, "EB02-039", "EB02-039", name="GERMA 66")

    def ids(self, text):
        return [m.base_card_id for m in find_cards(self.conn, text)]

    def test_exact_id_in_any_case(self):
        self.assertEqual(exact_card(self.conn, "op06-072"), "OP06-072")
        self.assertEqual(exact_card(self.conn, " OP06-072 "), "OP06-072")
        self.assertIsNone(exact_card(self.conn, "OP99-999"))

    def test_a_shared_name_returns_every_card_once(self):
        """Two printings of OP06-060 match, but it is one card."""
        self.assertEqual(self.ids("ichiji"), ["OP06-060", "OP06-061"])

    def test_capitals_dots_and_word_order_dont_matter(self):
        self.assertEqual(self.ids("monkey d luffy"), ["ST31-004"])
        self.assertEqual(self.ids("LUFFY MONKEY"), ["ST31-004"])
        self.assertEqual(self.ids("germa 66"), ["EB02-039"])

    def test_every_word_must_match(self):
        self.assertEqual(self.ids("vinsmoke cosette"), [])

    def test_a_variant_name_finds_its_card(self):
        self.assertEqual(self.ids("jolly roger"), ["OP06-060"])

    def test_described_by_the_base_printing_without_the_number_tag(self):
        self.assertEqual(find_cards(self.conn, "ichiji")[0].name, "Vinsmoke Ichiji")
        self.assertEqual(find_cards(self.conn, "luffy")[0].name, "Monkey.D.Luffy")

    def test_owned_count(self):
        self.conn.execute(
            "INSERT INTO copies (base_card_id, location_id, quantity) VALUES ('OP06-072', 1, 3)"
        )
        self.assertEqual(find_cards(self.conn, "cosette")[0].owned, 3)

    def test_card_matches_skips_unknown_ids(self):
        found = card_matches(self.conn, {"OP06-072", "OP99-999"})
        self.assertEqual([m.base_card_id for m in found], ["OP06-072"])

    def test_typo_is_corrected_word_by_word(self):
        self.assertEqual(corrected_query(self.conn, "ichjii"), "ichiji")
        self.assertEqual(corrected_query(self.conn, "vinsmok ichji"), "vinsmoke ichiji")
        self.assertEqual(corrected_query(self.conn, "cosete"), "cosette")

    def test_no_suggestion_for_nonsense_or_a_correct_query(self):
        self.assertIsNone(corrected_query(self.conn, "zzzz"))
        self.assertIsNone(corrected_query(self.conn, "cosette"))

if __name__ == "__main__":
    unittest.main()
