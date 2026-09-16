"""Unit tests for src/storage.py.

Run:
    python -m pytest tests/test_storage.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from src.db_setup import create_schema
from src.storage import (
    LOCATION_KINDS,
    AmbiguousLocation,
    StorageError,
    add_location,
    contents,
    delete_location,
    list_locations,
    place,
    rename_location,
    unplace,
)


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, name, category) "
        "VALUES ('OP05-097', 'OP05-097', 'Luffy', 'Character')"
    )
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, category) "
        "VALUES ('OP05-097_p1', 'OP05-097', 'p1', 'Luffy (Parallel)', 'Character')"
    )
    conn.execute("INSERT INTO collection (card_image_id, quantity) VALUES ('OP05-097', 7)")
    conn.commit()
    return conn


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_add_and_list(self):
        add_location(self.conn, "Binder A", notes="red decks")
        locations = list_locations(self.conn)
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].name, "Binder A")
        self.assertEqual(locations[0].notes, "red decks")
        self.assertEqual(locations[0].total_cards, 0)

    def test_names_are_trimmed(self):
        add_location(self.conn, "  Binder A  ")
        self.assertEqual(list_locations(self.conn)[0].name, "Binder A")

    def test_duplicate_name_refused(self):
        add_location(self.conn, "Binder A")
        with self.assertRaises(StorageError) as ctx:
            add_location(self.conn, "Binder A")
        self.assertIn("already exists", str(ctx.exception))

    def test_empty_name_refused(self):
        with self.assertRaises(StorageError):
            add_location(self.conn, "   ")

    def test_rename(self):
        add_location(self.conn, "Binder A")
        rename_location(self.conn, "Binder A", "Binder One")
        self.assertEqual(list_locations(self.conn)[0].name, "Binder One")

    # -- kinds ------------------------------------------------------------

    def test_kind_defaults_to_storage(self):
        add_location(self.conn, "OP13 Box")
        self.assertEqual(list_locations(self.conn)[0].kind, "storage")

    def test_unknown_kind_refused(self):
        with self.assertRaises(StorageError) as ctx:
            add_location(self.conn, "Somewhere", "deck")
        self.assertIn("unknown location kind", str(ctx.exception))

    def test_every_kind_is_accepted(self):
        for kind in LOCATION_KINDS:
            add_location(self.conn, f"place-{kind}", kind)
        self.assertEqual(len(list_locations(self.conn)), len(LOCATION_KINDS))

    def test_same_name_allowed_under_different_kinds(self):
        add_location(self.conn, "Box 1", "storage")
        add_location(self.conn, "Box 1", "binder")
        self.assertEqual(len(list_locations(self.conn)), 2)

    def test_duplicate_name_within_one_kind_refused(self):
        add_location(self.conn, "Box 1", "storage")
        with self.assertRaises(StorageError) as ctx:
            add_location(self.conn, "Box 1", "storage")
        self.assertIn("already exists", str(ctx.exception))

    def test_ambiguous_bare_name_refuses_rather_than_guessing(self):
        add_location(self.conn, "Box 1", "storage")
        add_location(self.conn, "Box 1", "binder")
        with self.assertRaises(AmbiguousLocation) as ctx:
            place(self.conn, "OP05-097", "Box 1", 2)
        self.assertEqual(sorted(ctx.exception.kinds), ["binder", "storage"])

    def test_kind_disambiguates(self):
        add_location(self.conn, "Box 1", "storage")
        add_location(self.conn, "Box 1", "binder")
        place(self.conn, "OP05-097", "Box 1", 2, kind="binder")
        self.assertEqual(contents(self.conn, "Box 1", kind="binder")[0][2], 2)
        self.assertEqual(contents(self.conn, "Box 1", kind="storage"), [])

    def test_list_filters_by_kind(self):
        add_location(self.conn, "Mewtwo Binder", "binder")
        add_location(self.conn, "OP13 Box", "storage")
        add_location(self.conn, "Friend 1", "loan")
        loans = list_locations(self.conn, "loan")
        self.assertEqual([loc.name for loc in loans], ["Friend 1"])

    def test_list_is_grouped_by_kind(self):
        add_location(self.conn, "OP13 Box", "storage")
        add_location(self.conn, "Mewtwo Binder", "binder")
        add_location(self.conn, "Friend 1", "loan")
        kinds = [loc.kind for loc in list_locations(self.conn)]
        self.assertEqual(kinds, sorted(kinds))

    def test_rename_to_existing_name_refused(self):
        add_location(self.conn, "Binder A")
        add_location(self.conn, "Binder B")
        with self.assertRaises(StorageError):
            rename_location(self.conn, "Binder A", "Binder B")

    def test_unknown_location_reports_clearly(self):
        with self.assertRaises(StorageError) as ctx:
            rename_location(self.conn, "Nowhere", "Somewhere")
        self.assertIn("no storage location", str(ctx.exception))

    def test_counts_reflect_placements(self):
        add_location(self.conn, "Binder A")
        place(self.conn, "OP05-097", "Binder A", 2)
        place(self.conn, "OP05-097_p1", "Binder A", 3)
        summary = list_locations(self.conn)[0]
        self.assertEqual(summary.distinct_cards, 2)
        self.assertEqual(summary.total_cards, 5)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_location(self.conn, "Binder A")
        add_location(self.conn, "Trade box")

    def tearDown(self):
        self.conn.close()

    def test_place_records_quantity(self):
        result = place(self.conn, "OP05-097", "Binder A", 2)
        self.assertEqual(result.quantity, 2)
        self.assertIsNone(result.warning)

    def test_place_is_absolute_not_additive(self):
        place(self.conn, "OP05-097", "Binder A", 2)
        place(self.conn, "OP05-097", "Binder A", 2)
        self.assertEqual(contents(self.conn, "Binder A")[0][2], 2)

    def test_place_zero_removes_the_row(self):
        place(self.conn, "OP05-097", "Binder A", 2)
        place(self.conn, "OP05-097", "Binder A", 0)
        self.assertEqual(contents(self.conn, "Binder A"), [])

    def test_copies_split_across_locations(self):
        place(self.conn, "OP05-097", "Binder A", 2)
        place(self.conn, "OP05-097", "Trade box", 1)
        self.assertEqual(contents(self.conn, "Binder A")[0][2], 2)
        self.assertEqual(contents(self.conn, "Trade box")[0][2], 1)

    def test_negative_quantity_refused(self):
        with self.assertRaises(StorageError):
            place(self.conn, "OP05-097", "Binder A", -1)

    def test_unknown_card_refused(self):
        with self.assertRaises(StorageError) as ctx:
            place(self.conn, "ZZ99-999", "Binder A", 1)
        self.assertIn("unknown printing", str(ctx.exception))

    def test_unknown_location_refused(self):
        with self.assertRaises(StorageError):
            place(self.conn, "OP05-097", "Nowhere", 1)

    def test_unplace_all(self):
        place(self.conn, "OP05-097", "Binder A", 3)
        self.assertEqual(unplace(self.conn, "OP05-097", "Binder A"), 0)
        self.assertEqual(contents(self.conn, "Binder A"), [])

    def test_unplace_partial(self):
        place(self.conn, "OP05-097", "Binder A", 3)
        self.assertEqual(unplace(self.conn, "OP05-097", "Binder A", 1), 2)

    def test_unplace_more_than_present_floors_at_zero(self):
        place(self.conn, "OP05-097", "Binder A", 2)
        self.assertEqual(unplace(self.conn, "OP05-097", "Binder A", 10), 0)

    def test_unplace_absent_placement_is_zero(self):
        self.assertEqual(unplace(self.conn, "OP05-097", "Binder A"), 0)


class OverPlacementWarningTests(unittest.TestCase):
    """Over-placement warns; it must never block (D-019)."""

    def setUp(self):
        self.conn = make_db()
        add_location(self.conn, "Binder A")
        add_location(self.conn, "Trade box")

    def tearDown(self):
        self.conn.close()

    def test_placing_more_than_owned_warns_but_saves(self):
        result = place(self.conn, "OP05-097", "Binder A", 99)
        self.assertIsNotNone(result.warning)
        self.assertIn("only 7 owned", result.warning)
        self.assertEqual(contents(self.conn, "Binder A")[0][2], 99)  # saved anyway

    def test_split_placements_exceeding_owned_warns(self):
        place(self.conn, "OP05-097", "Binder A", 5)
        result = place(self.conn, "OP05-097", "Trade box", 5)
        self.assertIn("10 placed", result.warning)

    def test_placing_cards_that_are_sleeved_warns(self):
        self.conn.execute("INSERT INTO decks (name, is_physical) VALUES ('Red Luffy', 1)")
        deck_id = self.conn.execute("SELECT id FROM decks").fetchone()[0]
        self.conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, 'OP05-097', 5)",
            (deck_id,),
        )
        self.conn.commit()

        # 7 owned, 5 sleeved → only 2 loose, so placing 4 is impossible
        result = place(self.conn, "OP05-097", "Binder A", 4)
        self.assertIsNotNone(result.warning)
        self.assertIn("only 2 loose", result.warning)

    def test_placing_within_loose_count_is_silent(self):
        self.conn.execute("INSERT INTO decks (name, is_physical) VALUES ('Red Luffy', 1)")
        deck_id = self.conn.execute("SELECT id FROM decks").fetchone()[0]
        self.conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, 'OP05-097', 5)",
            (deck_id,),
        )
        self.conn.commit()
        self.assertIsNone(place(self.conn, "OP05-097", "Binder A", 2).warning)

    def test_wishlist_decks_do_not_reduce_loose(self):
        self.conn.execute("INSERT INTO decks (name, is_physical) VALUES ('Someday', 0)")
        deck_id = self.conn.execute("SELECT id FROM decks").fetchone()[0]
        self.conn.execute(
            "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, 'OP05-097', 5)",
            (deck_id,),
        )
        self.conn.commit()
        self.assertIsNone(place(self.conn, "OP05-097", "Binder A", 7).warning)


class DeleteLocationTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_location(self.conn, "Binder A")

    def tearDown(self):
        self.conn.close()

    def test_delete_cascades_placements_but_keeps_ownership(self):
        place(self.conn, "OP05-097", "Binder A", 3)
        removed = delete_location(self.conn, "Binder A")

        self.assertEqual(removed, 1)
        self.assertEqual(list_locations(self.conn), [])
        owned = self.conn.execute(
            "SELECT quantity FROM collection WHERE card_image_id = 'OP05-097'"
        ).fetchone()[0]
        self.assertEqual(owned, 7)  # you still own them (D-019)

    def test_delete_unknown_location_refused(self):
        with self.assertRaises(StorageError):
            delete_location(self.conn, "Nowhere")


if __name__ == "__main__":
    unittest.main()


class ExtraStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_location(self.conn, "Binder A")

    def tearDown(self):
        self.conn.close()

    def test_rename_to_an_empty_name_refused(self):
        with self.assertRaises(StorageError):
            rename_location(self.conn, "Binder A", "   ")

    def test_contents_respects_a_limit(self):
        place(self.conn, "OP05-097", "Binder A", 1)
        place(self.conn, "OP05-097_p1", "Binder A", 1)
        self.assertEqual(len(contents(self.conn, "Binder A")), 2)
        self.assertEqual(len(contents(self.conn, "Binder A", limit=1)), 1)

    def test_contents_hides_zero_quantity_rows(self):
        place(self.conn, "OP05-097", "Binder A", 2)
        place(self.conn, "OP05-097", "Binder A", 0)
        self.assertEqual(contents(self.conn, "Binder A"), [])
