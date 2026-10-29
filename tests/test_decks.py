"""Unit tests for optcg/decks.py (import).

Run:
    python -m pytest tests/test_decks.py -v
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from optcg.db_setup import create_schema
from optcg.storage import add_location, deck_container, loose_container, resolve_container
from optcg.collection import CollectionError, add, move, remove, set_art, show, total_owned
from optcg.decks import (
    DeckError,
    DeckNameExists,
    add_to_deck,
    delete_deck,
    import_decklist,
    list_decks,
    new_deck,
    remove_from_deck,
    resolve_deck,
    set_physical,
    show_deck,
)

LEADER = "OP05-100"  # kept clear of the OP05-001..013 characters below


def make_db() -> sqlite3.Connection:
    """A DB stocked with a leader plus 13 characters, each with an alt art."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, name, category) "
        "VALUES (?, ?, 'Leader', 'Leader')",
        (LEADER, LEADER),
    )
    for i in range(1, 14):
        base = f"OP05-{i:03d}"
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, name, category) "
            "VALUES (?, ?, ?, 'Character')",
            (base, base, f"Card {i}"),
        )
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, category) "
            "VALUES (?, ?, 'p1', ?, 'Character')",
            (f"{base}_p1", base, f"Card {i} (Parallel)"),
        )
    conn.commit()
    return conn


def decklist(counts: dict[str, int] | None = None, leader: str | None = LEADER) -> str:
    """A decklist totalling 50 non-leader cards unless overridden."""
    if counts is None:
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}  # 48
        counts["OP05-013"] = 2                               # 50
    lines = []
    if leader:
        lines.append(f"Leader: 1 Leader {leader}")
    lines.append("DON!!: x10")
    lines.append("Character:")
    lines += [f"{qty} Card {cid}" for cid, qty in counts.items()]
    return "\n".join(lines) + "\n"


def availability(conn, base_card_id):
    """(owned, committed, available) for one gameplay card.

    Aggregated per base_card_id, which is the level commitment works at
    (D-003): this fixture gives every card an alt art, so a per-printing
    reading would subtract the same commitment twice.
    """
    status = show(conn, base_card_id)
    return (status.total_owned, status.committed, status.loose)


def owned(conn, base_card_id):
    """Copies of one gameplay card, wherever they sit and whatever art (D-030)."""
    return total_owned(conn, base_card_id)


def copies_in(conn, base_card_id, container):
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM copies WHERE base_card_id = ? "
        "AND location_id IS ? AND deck_id IS ?",
        (base_card_id, container.location_id, container.deck_id),
    ).fetchone()
    return row[0]


def deck_rows(conn, deck_id):
    return dict(
        conn.execute(
            "SELECT base_card_id, quantity FROM deck_cards WHERE deck_id = ?", (deck_id,)
        )
    )


class ImportBasicsTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_creates_deck_and_cards(self):
        result = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(result.name, "Red Luffy")
        self.assertTrue(result.is_physical)
        self.assertEqual(result.leader_id, LEADER)
        self.assertEqual(result.total_cards, 51)  # 50 + leader
        self.assertEqual(len(deck_rows(self.conn, result.deck_id)), 14)

    def test_leader_is_stored_both_ways(self):
        """leader_id identifies it; the deck_cards row makes it lockable."""
        result = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        stored = self.conn.execute(
            "SELECT leader_id FROM decks WHERE id = ?", (result.deck_id,)
        ).fetchone()[0]
        self.assertEqual(stored, LEADER)
        self.assertEqual(deck_rows(self.conn, result.deck_id)[LEADER], 1)

    def test_physical_import_credits_the_collection(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(owned(self.conn, LEADER), 1)       # leader credited too
        self.assertEqual(owned(self.conn, "OP05-002"), 4)
        self.assertEqual(owned(self.conn, "OP05-013"), 2)

    def test_imported_copies_are_unsorted_not_a_guessed_art(self):
        """D-030 kills D-016: a decklist names a card, so the art stays open."""
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        arts = {a.card_image_id for a in show(self.conn, "OP05-002").arts}
        self.assertEqual(arts, {None})

    def test_imported_copies_are_placed_in_the_deck(self):
        result = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        deck = deck_container(self.conn, result.deck_id)
        self.assertEqual(copies_in(self.conn, "OP05-002", deck), 4)
        self.assertEqual(copies_in(self.conn, "OP05-002", loose_container(self.conn)), 0)

    def test_wishlist_import_never_touches_the_collection(self):
        result = import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        self.assertEqual(result.collection_credited, 0)
        total = self.conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0]
        self.assertEqual(total, 0)
        self.assertEqual(len(deck_rows(self.conn, result.deck_id)), 14)

    def test_locked_cards_show_in_availability(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 4, 0))

    def test_wishlist_deck_locks_nothing(self):
        import_decklist(self.conn, decklist(), "Real", is_physical=True)
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        owned_, committed, _ = availability(self.conn, "OP05-002")
        self.assertEqual((owned_, committed), (4, 4))  # only the physical deck counts

    def test_empty_name_refused(self):
        with self.assertRaises(DeckError):
            import_decklist(self.conn, decklist(), "   ")

    def test_empty_decklist_refused(self):
        with self.assertRaises(DeckError):
            import_decklist(self.conn, "", "Nothing")

    def test_unknown_conflict_policy_refused(self):
        with self.assertRaises(DeckError):
            import_decklist(self.conn, decklist(), "X", on_conflict="merge")


class WarningTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_unknown_ids_warn_and_are_skipped(self):
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        counts["ZZ99-999"] = 1
        result = import_decklist(self.conn, decklist(counts), "Red Luffy", is_physical=True)
        self.assertIn("not in the card database", " ".join(result.warnings))
        self.assertNotIn("ZZ99-999", deck_rows(self.conn, result.deck_id))

    def test_import_still_succeeds_with_unknown_ids(self):
        counts = {"OP05-001": 4, "ZZ99-999": 1}
        result = import_decklist(self.conn, decklist(counts), "Partial", is_physical=True)
        self.assertGreater(result.distinct_cards, 0)

    def test_all_unknown_ids_refused(self):
        with self.assertRaises(DeckError):
            import_decklist(self.conn, decklist({"ZZ99-999": 4}, leader=None), "Bogus")

    def test_more_than_four_copies_warns_but_imports(self):
        counts = {"OP05-001": 6, "OP05-002": 44}
        result = import_decklist(self.conn, decklist(counts), "Greedy", is_physical=True)
        self.assertIn("exceeds the usual 4", " ".join(result.warnings))
        self.assertEqual(deck_rows(self.conn, result.deck_id)["OP05-001"], 6)

    def test_wrong_deck_size_warns(self):
        result = import_decklist(self.conn, decklist({"OP05-002": 4}), "Short")
        self.assertIn("expected 50", " ".join(result.warnings))

    def test_correct_deck_size_is_silent(self):
        result = import_decklist(self.conn, decklist(), "Right")
        self.assertNotIn("expected 50", " ".join(result.warnings))

    def test_missing_leader_warns(self):
        result = import_decklist(self.conn, decklist(leader=None), "Leaderless")
        self.assertIn("no leader", " ".join(result.warnings))
        self.assertIsNone(result.leader_id)


class NameConflictTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.first = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)

    def tearDown(self):
        self.conn.close()

    def test_clash_raises_so_the_caller_can_ask(self):
        with self.assertRaises(DeckNameExists) as ctx:
            import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(ctx.exception.deck_id, self.first.deck_id)

    def test_clash_changes_nothing(self):
        before = owned(self.conn, "OP05-002")
        with self.assertRaises(DeckNameExists):
            import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(owned(self.conn, "OP05-002"), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 1)

    def test_replace_leaves_one_deck(self):
        result = import_decklist(
            self.conn, decklist(), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertTrue(result.replaced)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 1)

    def test_replace_with_identical_list_credits_nothing(self):
        before = owned(self.conn, "OP05-002")
        result = import_decklist(
            self.conn, decklist(), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertEqual(result.collection_credited, 0)
        self.assertEqual(owned(self.conn, "OP05-002"), before)

    def test_replace_credits_only_the_increase(self):
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        counts["OP05-002"] = 6  # was 4
        import_decklist(
            self.conn, decklist(counts), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertEqual(owned(self.conn, "OP05-002"), 6)  # 4 + 2, not 4 + 6

    def test_replace_moves_dropped_cards_to_just_loose(self):
        """D-041: a physical deck is what's in it, so a card dropped from the list
        leaves the deck. You still own it (D-022) - it's loose now."""
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        del counts["OP05-002"]
        result = import_decklist(
            self.conn, decklist(counts), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertEqual(result.returned_to_loose, 4)
        self.assertEqual(owned(self.conn, "OP05-002"), 4)  # still owned
        _, committed, loose = availability(self.conn, "OP05-002")
        self.assertEqual((committed, loose), (0, 4))
        self.assertNotIn("OP05-002", [c.base_card_id for c in show_deck(self.conn, "Red Luffy").cards])

    def test_replace_running_fewer_takes_only_the_excess(self):
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        counts["OP05-002"] = 1
        result = import_decklist(
            self.conn, decklist(counts), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertEqual(result.returned_to_loose, 3)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 1, 3))

    def test_new_creates_a_second_deck_and_credits_again(self):
        result = import_decklist(
            self.conn, decklist(), "Red Luffy", is_physical=True, on_conflict="new"
        )
        self.assertFalse(result.replaced)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 2)
        self.assertEqual(owned(self.conn, "OP05-002"), 8)  # two physical copies

    def test_replacing_a_physical_deck_with_a_wishlist_frees_the_cards(self):
        import_decklist(
            self.conn, decklist(), "Red Luffy", is_physical=False, on_conflict="replace"
        )
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 0, 4))


class AtomicityTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_failure_midway_leaves_no_partial_deck(self):
        calls = {"n": 0}

        def exploding_deposit(conn, base_card_id, quantity, container, art=None):
            calls["n"] += 1
            if calls["n"] > 3:
                raise sqlite3.OperationalError("simulated failure")
            from optcg.collection import deposit as real_deposit
            return real_deposit(conn, base_card_id, quantity, container, art)

        with mock.patch("optcg.decks.deposit", side_effect=exploding_deposit):
            with self.assertRaises(sqlite3.OperationalError):
                import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 0)
        total = self.conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM copies"
        ).fetchone()[0]
        self.assertEqual(total, 0)

    def test_failed_replace_keeps_the_original_deck(self):
        first = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)

        with mock.patch("optcg.decks.deposit", side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
                counts["OP05-013"] = 3
                import_decklist(
                    self.conn, decklist(counts), "Red Luffy",
                    is_physical=True, on_conflict="replace",
                )

        decks = self.conn.execute("SELECT id FROM decks").fetchall()
        self.assertEqual(len(decks), 1)
        self.assertEqual(decks[0][0], first.deck_id)
        self.assertEqual(len(deck_rows(self.conn, first.deck_id)), 14)


class DeckEditingTests(unittest.TestCase):
    """D-041: a physical deck is exactly what's in it - its list follows its cards."""

    def setUp(self):
        self.conn = make_db()
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.deck = deck_container(self.conn, resolve_deck(self.conn, "Red Luffy"))

    def tearDown(self):
        self.conn.close()

    def listed(self, deck, card):
        row = self.conn.execute(
            "SELECT quantity FROM deck_cards dc JOIN decks d ON d.id = dc.deck_id "
            "WHERE d.name = ? AND dc.base_card_id = ?", (deck, card)).fetchone()
        return row[0] if row else 0

    def test_remove_takes_cards_out_and_shrinks_the_list(self):
        left = remove_from_deck(self.conn, "Red Luffy", "OP05-002", 2)
        self.assertEqual(left, 2)
        self.assertEqual(self.listed("Red Luffy", "OP05-002"), 2)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 2, 2))  # still owned

    def test_remove_the_whole_card_drops_the_line(self):
        remove_from_deck(self.conn, "Red Luffy", "OP05-002", 4)
        self.assertEqual(self.listed("Red Luffy", "OP05-002"), 0)
        self.assertNotIn("OP05-002", [c.base_card_id for c in show_deck(self.conn, "Red Luffy").cards])

    def test_remove_more_than_the_deck_runs_is_refused(self):
        with self.assertRaises(DeckError) as ctx:
            remove_from_deck(self.conn, "Red Luffy", "OP05-002", 5)
        self.assertIn("runs 4 OP05-002, not 5", str(ctx.exception))
        self.assertEqual(self.listed("Red Luffy", "OP05-002"), 4)

    def test_remove_can_send_cards_elsewhere(self):
        add_location(self.conn, "Binder A", "binder")
        binder = resolve_container(self.conn, "Binder A")
        remove_from_deck(self.conn, "Red Luffy", "OP05-002", 1, to=binder)
        self.assertEqual(copies_in(self.conn, "OP05-002", binder), 1)

    def test_remove_takes_unsorted_copies_before_sorted_ones(self):
        set_art(self.conn, "OP05-002", 1, "p1", self.deck)
        remove_from_deck(self.conn, "Red Luffy", "OP05-002", 3)
        rows = self.conn.execute(
            "SELECT card_image_id, quantity FROM copies WHERE base_card_id = 'OP05-002' "
            "AND deck_id IS NOT NULL").fetchall()
        self.assertEqual(rows, [("OP05-002_p1", 1)])

    def test_remove_a_named_art(self):
        set_art(self.conn, "OP05-002", 1, "p1", self.deck)
        remove_from_deck(self.conn, "Red Luffy", "OP05-002", 1, art="p1")
        row = self.conn.execute(
            "SELECT quantity FROM copies WHERE card_image_id = 'OP05-002_p1' "
            "AND location_id IS NOT NULL").fetchone()
        self.assertEqual(row, (1,))

    def test_add_takes_cards_from_just_loose_and_grows_the_list(self):
        add(self.conn, "OP05-013", 2)
        now = add_to_deck(self.conn, "Red Luffy", "OP05-013", 2)
        self.assertEqual(now, 4)
        self.assertEqual(availability(self.conn, "OP05-013"), (4, 4, 0))

    def test_add_cards_you_dont_have_loose_is_refused(self):
        with self.assertRaises(CollectionError):
            add_to_deck(self.conn, "Red Luffy", "OP05-013", 2)
        self.assertEqual(self.listed("Red Luffy", "OP05-013"), 2)

    def test_moving_cards_between_decks_changes_both_lists(self):
        """Taking two to play in another deck: this one is two short, nothing remembers."""
        new_deck(self.conn, "Blue")
        blue = deck_container(self.conn, resolve_deck(self.conn, "Blue"))
        move(self.conn, "OP05-002", 2, self.deck, blue)
        self.assertEqual(self.listed("Red Luffy", "OP05-002"), 2)
        self.assertEqual(self.listed("Blue", "OP05-002"), 2)
        self.assertEqual(show_deck(self.conn, "Red Luffy").total_cards, 49)

    def test_selling_a_card_out_of_a_deck_shrinks_it(self):
        remove(self.conn, "OP05-002", 1, frm=self.deck)
        self.assertEqual(self.listed("Red Luffy", "OP05-002"), 3)
        self.assertEqual(owned(self.conn, "OP05-002"), 3)

    def test_adding_straight_into_a_deck_grows_it(self):
        add(self.conn, "OP05-013", 2, to=self.deck)
        self.assertEqual(self.listed("Red Luffy", "OP05-013"), 4)

    def test_taking_the_leader_out_leaves_no_leader(self):
        remove_from_deck(self.conn, "Red Luffy", LEADER, 1)
        self.assertIsNone(show_deck(self.conn, "Red Luffy").leader_id)

    def test_a_wishlist_only_changes_its_list(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        add_to_deck(self.conn, "Someday", "OP05-013", 1)
        remove_from_deck(self.conn, "Someday", "OP05-002", 4)
        self.assertEqual(self.listed("Someday", "OP05-013"), 3)
        self.assertEqual(self.listed("Someday", "OP05-002"), 0)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 4, 0))  # Red Luffy's, untouched

    def test_a_wishlist_refuses_real_copies(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        with self.assertRaises(DeckError):
            add_to_deck(self.conn, "Someday", "OP05-013", 1, frm=loose_container(self.conn))

    def test_replacing_a_physical_deck_with_a_wishlist_empties_it(self):
        result = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=False,
                                 on_conflict="replace")
        self.assertEqual(result.returned_to_loose, 51)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 0, 4))

    def test_new_deck(self):
        deck_id = new_deck(self.conn, "Enel")
        detail = show_deck(self.conn, deck_id)
        self.assertTrue(detail.is_physical)
        self.assertEqual(detail.total_cards, 0)
        with self.assertRaises(DeckError):
            new_deck(self.conn, "Enel")

if __name__ == "__main__":
    unittest.main()


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_list_decks_physical_first(self):
        import_decklist(self.conn, decklist(), "Wishlist", is_physical=False)
        import_decklist(self.conn, decklist(), "Real", is_physical=True)
        names = [d.name for d in list_decks(self.conn)]
        self.assertEqual(names, ["Real", "Wishlist"])

    def test_list_decks_counts(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        summary = list_decks(self.conn)[0]
        self.assertEqual(summary.distinct_cards, 14)
        self.assertEqual(summary.total_cards, 51)
        self.assertEqual(summary.leader_id, LEADER)
        self.assertTrue(summary.is_physical)

    def test_resolve_by_name_and_id(self):
        result = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(resolve_deck(self.conn, "Red Luffy"), result.deck_id)
        self.assertEqual(resolve_deck(self.conn, result.deck_id), result.deck_id)
        self.assertEqual(resolve_deck(self.conn, str(result.deck_id)), result.deck_id)

    def test_ambiguous_name_reports_ids(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True, on_conflict="new")
        with self.assertRaises(DeckError) as ctx:
            resolve_deck(self.conn, "Red Luffy")
        self.assertIn("use an id", str(ctx.exception))

    def test_unknown_deck_refused(self):
        with self.assertRaises(DeckError):
            resolve_deck(self.conn, "Nope")
        with self.assertRaises(DeckError):
            resolve_deck(self.conn, 999)

    def test_show_physical_deck_is_complete(self):
        """A deck you built owns its own cards - nothing should read as missing."""
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        detail = show_deck(self.conn, "Red Luffy")
        self.assertTrue(detail.is_complete)
        self.assertEqual(detail.missing_total, 0)
        self.assertEqual(detail.total_cards, 51)

    def test_show_wishlist_deck_lists_everything_as_missing(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        detail = show_deck(self.conn, "Someday")
        self.assertFalse(detail.is_complete)
        self.assertEqual(detail.missing_total, 51)

    def test_show_leader_first(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        detail = show_deck(self.conn, "Red Luffy")
        self.assertEqual(detail.cards[0].base_card_id, LEADER)
        self.assertTrue(detail.cards[0].is_leader)

    def test_other_physical_decks_reduce_availability(self):
        import_decklist(self.conn, decklist(), "Deck A", is_physical=True)
        import_decklist(self.conn, decklist(), "Deck B", is_physical=False)
        detail = show_deck(self.conn, "Deck B")
        card = next(c for c in detail.cards if c.base_card_id == "OP05-002")
        self.assertEqual(card.owned, 4)
        self.assertEqual(card.available_to_deck, 0)  # all 4 are in Deck A
        self.assertEqual(card.missing, 4)

    def test_wishlist_decks_do_not_reduce_availability(self):
        import_decklist(self.conn, decklist(), "Real", is_physical=True)
        import_decklist(self.conn, decklist(), "Wish A", is_physical=False)
        import_decklist(self.conn, decklist(), "Wish B", is_physical=False)
        card = next(
            c for c in show_deck(self.conn, "Wish A").cards if c.base_card_id == "OP05-002"
        )
        self.assertEqual(card.available_to_deck, 0)  # only "Real" subtracts

    def test_partially_owned_wishlist_reports_the_shortfall(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        add(self.conn, "OP05-002", 3)
        card = next(
            c for c in show_deck(self.conn, "Someday").cards if c.base_card_id == "OP05-002"
        )
        self.assertEqual(card.owned, 3)
        self.assertEqual(card.missing, 1)  # needs 4

    def test_alt_art_counts_toward_completion(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        add(self.conn, "OP05-002", 4, art="p1")
        card = next(
            c for c in show_deck(self.conn, "Someday").cards if c.base_card_id == "OP05-002"
        )
        self.assertEqual(card.missing, 0)  # any printing satisfies a deck (D-003)

    def test_delete_removes_deck_and_cards_but_keeps_collection(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        delete_deck(self.conn, "Red Luffy")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 0)
        self.assertEqual(owned(self.conn, "OP05-002"), 4)  # still yours

    def test_delete_frees_availability(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        delete_deck(self.conn, "Red Luffy")
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 0, 4))

    def test_a_wishlist_with_a_list_cannot_become_physical(self):
        """D-041: a physical deck can't start out empty with a list it doesn't hold."""
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        with self.assertRaises(DeckError) as ctx:
            set_physical(self.conn, "Someday", True)
        self.assertIn("deck new", str(ctx.exception))
        self.assertFalse(show_deck(self.conn, "Someday").is_physical)

    def test_moving_copies_into_a_physical_deck_commits_them_and_lists_them(self):
        new_deck(self.conn, "Someday")
        add(self.conn, "OP05-002", 4)
        deck = deck_container(self.conn, resolve_deck(self.conn, "Someday"))
        move(self.conn, "OP05-002", 4, loose_container(self.conn), deck)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 4, 0))
        self.assertEqual(show_deck(self.conn, "Someday").total_cards, 4)

    def test_set_physical_off_unmakes_the_deck(self):
        """The sleeved copies come back to just-loose (D-030)."""
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        set_physical(self.conn, "Red Luffy", False)
        self.assertEqual(availability(self.conn, "OP05-002"), (4, 0, 4))
        self.assertEqual(
            copies_in(self.conn, "OP05-002", loose_container(self.conn)), 4
        )

    def test_an_empty_wishlist_can_become_physical(self):
        new_deck(self.conn, "Empty", is_physical=False)
        self.assertEqual(set_physical(self.conn, "Empty", True), [])
        self.assertTrue(show_deck(self.conn, "Empty").is_physical)

    def test_set_physical_off_never_warns(self):
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        self.assertEqual(set_physical(self.conn, "Someday", False), [])
