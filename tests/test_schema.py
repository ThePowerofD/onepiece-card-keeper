"""Schema tests — the availability view and the Phase 1 tables.

The view encodes real business rules (D-009, D-010, D-003), so it gets tested
like code rather than trusted like configuration.

Run:
    python -m pytest tests/test_schema.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.db_setup import create_schema


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add_card(conn, card_image_id, base_card_id, name="Test Card"):
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, name, category) VALUES (?, ?, ?, 'Character')",
        (card_image_id, base_card_id, name),
    )


def add_deck(conn, name, is_physical, cards):
    cur = conn.execute(
        "INSERT INTO decks (name, is_physical) VALUES (?, ?)", (name, 1 if is_physical else 0)
    )
    deck_id = cur.lastrowid
    for base_card_id, qty in cards:
        conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
            (deck_id, base_card_id, qty),
        )
    return deck_id


def available(conn, card_image_id):
    row = conn.execute(
        "SELECT owned, committed, available FROM available_cards WHERE card_image_id = ?",
        (card_image_id,),
    ).fetchone()
    return row


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_all_tables_and_view_exist(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        for expected in [
            "cards", "card_types", "known_types", "unknown_type_log",
            "skipped_cards_log", "collection", "decks", "deck_cards",
            "app_settings", "storage_locations", "collection_placements",
            "available_cards",
        ]:
            self.assertIn(expected, names)

    def test_collection_has_foil_quantity_defaulting_to_zero(self):
        add_card(self.conn, "OP05-097", "OP05-097")
        self.conn.execute(
            "INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097', 3)"
        )
        row = self.conn.execute(
            "SELECT quantity, foil_quantity FROM collection WHERE card_image_id = 'OP05-097'"
        ).fetchone()
        self.assertEqual(row, (3, 0))

    def test_create_schema_is_idempotent(self):
        create_schema(self.conn)  # must not raise
        create_schema(self.conn)


class AvailabilityViewTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", "Luffy")
        self.conn.execute(
            "INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097', 4)"
        )

    def tearDown(self):
        self.conn.close()

    def test_all_available_when_no_decks(self):
        self.assertEqual(available(self.conn, "OP05-097"), (4, 0, 4))

    def test_physical_deck_locks_cards(self):
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 3)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 3, 1))

    def test_wishlist_deck_locks_nothing(self):
        add_deck(self.conn, "Someday", False, [("OP05-097", 4)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 0, 4))

    def test_only_physical_decks_count_when_both_exist(self):
        add_deck(self.conn, "Real", True, [("OP05-097", 2)])
        add_deck(self.conn, "Wishlist", False, [("OP05-097", 4)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 2, 2))

    def test_multiple_physical_decks_sum(self):
        add_deck(self.conn, "Deck A", True, [("OP05-097", 2)])
        add_deck(self.conn, "Deck B", True, [("OP05-097", 1)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 3, 1))

    def test_available_goes_negative_when_overcommitted(self):
        # The view reports the truth; the guard that prevents this lives in the
        # collection commands (Task 1.4), not in SQL.
        add_deck(self.conn, "Greedy", True, [("OP05-097", 6)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 6, -2))

    def test_cards_not_in_collection_are_absent_from_the_view(self):
        add_card(self.conn, "OP05-020", "OP05-020", "Zoro")
        self.assertIsNone(available(self.conn, "OP05-020"))

    def test_commitment_is_gameplay_level_so_printings_over_subtract(self):
        """Documents the known D-003 caveat rather than pretending it's absent.

        Both printings of OP05-097 report the same committed figure, because a
        deck says 'OP05-097' without naming a printing. Summing `available`
        across printings therefore under-counts — aggregate per base_card_id.
        """
        add_card(self.conn, "OP05-097_p1", "OP05-097", "Luffy (Parallel)")
        self.conn.execute(
            "INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097_p1', 2)"
        )
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 3)])

        self.assertEqual(available(self.conn, "OP05-097"), (4, 3, 1))
        self.assertEqual(available(self.conn, "OP05-097_p1"), (2, 3, -1))

        owned = self.conn.execute(
            "SELECT SUM(owned) FROM available_cards WHERE base_card_id = 'OP05-097'"
        ).fetchone()[0]
        self.assertEqual(owned, 6)  # 6 owned, 3 committed → 3 genuinely free


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097")
        self.conn.execute(
            "INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097', 7)"
        )

    def tearDown(self):
        self.conn.close()

    def place(self, location, qty):
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO storage_locations (name) VALUES (?)", (location,)
        )
        loc_id = cur.lastrowid or self.conn.execute(
            "SELECT id FROM storage_locations WHERE name = ?", (location,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO collection_placements (card_image_id, location_id, quantity) VALUES (?, ?, ?)",
            ("OP05-097", loc_id, qty),
        )
        return loc_id

    def test_copies_split_across_locations(self):
        self.place("Binder A", 2)
        self.place("Trade box", 1)
        placed = self.conn.execute(
            "SELECT SUM(quantity) FROM collection_placements WHERE card_image_id = 'OP05-097'"
        ).fetchone()[0]
        self.assertEqual(placed, 3)

    def test_location_names_are_unique(self):
        self.place("Binder A", 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO storage_locations (name) VALUES ('Binder A')")

    def test_one_placement_row_per_card_and_location(self):
        loc_id = self.place("Binder A", 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO collection_placements (card_image_id, location_id, quantity) "
                "VALUES ('OP05-097', ?, 1)",
                (loc_id,),
            )

    def test_deleting_a_location_cascades_placements_but_keeps_ownership(self):
        loc_id = self.place("Binder A", 2)
        self.conn.execute("DELETE FROM storage_locations WHERE id = ?", (loc_id,))

        placements = self.conn.execute(
            "SELECT COUNT(*) FROM collection_placements"
        ).fetchone()[0]
        owned = self.conn.execute(
            "SELECT quantity FROM collection WHERE card_image_id = 'OP05-097'"
        ).fetchone()[0]

        self.assertEqual(placements, 0)
        self.assertEqual(owned, 7)  # you still own them — you just haven't said where

    def test_placement_quantity_cannot_be_negative(self):
        loc_id = self.place("Binder A", 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE collection_placements SET quantity = -1 WHERE location_id = ?",
                (loc_id,),
            )


if __name__ == "__main__":
    unittest.main()
