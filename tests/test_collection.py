"""Unit tests for src/collection.py.

Run:
    python -m pytest tests/test_collection.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.collection import (
    CollectionError,
    add,
    list_cards,
    remove,
    set_quantity,
    show,
)
from src.db_setup import create_schema


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add_card(conn, card_image_id, base_card_id, variant=None, name="Card",
             category="Character", color="Red", set_code="OP-05"):
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, "
        "category, color, set_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (card_image_id, base_card_id, variant, name, category, color, set_code),
    )


def add_deck(conn, name, is_physical, cards):
    cur = conn.execute(
        "INSERT INTO decks (name, is_physical) VALUES (?, ?)", (name, 1 if is_physical else 0)
    )
    for base_card_id, qty in cards:
        conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
            (cur.lastrowid, base_card_id, qty),
        )
    conn.commit()


def owned(conn, card_image_id):
    row = conn.execute(
        "SELECT quantity FROM collection WHERE card_image_id = ?", (card_image_id,)
    ).fetchone()
    return row[0] if row else None


class QuantityTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", None, "Luffy")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_add_creates_the_row(self):
        self.assertEqual(add(self.conn, "OP05-097", 3), 3)
        self.assertEqual(owned(self.conn, "OP05-097"), 3)

    def test_add_accumulates(self):
        add(self.conn, "OP05-097", 2)
        self.assertEqual(add(self.conn, "OP05-097", 3), 5)

    def test_set_is_absolute(self):
        add(self.conn, "OP05-097", 9)
        self.assertEqual(set_quantity(self.conn, "OP05-097", 2), 2)

    def test_remove_subtracts(self):
        add(self.conn, "OP05-097", 5)
        self.assertEqual(remove(self.conn, "OP05-097", 2), 3)

    def test_remove_floors_at_zero_and_keeps_the_row(self):
        add(self.conn, "OP05-097", 2)
        self.assertEqual(remove(self.conn, "OP05-097", 10), 0)
        self.assertEqual(owned(self.conn, "OP05-097"), 0)  # row kept, not deleted (D-005)

    def test_set_to_zero_keeps_the_row(self):
        add(self.conn, "OP05-097", 4)
        set_quantity(self.conn, "OP05-097", 0)
        self.assertEqual(owned(self.conn, "OP05-097"), 0)

    def test_remove_from_absent_card_is_zero_not_an_error(self):
        self.assertEqual(remove(self.conn, "OP05-097", 3), 0)

    def test_unknown_printing_rejected(self):
        with self.assertRaises(CollectionError) as ctx:
            add(self.conn, "ZZ99-999", 1)
        self.assertIn("unknown printing", str(ctx.exception))

    def test_negative_quantities_rejected(self):
        with self.assertRaises(CollectionError):
            add(self.conn, "OP05-097", -1)
        with self.assertRaises(CollectionError):
            remove(self.conn, "OP05-097", -1)
        with self.assertRaises(CollectionError):
            set_quantity(self.conn, "OP05-097", -1)


class GuardTests(unittest.TestCase):
    """The guard works at gameplay level, not printing level (D-003)."""

    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", None, "Luffy")
        add_card(self.conn, "OP05-097_p1", "OP05-097", "p1", "Luffy (Parallel)")
        self.conn.commit()
        add(self.conn, "OP05-097", 4)

    def tearDown(self):
        self.conn.close()

    def test_refuses_dropping_below_what_physical_decks_use(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 4)])
        with self.assertRaises(CollectionError) as ctx:
            set_quantity(self.conn, "OP05-097", 2)
        self.assertIn("physical decks", str(ctx.exception))
        self.assertEqual(owned(self.conn, "OP05-097"), 4)  # unchanged

    def test_allows_dropping_to_exactly_what_is_committed(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 4)])
        self.assertEqual(set_quantity(self.conn, "OP05-097", 4), 4)

    def test_wishlist_decks_do_not_lock_anything(self):
        add_deck(self.conn, "Someday", False, [("OP05-097", 4)])
        self.assertEqual(set_quantity(self.conn, "OP05-097", 0), 0)

    def test_another_printing_can_satisfy_the_commitment(self):
        """Owning the alt art means the base printing can go to zero."""
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 4)])
        add(self.conn, "OP05-097_p1", 4)
        self.assertEqual(set_quantity(self.conn, "OP05-097", 0), 0)

    def test_refuses_when_printings_together_cannot_cover_it(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 5)])
        add(self.conn, "OP05-097_p1", 1)  # 4 + 1 = 5, exactly covered
        with self.assertRaises(CollectionError):
            set_quantity(self.conn, "OP05-097", 3)  # would leave 4 < 5

    def test_adding_is_allowed_even_when_already_over_committed(self):
        """Reachable by flipping a wishlist deck to physical.

        In that state the collection is short, and adding copies is the fix —
        so the guard must not block it.
        """
        add_deck(self.conn, "Greedy", True, [("OP05-097", 10)])
        self.assertEqual(add(self.conn, "OP05-097", 2), 6)
        self.assertEqual(add(self.conn, "OP05-097_p1", 1), 1)

    def test_still_refuses_reductions_while_over_committed(self):
        add_deck(self.conn, "Greedy", True, [("OP05-097", 10)])
        with self.assertRaises(CollectionError):
            remove(self.conn, "OP05-097", 1)

    def test_no_op_set_is_allowed_while_over_committed(self):
        add_deck(self.conn, "Greedy", True, [("OP05-097", 10)])
        self.assertEqual(set_quantity(self.conn, "OP05-097", 4), 4)

    def test_remove_is_guarded_too(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 3)])
        with self.assertRaises(CollectionError):
            remove(self.conn, "OP05-097", 2)


class ShowTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", None, "Luffy")
        add_card(self.conn, "OP05-097_p1", "OP05-097", "p1", "Luffy (Parallel)")
        self.conn.commit()
        add(self.conn, "OP05-097", 4)
        add(self.conn, "OP05-097_p1", 3)

    def tearDown(self):
        self.conn.close()

    def test_unknown_card_returns_none(self):
        self.assertIsNone(show(self.conn, "ZZ99-999"))

    def test_totals_across_printings(self):
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.total_owned, 7)
        self.assertEqual(status.committed, 0)
        self.assertEqual(status.available, 7)
        self.assertEqual(len(status.printings), 2)

    def test_base_printing_listed_first(self):
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.printings[0].card_image_id, "OP05-097")

    def test_committed_and_loose(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 4)])
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.committed, 4)
        self.assertEqual(status.available, 3)
        self.assertEqual(status.loose, 3)

    def test_wishlist_deck_shown_but_not_committed(self):
        add_deck(self.conn, "Someday", False, [("OP05-097", 4)])
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.committed, 0)
        self.assertEqual([d.deck_name for d in status.decks], ["Someday"])
        self.assertFalse(status.decks[0].is_physical)

    def test_unplaced_counts_loose_copies_with_no_location(self):
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.placed, 0)
        self.assertEqual(status.unplaced, 7)

    def test_placements_reduce_unplaced(self):
        self.conn.execute("INSERT INTO storage_locations (name) VALUES ('Binder A')")
        loc = self.conn.execute("SELECT id FROM storage_locations").fetchone()[0]
        self.conn.execute(
            "INSERT INTO collection_placements (card_image_id, location_id, quantity) "
            "VALUES ('OP05-097', ?, 2)",
            (loc,),
        )
        self.conn.commit()

        status = show(self.conn, "OP05-097")
        self.assertEqual(status.placed, 2)
        self.assertEqual(status.unplaced, 5)
        self.assertEqual(status.printings[0].placements[0].location, "Binder A")

    def test_card_with_no_collection_rows_shows_zeroes(self):
        add_card(self.conn, "OP05-020", "OP05-020", None, "Zoro")
        self.conn.commit()
        status = show(self.conn, "OP05-020")
        self.assertEqual(status.total_owned, 0)
        self.assertEqual(status.printings[0].owned, 0)


class ListTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", None, "Luffy", "Character", "Red", "OP-05")
        add_card(self.conn, "OP05-001", "OP05-001", None, "Leader", "Leader", "Blue/Red", "OP-05")
        add_card(self.conn, "OP06-010", "OP06-010", None, "Other", "Character", "Green", "OP-06")
        self.conn.commit()
        add(self.conn, "OP05-097", 2)

    def tearDown(self):
        self.conn.close()

    def test_lists_everything_by_default(self):
        self.assertEqual(len(list_cards(self.conn)), 3)

    def test_owned_only(self):
        rows = list_cards(self.conn, owned_only=True)
        self.assertEqual([r[0] for r in rows], ["OP05-097"])

    def test_filter_by_set(self):
        rows = list_cards(self.conn, set_code="OP-05")
        self.assertEqual(len(rows), 2)

    def test_filter_by_category(self):
        rows = list_cards(self.conn, category="Leader")
        self.assertEqual([r[0] for r in rows], ["OP05-001"])

    def test_filter_by_color_matches_multicolour(self):
        rows = list_cards(self.conn, color="Blue")
        self.assertEqual([r[0] for r in rows], ["OP05-001"])

    def test_limit(self):
        self.assertEqual(len(list_cards(self.conn, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
