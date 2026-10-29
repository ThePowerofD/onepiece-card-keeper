"""Unit tests for optcg/storage.py — containers and locations (D-025, D-030).

Run:
    python -m pytest tests/test_storage.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from optcg.collection import add, total_owned
from optcg.db_setup import BUILTIN_STORAGE_TYPES, STARTER_STORAGE_TYPES, DEFAULT_LOOSE_LOCATION, create_schema
from optcg.storage import (
    AmbiguousLocation,
    StorageError,
    add_location,
    contents,
    deck_container,
    delete_location,
    list_locations,
    loose_container,
    rename_location,
    resolve_container,
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
    conn.commit()
    return conn


def add_deck(conn, name, is_physical=True):
    cur = conn.execute(
        "INSERT INTO decks (name, is_physical) VALUES (?, ?)", (name, 1 if is_physical else 0)
    )
    conn.commit()
    return cur.lastrowid


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()


class BuiltInLooseTests(Base):
    def test_created_with_the_schema(self):
        loose = loose_container(self.conn)
        self.assertEqual(loose.name, DEFAULT_LOOSE_LOCATION)
        self.assertEqual(loose.kind, "loose")
        self.assertFalse(loose.is_deck)

    def test_creating_the_schema_twice_does_not_duplicate_it(self):
        create_schema(self.conn)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM storage_locations WHERE name = ?",
            (DEFAULT_LOOSE_LOCATION,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_no_container_named_means_just_loose(self):
        self.assertEqual(resolve_container(self.conn).name, DEFAULT_LOOSE_LOCATION)
        self.assertEqual(resolve_container(self.conn, None).name, DEFAULT_LOOSE_LOCATION)
        self.assertEqual(resolve_container(self.conn, "  ").name, DEFAULT_LOOSE_LOCATION)

    def test_cannot_be_renamed_or_deleted(self):
        """Every command with no container named falls back to it."""
        with self.assertRaises(StorageError):
            rename_location(self.conn, DEFAULT_LOOSE_LOCATION, "mine")
        with self.assertRaises(StorageError):
            delete_location(self.conn, DEFAULT_LOOSE_LOCATION)

    def test_other_loose_locations_are_allowed_alongside_it(self):
        add_location(self.conn, "Floor1", "loose")
        kinds = {loc.name for loc in list_locations(self.conn, "loose")}
        self.assertEqual(kinds, {DEFAULT_LOOSE_LOCATION, "Floor1"})


class AddLocationTests(Base):
    def test_every_kind(self):
        for i, kind in enumerate(BUILTIN_STORAGE_TYPES + STARTER_STORAGE_TYPES):
            add_location(self.conn, f"L{i}", kind)
        self.assertEqual(len(list_locations(self.conn)), len(BUILTIN_STORAGE_TYPES + STARTER_STORAGE_TYPES) + 1)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(StorageError):
            add_location(self.conn, "X", "shoebox")

    def test_empty_name_is_refused(self):
        with self.assertRaises(StorageError):
            add_location(self.conn, "   ", "binder")

    def test_names_are_unique_per_kind_not_globally(self):
        add_location(self.conn, "Box 1", "booster-box")
        add_location(self.conn, "Box 1", "binder")     # fine - different kind
        with self.assertRaises(StorageError):
            add_location(self.conn, "Box 1", "binder")


class ResolveContainerTests(Base):
    def test_by_bare_name(self):
        add_location(self.conn, "Binder A", "binder")
        self.assertEqual(resolve_container(self.conn, "Binder A").kind, "binder")

    def test_ambiguous_across_kinds_refuses_and_kind_resolves_it(self):
        add_location(self.conn, "Box 1", "booster-box")
        add_location(self.conn, "Box 1", "binder")
        with self.assertRaises(AmbiguousLocation) as ctx:
            resolve_container(self.conn, "Box 1")
        self.assertEqual(sorted(ctx.exception.kinds), ["binder", "booster-box"])
        self.assertEqual(resolve_container(self.conn, "Box 1", "binder").kind, "binder")

    def test_a_deck_resolves_as_a_container(self):
        deck_id = add_deck(self.conn, "Law")
        container = resolve_container(self.conn, "Law")
        self.assertTrue(container.is_deck)
        self.assertEqual(container.deck_id, deck_id)

    def test_a_location_and_a_deck_sharing_a_name_is_ambiguous(self):
        add_location(self.conn, "Law", "binder")
        add_deck(self.conn, "Law")
        with self.assertRaises(AmbiguousLocation):
            resolve_container(self.conn, "Law")
        self.assertTrue(resolve_container(self.conn, "Law", "deck").is_deck)
        self.assertFalse(resolve_container(self.conn, "Law", "binder").is_deck)

    def test_decks_can_be_excluded(self):
        add_deck(self.conn, "Law")
        with self.assertRaises(StorageError):
            resolve_container(self.conn, "Law", allow_deck=False)

    def test_two_decks_with_one_name_asks_for_an_id(self):
        add_deck(self.conn, "Law")
        add_deck(self.conn, "Law")
        with self.assertRaises(StorageError) as ctx:
            resolve_container(self.conn, "Law", "deck")
        self.assertIn("use an id", str(ctx.exception))

    def test_missing_container(self):
        with self.assertRaises(StorageError):
            resolve_container(self.conn, "Nowhere")

    def test_deck_container_by_id(self):
        deck_id = add_deck(self.conn, "Law")
        self.assertEqual(deck_container(self.conn, deck_id).name, "Law")
        with self.assertRaises(StorageError):
            deck_container(self.conn, 999)


class ListLocationsTests(Base):
    def test_counts_cards_and_copies(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 3, to=binder)
        add(self.conn, "OP05-097", 1, art="p1", to=binder)
        summary = next(l for l in list_locations(self.conn) if l.name == "Binder A")
        self.assertEqual(summary.distinct_cards, 1)   # one gameplay card
        self.assertEqual(summary.total_cards, 4)      # four copies

    def test_empty_location_counts_zero(self):
        add_location(self.conn, "Empty", "booster-box")
        summary = next(l for l in list_locations(self.conn) if l.name == "Empty")
        self.assertEqual((summary.distinct_cards, summary.total_cards), (0, 0))

    def test_filter_by_kind(self):
        add_location(self.conn, "Binder A", "binder")
        self.assertEqual([l.name for l in list_locations(self.conn, "binder")], ["Binder A"])


class RenameAndDeleteTests(Base):
    def test_rename(self):
        add_location(self.conn, "Binder A", "binder")
        rename_location(self.conn, "Binder A", "Binder B")
        self.assertEqual([l.name for l in list_locations(self.conn, "binder")], ["Binder B"])

    def test_rename_onto_an_existing_name_is_refused(self):
        add_location(self.conn, "A", "binder")
        add_location(self.conn, "B", "binder")
        with self.assertRaises(StorageError):
            rename_location(self.conn, "A", "B")

    def test_delete_returns_copies_to_just_loose_rather_than_destroying_them(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 3, to=binder)

        self.assertEqual(delete_location(self.conn, "Binder A"), 3)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 3)
        self.assertEqual(
            contents(self.conn, DEFAULT_LOOSE_LOCATION),
            [("OP05-097", None, "Luffy", 3)],
        )

    def test_delete_an_empty_location(self):
        add_location(self.conn, "Empty", "booster-box")
        self.assertEqual(delete_location(self.conn, "Empty"), 0)

    def test_delete_missing_location(self):
        with self.assertRaises(StorageError):
            delete_location(self.conn, "Nowhere")


class ContentsTests(Base):
    def test_lists_sorted_and_unsorted_copies(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 2, to=binder)
        add(self.conn, "OP05-097", 1, art="p1", to=binder)
        self.assertEqual(
            contents(self.conn, "Binder A"),
            [("OP05-097", None, "Luffy", 2),
             ("OP05-097", "OP05-097_p1", "Luffy (Parallel)", 1)],
        )

    def test_defaults_to_just_loose(self):
        add(self.conn, "OP05-097", 2)
        self.assertEqual(contents(self.conn), [("OP05-097", None, "Luffy", 2)])

    def test_a_decks_contents(self):
        deck = deck_container(self.conn, add_deck(self.conn, "Law"))
        add(self.conn, "OP05-097", 2, to=deck)
        self.assertEqual(contents(self.conn, "Law"), [("OP05-097", None, "Luffy", 2)])

    def test_empty_container(self):
        add_location(self.conn, "Empty", "booster-box")
        self.assertEqual(contents(self.conn, "Empty"), [])

    def test_limit(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 2, to=binder)
        add(self.conn, "OP05-097", 1, art="p1", to=binder)
        self.assertEqual(len(contents(self.conn, "Binder A", limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
