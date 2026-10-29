"""Schema tests — the tables, and the constraints that keep `copies` honest.

Availability encodes real business rules (D-009, D-010, D-003) so it gets
tested like code. It is computed by `collection.show()`, never stored: D-026
removed the `available_cards` view, and D-030 removed the `collection` table
that held the total.

Run:
    python -m pytest tests/test_schema.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from optcg.collection import add, move, show
from optcg.db_setup import DEFAULT_LOOSE_LOCATION, create_schema
from optcg.storage import deck_container, loose_container


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add_card(conn, card_image_id, base_card_id, name="Test Card", variant=None):
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, category) "
        "VALUES (?, ?, ?, ?, 'Character')",
        (card_image_id, base_card_id, variant, name),
    )
    conn.commit()


def add_deck(conn, name, is_physical, cards=()):
    cur = conn.execute(
        "INSERT INTO decks (name, is_physical) VALUES (?, ?)", (name, 1 if is_physical else 0)
    )
    deck_id = cur.lastrowid
    for base_card_id, qty in cards:
        conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
            (deck_id, base_card_id, qty),
        )
    conn.commit()
    return deck_id


def available(conn, base_card_id):
    """(owned, committed, loose) for one gameplay card.

    Aggregated per base_card_id — the level commitment applies at (D-003).
    None only when the id isn't a real card at all.
    """
    status = show(conn, base_card_id)
    if status is None:
        return None
    return (status.total_owned, status.committed, status.loose)


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_all_tables_exist(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        for expected in [
            "cards", "card_types", "known_types", "unknown_type_log",
            "skipped_cards_log", "copies", "decks", "deck_cards",
            "app_settings", "storage_locations",
        ]:
            self.assertIn(expected, names)

    def test_tables_replaced_by_copies_are_gone(self):
        """D-030: one ledger, not a total plus a placement breakdown."""
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("collection", names)
        self.assertNotIn("collection_placements", names)

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

    def test_just_loose_is_seeded(self):
        row = self.conn.execute(
            "SELECT kind FROM storage_locations WHERE name = ?", (DEFAULT_LOOSE_LOCATION,)
        ).fetchone()
        self.assertEqual(row[0], "loose")

    def test_create_schema_is_idempotent(self):
        create_schema(self.conn)  # must not raise
        create_schema(self.conn)


class CopiesConstraintTests(unittest.TestCase):
    """The table has to make the impossible states unrepresentable."""

    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", "Luffy")
        self.loose_id = loose_container(self.conn).location_id
        self.deck_id = add_deck(self.conn, "Law", True)

    def tearDown(self):
        self.conn.close()

    def insert(self, **fields):
        fields.setdefault("base_card_id", "OP05-097")
        fields.setdefault("quantity", 1)
        cols = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        self.conn.execute(f"INSERT INTO copies ({cols}) VALUES ({marks})", tuple(fields.values()))

    def test_a_copy_needs_exactly_one_container(self):
        self.insert(location_id=self.loose_id)          # fine
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(location_id=self.loose_id, deck_id=self.deck_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert()                                # neither

    def test_quantity_must_be_positive(self):
        """A row exists because copies exist; zero means delete the row."""
        for bad in (0, -1):
            with self.assertRaises(sqlite3.IntegrityError):
                self.insert(location_id=self.loose_id, quantity=bad)

    def test_one_row_per_card_art_and_container(self):
        self.insert(location_id=self.loose_id, card_image_id="OP05-097")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(location_id=self.loose_id, card_image_id="OP05-097")

    def test_unsorted_rows_are_deduplicated_too(self):
        """SQLite treats NULLs as distinct, so the index has to COALESCE them."""
        self.insert(location_id=self.loose_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(location_id=self.loose_id)

    def test_the_same_card_may_sit_in_several_containers(self):
        self.insert(location_id=self.loose_id)
        self.insert(deck_id=self.deck_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0], 2
        )

    def test_sorted_and_unsorted_coexist_in_one_container(self):
        add_card(self.conn, "OP05-097_p1", "OP05-097", "Luffy (Parallel)", "p1")
        self.insert(location_id=self.loose_id)
        self.insert(location_id=self.loose_id, card_image_id="OP05-097_p1")
        self.assertEqual(
            self.conn.execute("SELECT SUM(quantity) FROM copies").fetchone()[0], 2
        )

    def test_the_art_must_be_a_real_printing(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(location_id=self.loose_id, card_image_id="XX99-999")

    def test_a_container_holding_copies_cannot_just_be_deleted(self):
        """No cascade: deleting a binder must move its cards, never destroy them."""
        self.insert(location_id=self.loose_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "DELETE FROM storage_locations WHERE id = ?", (self.loose_id,)
            )


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", "Luffy")
        self.loose = loose_container(self.conn)
        add(self.conn, "OP05-097", 4)

    def tearDown(self):
        self.conn.close()

    def sleeve(self, deck_name, qty, physical=True):
        deck = deck_container(self.conn, add_deck(self.conn, deck_name, physical))
        move(self.conn, "OP05-097", qty, self.loose, deck)
        return deck

    def test_all_loose_when_no_decks(self):
        self.assertEqual(available(self.conn, "OP05-097"), (4, 0, 4))

    def test_a_physical_deck_commits_the_copies_in_it(self):
        self.sleeve("Red Luffy", 3)
        self.assertEqual(available(self.conn, "OP05-097"), (4, 3, 1))

    def test_a_wishlist_deck_commits_nothing(self):
        """Copies can sit in a wishlist deck's container without being committed."""
        self.sleeve("Someday", 4, physical=False)
        self.assertEqual(available(self.conn, "OP05-097"), (4, 0, 4))

    def test_only_physical_decks_count_when_both_exist(self):
        self.sleeve("Real", 2)
        self.sleeve("Wishlist", 1, physical=False)
        self.assertEqual(available(self.conn, "OP05-097"), (4, 2, 2))

    def test_multiple_physical_decks_sum(self):
        self.sleeve("Deck A", 2)
        self.sleeve("Deck B", 1)
        self.assertEqual(available(self.conn, "OP05-097"), (4, 3, 1))

    def test_you_cannot_commit_more_than_you_own(self):
        """Structural under D-030: a committed copy *is* a copy you own."""
        from optcg.collection import CollectionError

        with self.assertRaises(CollectionError):
            self.sleeve("Greedy", 6)
        self.assertEqual(available(self.conn, "OP05-097"), (4, 0, 4))

    def test_cards_you_own_none_of_report_zero(self):
        add_card(self.conn, "OP05-020", "OP05-020", "Zoro")
        self.assertEqual(available(self.conn, "OP05-020"), (0, 0, 0))

    def test_unknown_ids_are_none(self):
        self.assertIsNone(available(self.conn, "XX99-999"))

    def test_commitment_is_gameplay_level_and_counted_once(self):
        """Owning several arts of one card must not multiply the commitment.

        This is what the deleted view got wrong (D-026) and what keying
        ownership on base_card_id makes structurally impossible (D-030).
        """
        add_card(self.conn, "OP05-097_p1", "OP05-097", "Luffy (Parallel)", "p1")
        add(self.conn, "OP05-097", 2, art="p1")
        self.sleeve("Red Luffy", 3)

        # 6 owned across two arts, 3 committed once -> 3 genuinely free.
        self.assertEqual(available(self.conn, "OP05-097"), (6, 3, 3))

        status = show(self.conn, "OP05-097")
        self.assertEqual(
            {a.card_image_id: a.quantity for a in status.arts},
            {None: 4, "OP05-097_p1": 2},
        )

    def test_an_alt_art_can_satisfy_a_deck(self):
        """A decklist names a card, not an art, so any printing counts."""
        add_card(self.conn, "OP05-097_p1", "OP05-097", "Luffy (Parallel)", "p1")
        add(self.conn, "OP05-097", 1, art="p1")
        deck = self.sleeve("Red Luffy", 4)
        move(self.conn, "OP05-097", 1, self.loose, deck, art="p1")
        self.assertEqual(available(self.conn, "OP05-097"), (5, 5, 0))


if __name__ == "__main__":
    unittest.main()
