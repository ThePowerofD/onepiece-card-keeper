"""Schema tests — availability and the Phase 1 tables.

Availability encodes real business rules (D-009, D-010, D-003), so it gets
tested like code. It is computed by `collection.show()`, not by SQL: D-026
removed the `available_cards` view because it could not express them.

Run:
    python -m pytest tests/test_schema.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.collection import show
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


def available(conn, base_card_id):
    """(owned, committed, available) for one gameplay card.

    Aggregated per base_card_id — the level commitment applies at (D-003).
    None only when the id isn't a real card at all.
    """
    status = show(conn, base_card_id)
    if status is None:
        return None
    return (status.total_owned, status.committed, status.available)


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
        ]:
            self.assertIn(expected, names)

    def test_cards_has_game_set_and_printing_kind(self):
        """D-028: the set a card belongs to, and what kind of printing it is."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(cards)")}
        for col in ("game_set", "printing_kind"):
            self.assertIn(col, cols)

    def test_cards_has_attribute_life_and_rotation_block(self):
        """D-027: attribute and life are synced; rotation_block is not."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(cards)")}
        for col in ("attribute", "life", "rotation_block"):
            self.assertIn(col, cols)

    def test_rotation_block_is_nullable(self):
        """It ships empty, so inserting a card without one must work."""
        add_card(self.conn, "OP05-097", "OP05-097", "Luffy")
        self.assertIsNone(
            self.conn.execute(
                "SELECT rotation_block FROM cards WHERE card_image_id = 'OP05-097'"
            ).fetchone()[0]
        )

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
        # Availability reports the truth; the guard that prevents this lives in
        # the collection commands (Task 1.4).
        add_deck(self.conn, "Greedy", True, [("OP05-097", 6)])
        self.assertEqual(available(self.conn, "OP05-097"), (4, 6, -2))

    def test_cards_not_in_collection_report_zero(self):
        """A real card you own none of is 0/0/0, not missing.

        The old view inner-joined `collection` and dropped such cards entirely,
        which a card browser would have had to paper over (D-026).
        """
        add_card(self.conn, "OP05-020", "OP05-020", "Zoro")
        self.assertEqual(available(self.conn, "OP05-020"), (0, 0, 0))

    def test_unknown_ids_are_none(self):
        self.assertIsNone(available(self.conn, "XX99-999"))

    def test_commitment_is_gameplay_level_and_counted_once(self):
        """The case the deleted view got wrong (D-026).

        A deck says 'OP05-097' without naming a printing, so commitment is
        gameplay-level while ownership is printing-level (D-003). The view
        repeated the same committed figure on every printing row; `show()`
        aggregates per base_card_id and subtracts it exactly once.
        """
        add_card(self.conn, "OP05-097_p1", "OP05-097", "Luffy (Parallel)")
        self.conn.execute(
            "INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097_p1', 2)"
        )
        add_deck(self.conn, "Red Luffy", True, [("OP05-097", 3)])

        # 6 owned across two printings, 3 committed once → 3 genuinely free.
        self.assertEqual(available(self.conn, "OP05-097"), (6, 3, 3))

        # Both printings are reported, and ownership stays printing-level.
        status = show(self.conn, "OP05-097")
        self.assertEqual(
            {p.card_image_id: p.owned for p in status.printings},
            {"OP05-097": 4, "OP05-097_p1": 2},
        )


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
