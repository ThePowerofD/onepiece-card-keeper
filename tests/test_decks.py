"""Unit tests for src/decks.py (import).

Run:
    python -m pytest tests/test_decks.py -v
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from src.db_setup import create_schema
from src.decks import DeckError, DeckNameExists, import_decklist

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


def owned(conn, card_image_id):
    row = conn.execute(
        "SELECT quantity FROM collection WHERE card_image_id = ?", (card_image_id,)
    ).fetchone()
    return row[0] if row else 0


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

    def test_credits_the_base_printing_not_the_alt_art(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        self.assertEqual(owned(self.conn, "OP05-002"), 4)
        self.assertEqual(owned(self.conn, "OP05-002_p1"), 0)

    def test_wishlist_import_never_touches_the_collection(self):
        result = import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        self.assertEqual(result.collection_credited, 0)
        total = self.conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
        self.assertEqual(total, 0)
        self.assertEqual(len(deck_rows(self.conn, result.deck_id)), 14)

    def test_locked_cards_show_in_availability(self):
        import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)
        row = self.conn.execute(
            "SELECT owned, committed, available FROM available_cards "
            "WHERE card_image_id = 'OP05-002'"
        ).fetchone()
        self.assertEqual(row, (4, 4, 0))

    def test_wishlist_deck_locks_nothing(self):
        import_decklist(self.conn, decklist(), "Real", is_physical=True)
        import_decklist(self.conn, decklist(), "Someday", is_physical=False)
        row = self.conn.execute(
            "SELECT owned, committed FROM available_cards WHERE card_image_id = 'OP05-002'"
        ).fetchone()
        self.assertEqual(row, (4, 4))  # only the physical deck counts

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

    def test_replace_does_not_remove_cards_you_still_own(self):
        """Dropping a card from a list doesn't mean you sold it — it goes loose."""
        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        del counts["OP05-002"]
        import_decklist(
            self.conn, decklist(counts), "Red Luffy", is_physical=True, on_conflict="replace"
        )
        self.assertEqual(owned(self.conn, "OP05-002"), 4)  # still owned
        row = self.conn.execute(
            "SELECT committed, available FROM available_cards WHERE card_image_id = 'OP05-002'"
        ).fetchone()
        self.assertEqual(row, (0, 4))  # now free to use

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
        row = self.conn.execute(
            "SELECT owned, committed, available FROM available_cards "
            "WHERE card_image_id = 'OP05-002'"
        ).fetchone()
        self.assertEqual(row, (4, 0, 4))


class AtomicityTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def test_failure_midway_leaves_no_partial_deck(self):
        calls = {"n": 0}

        def exploding_credit(conn, card_image_id, delta):
            calls["n"] += 1
            if calls["n"] > 3:
                raise sqlite3.OperationalError("simulated failure")
            from src.collection import credit as real
            return real(conn, card_image_id, delta)

        with mock.patch("src.decks.credit", side_effect=exploding_credit):
            with self.assertRaises(sqlite3.OperationalError):
                import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0], 0)
        total = self.conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM collection"
        ).fetchone()[0]
        self.assertEqual(total, 0)

    def test_failed_replace_keeps_the_original_deck(self):
        first = import_decklist(self.conn, decklist(), "Red Luffy", is_physical=True)

        with mock.patch("src.decks.credit", side_effect=sqlite3.OperationalError("boom")):
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


if __name__ == "__main__":
    unittest.main()
