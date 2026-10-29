"""Unit tests for optcg/collection.py — the copy model (D-030).

Run:
    python -m pytest tests/test_collection.py -v
"""

from __future__ import annotations

import sqlite3
import unittest

from optcg.collection import (
    CollectionError,
    add,
    committed,
    committed_elsewhere,
    empty_container,
    list_cards,
    move,
    remove,
    resolve_art,
    set_art,
    show,
    total_owned,
)
from optcg.db_setup import create_schema
from optcg.storage import add_location, deck_container, loose_container, resolve_container


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def add_card(conn, card_image_id, base_card_id, variant=None, name="Card",
             category="Character", color="Red", game_set="OP05"):
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, "
        "category, color, game_set) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (card_image_id, base_card_id, variant, name, category, color, game_set),
    )
    conn.commit()


def add_deck(conn, name, is_physical=True):
    cur = conn.execute(
        "INSERT INTO decks (name, is_physical) VALUES (?, ?)", (name, 1 if is_physical else 0)
    )
    conn.commit()
    return cur.lastrowid


def slots(conn, base_card_id):
    """Every copy row for a card as (art, container_kind, quantity)."""
    return conn.execute(
        "SELECT card_image_id, location_id, deck_id, quantity FROM copies "
        "WHERE base_card_id = ? ORDER BY (card_image_id IS NOT NULL), card_image_id",
        (base_card_id,),
    ).fetchall()


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_card(self.conn, "OP05-097", "OP05-097", name="Luffy")
        add_card(self.conn, "OP05-097_p1", "OP05-097", "p1", name="Luffy (Parallel)")
        add_card(self.conn, "OP05-002", "OP05-002", name="Zoro")
        self.loose = loose_container(self.conn)

    def tearDown(self):
        self.conn.close()


class AddTests(Base):
    def test_lands_unsorted_in_just_loose(self):
        self.assertEqual(add(self.conn, "OP05-097", 4), 4)
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [(None, self.loose.location_id, None, 4)])

    def test_repeated_adds_accumulate_in_one_row(self):
        add(self.conn, "OP05-097", 4)
        self.assertEqual(add(self.conn, "OP05-097", 2), 6)
        self.assertEqual(len(slots(self.conn, "OP05-097")), 1)

    def test_art_can_be_given_up_front(self):
        add(self.conn, "OP05-097", 2, art="p1")
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [("OP05-097_p1", self.loose.location_id, None, 2)])

    def test_sorted_and_unsorted_are_separate_rows(self):
        add(self.conn, "OP05-097", 3)
        add(self.conn, "OP05-097", 1, art="p1")
        self.assertEqual(total_owned(self.conn, "OP05-097"), 4)
        self.assertEqual(len(slots(self.conn, "OP05-097")), 2)

    def test_into_a_named_container(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 2, to=binder)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 2)
        self.assertEqual(slots(self.conn, "OP05-097"), [(None, binder.location_id, None, 2)])

    def test_unknown_card_is_refused(self):
        with self.assertRaises(CollectionError):
            add(self.conn, "XX99-999", 1)

    def test_quantity_must_be_positive(self):
        for bad in (0, -1):
            with self.assertRaises(CollectionError):
                add(self.conn, "OP05-097", bad)


class ResolveArtTests(Base):
    def test_none_means_unsorted(self):
        self.assertIsNone(resolve_art(self.conn, "OP05-097", None))
        self.assertIsNone(resolve_art(self.conn, "OP05-097", "unsorted"))

    def test_bare_variant_full_id_and_base(self):
        self.assertEqual(resolve_art(self.conn, "OP05-097", "p1"), "OP05-097_p1")
        self.assertEqual(resolve_art(self.conn, "OP05-097", "OP05-097_p1"), "OP05-097_p1")
        self.assertEqual(resolve_art(self.conn, "OP05-097", "base"), "OP05-097")

    def test_art_of_a_different_card_is_refused(self):
        add_card(self.conn, "OP05-002_p1", "OP05-002", "p1")
        with self.assertRaises(CollectionError) as ctx:
            resolve_art(self.conn, "OP05-097", "OP05-002_p1")
        self.assertIn("printing of OP05-002", str(ctx.exception))

    def test_unknown_art_lists_what_exists(self):
        with self.assertRaises(CollectionError) as ctx:
            resolve_art(self.conn, "OP05-097", "zz9")
        self.assertIn("base", str(ctx.exception))
        self.assertIn("p1", str(ctx.exception))


class RemoveTests(Base):
    def test_takes_from_just_loose_by_default(self):
        add(self.conn, "OP05-097", 4)
        self.assertEqual(remove(self.conn, "OP05-097", 1), 3)

    def test_empties_the_row_completely(self):
        add(self.conn, "OP05-097", 2)
        self.assertEqual(remove(self.conn, "OP05-097", 2), 0)
        self.assertEqual(slots(self.conn, "OP05-097"), [])

    def test_refuses_more_than_the_source_holds_and_writes_nothing(self):
        add(self.conn, "OP05-097", 2)
        with self.assertRaises(CollectionError) as ctx:
            remove(self.conn, "OP05-097", 3)
        self.assertIn("has 2", str(ctx.exception))
        self.assertEqual(total_owned(self.conn, "OP05-097"), 2)

    def test_unsorted_and_sorted_copies_are_taken_separately(self):
        add(self.conn, "OP05-097", 2)
        add(self.conn, "OP05-097", 2, art="p1")
        remove(self.conn, "OP05-097", 2, art="p1")
        self.assertEqual(total_owned(self.conn, "OP05-097"), 2)
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [(None, self.loose.location_id, None, 2)])

    def test_removing_a_sorted_art_you_have_none_of_is_refused(self):
        add(self.conn, "OP05-097", 4)          # all unsorted
        with self.assertRaises(CollectionError):
            remove(self.conn, "OP05-097", 1, art="p1")


class MoveTests(Base):
    def setUp(self):
        super().setUp()
        add_location(self.conn, "Binder A", "binder")
        self.binder = resolve_container(self.conn, "Binder A")

    def test_moves_between_containers(self):
        add(self.conn, "OP05-097", 4)
        self.assertEqual(move(self.conn, "OP05-097", 2, self.loose, self.binder), 2)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 4)  # a move is not a loss

    def test_merges_into_an_existing_row_at_the_destination(self):
        add(self.conn, "OP05-097", 2, to=self.binder)
        add(self.conn, "OP05-097", 3)
        self.assertEqual(move(self.conn, "OP05-097", 3, self.loose, self.binder), 5)
        self.assertEqual(len(slots(self.conn, "OP05-097")), 1)

    def test_overflow_is_an_error_that_writes_nothing(self):
        add(self.conn, "OP05-097", 2)
        with self.assertRaises(CollectionError):
            move(self.conn, "OP05-097", 3, self.loose, self.binder)
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [(None, self.loose.location_id, None, 2)])

    def test_same_container_is_refused(self):
        add(self.conn, "OP05-097", 2)
        with self.assertRaises(CollectionError):
            move(self.conn, "OP05-097", 1, self.loose, self.loose)

    def test_into_and_out_of_a_deck(self):
        deck_id = add_deck(self.conn, "Law")
        deck = deck_container(self.conn, deck_id)
        add(self.conn, "OP05-097", 4)
        move(self.conn, "OP05-097", 3, self.loose, deck)
        self.assertEqual(committed(self.conn, "OP05-097"), 3)
        move(self.conn, "OP05-097", 3, deck, self.loose)
        self.assertEqual(committed(self.conn, "OP05-097"), 0)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 4)

    def test_a_loan_is_a_move_and_keeps_ownership(self):
        add_location(self.conn, "Marco", "loan")
        marco = resolve_container(self.conn, "Marco")
        add(self.conn, "OP05-097", 2)
        move(self.conn, "OP05-097", 1, self.loose, marco)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 2)
        status = show(self.conn, "OP05-097")
        self.assertIn(("Marco", "loan"), [(h.container, h.kind) for h in status.holdings])


class SetArtTests(Base):
    def test_splits_the_row_without_changing_the_total(self):
        add(self.conn, "OP05-097", 4)
        self.assertEqual(set_art(self.conn, "OP05-097", 1, "p1"), 3)
        self.assertEqual(total_owned(self.conn, "OP05-097"), 4)
        self.assertEqual(
            slots(self.conn, "OP05-097"),
            [(None, self.loose.location_id, None, 3),
             ("OP05-097_p1", self.loose.location_id, None, 1)],
        )

    def test_sorting_the_last_copy_removes_the_unsorted_row(self):
        add(self.conn, "OP05-097", 2)
        self.assertEqual(set_art(self.conn, "OP05-097", 2, "p1"), 0)
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [("OP05-097_p1", self.loose.location_id, None, 2)])

    def test_applies_where_the_copies_actually_are(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 2, to=binder)
        add(self.conn, "OP05-097", 2)
        set_art(self.conn, "OP05-097", 1, "p1", container=binder)
        in_binder = {r[0]: r[3] for r in slots(self.conn, "OP05-097")
                     if r[1] == binder.location_id}
        self.assertEqual(in_binder, {None: 1, "OP05-097_p1": 1})

    def test_re_sorting_from_one_art_to_another(self):
        add(self.conn, "OP05-097", 2, art="p1")
        set_art(self.conn, "OP05-097", 1, "base", frm_art="p1")
        self.assertEqual({r[0]: r[3] for r in slots(self.conn, "OP05-097")},
                         {"OP05-097_p1": 1, "OP05-097": 1})

    def test_refuses_more_than_the_container_holds(self):
        add(self.conn, "OP05-097", 1)
        with self.assertRaises(CollectionError):
            set_art(self.conn, "OP05-097", 2, "p1")
        self.assertEqual(total_owned(self.conn, "OP05-097"), 1)

    def test_needs_a_real_printing(self):
        add(self.conn, "OP05-097", 1)
        with self.assertRaises(CollectionError):
            set_art(self.conn, "OP05-097", 1, "unsorted")


class CommittedTests(Base):
    def test_only_physical_decks_commit(self):
        physical = deck_container(self.conn, add_deck(self.conn, "Real", True))
        wishlist = deck_container(self.conn, add_deck(self.conn, "Someday", False))
        add(self.conn, "OP05-097", 4)
        move(self.conn, "OP05-097", 2, self.loose, physical)
        move(self.conn, "OP05-097", 1, self.loose, wishlist)
        self.assertEqual(committed(self.conn, "OP05-097"), 2)

    def test_a_deck_never_competes_with_itself(self):
        a = add_deck(self.conn, "A", True)
        b = add_deck(self.conn, "B", True)
        add(self.conn, "OP05-097", 4)
        move(self.conn, "OP05-097", 2, self.loose, deck_container(self.conn, a))
        move(self.conn, "OP05-097", 1, self.loose, deck_container(self.conn, b))
        self.assertEqual(committed_elsewhere(self.conn, "OP05-097", a), 1)
        self.assertEqual(committed_elsewhere(self.conn, "OP05-097", b), 2)


class ShowTests(Base):
    def test_unknown_card_is_none(self):
        self.assertIsNone(show(self.conn, "XX99-999"))

    def test_card_you_own_none_of_reports_zero(self):
        status = show(self.conn, "OP05-097")
        self.assertEqual(status.total_owned, 0)
        self.assertEqual(status.arts, [])
        self.assertEqual(len(status.printings), 2)

    def test_arts_holdings_and_derived_totals(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        deck = deck_container(self.conn, add_deck(self.conn, "Law", True))
        add(self.conn, "OP05-097", 4)
        move(self.conn, "OP05-097", 2, self.loose, binder)
        move(self.conn, "OP05-097", 1, self.loose, deck)
        set_art(self.conn, "OP05-097", 1, "p1", container=binder)

        status = show(self.conn, "OP05-097")
        self.assertEqual(status.total_owned, 4)
        self.assertEqual(status.committed, 1)
        self.assertEqual(status.loose, 3)
        self.assertEqual(status.unsorted, 3)
        self.assertEqual({(a.card_image_id, a.quantity) for a in status.arts},
                         {(None, 3), ("OP05-097_p1", 1)})
        self.assertEqual({(h.container, h.kind, h.quantity) for h in status.holdings},
                         {("Binder A", "binder", 1), ("Binder A", "binder", 1),
                          ("Law", "deck", 1), ("just-loose", "loose", 1)})


class EmptyContainerTests(Base):
    def test_moves_everything_to_just_loose_and_merges(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        add(self.conn, "OP05-097", 2, to=binder)
        add(self.conn, "OP05-097", 1)
        add(self.conn, "OP05-002", 3, to=binder)

        self.assertEqual(empty_container(self.conn, binder), 5)
        self.conn.commit()
        self.assertEqual(slots(self.conn, "OP05-097"),
                         [(None, self.loose.location_id, None, 3)])
        self.assertEqual(total_owned(self.conn, "OP05-002"), 3)


class ListCardsTests(Base):
    def test_owned_counts_the_art_and_card_owned_counts_the_card(self):
        add(self.conn, "OP05-097", 3)             # unsorted
        add(self.conn, "OP05-097", 1, art="p1")
        rows = {r[0]: (r[4], r[5]) for r in list_cards(self.conn)}
        self.assertEqual(rows["OP05-097"], (0, 4))       # no copies recorded as base art
        self.assertEqual(rows["OP05-097_p1"], (1, 4))

    def test_owned_only_keeps_cards_whose_art_is_unrecorded(self):
        add(self.conn, "OP05-097", 2)
        ids = {r[0] for r in list_cards(self.conn, owned_only=True)}
        self.assertEqual(ids, {"OP05-097", "OP05-097_p1"})

    def test_filters(self):
        add_card(self.conn, "OP06-001", "OP06-001", name="Nami",
                 category="Leader", color="Blue", game_set="OP06")
        self.assertEqual({r[0] for r in list_cards(self.conn, set_code="OP06")}, {"OP06-001"})
        self.assertEqual({r[0] for r in list_cards(self.conn, color="Blue")}, {"OP06-001"})
        self.assertEqual({r[0] for r in list_cards(self.conn, category="Leader")}, {"OP06-001"})
        self.assertEqual({r[0] for r in list_cards(self.conn, q="Nam")}, {"OP06-001"})

    def test_limit(self):
        self.assertEqual(len(list_cards(self.conn, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
