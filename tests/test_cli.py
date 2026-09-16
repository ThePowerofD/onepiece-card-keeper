"""Smoke tests for src/cli.py.

Exercises argument wiring and exit codes rather than re-testing the modules
underneath, which have their own suites.

Run:
    python -m pytest tests/test_cli.py -v
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src.cli import main
from src.db_setup import create_schema

LEADER = "OP05-100"


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"

        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, name, category, color, set_code) "
            "VALUES (?, ?, 'Leader', 'Leader', 'Red', 'OP-05')",
            (LEADER, LEADER),
        )
        for i in range(1, 14):
            base = f"OP05-{i:03d}"
            conn.execute(
                "INSERT INTO cards (card_image_id, base_card_id, name, category, color, set_code) "
                "VALUES (?, ?, ?, 'Character', 'Red', 'OP-05')",
                (base, base, f"Card {i}"),
            )
            conn.execute(
                "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, "
                "category, color, set_code) VALUES (?, ?, 'p1', ?, 'Character', 'Red', 'OP-05')",
                (f"{base}_p1", base, f"Card {i} (Parallel)"),
            )
        conn.commit()
        conn.close()

        counts = {f"OP05-{i:03d}": 4 for i in range(1, 13)}
        counts["OP05-013"] = 2
        lines = [f"Leader: 1 Leader {LEADER}", "DON!!: x10", "Character:"]
        lines += [f"{q} Card {cid}" for cid, q in counts.items()]
        self.decklist = Path(self.tmp.name) / "deck.txt"
        self.decklist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue(), err.getvalue()


class DeckCommandTests(CliTestCase):
    def test_import_physical(self):
        code, out, _ = self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        self.assertEqual(code, 0)
        self.assertIn("created physical deck 'Red'", out)
        self.assertIn("+51 copies", out)

    def test_import_wishlist_leaves_collection_alone(self):
        code, out, _ = self.run_cli("deck", "import", str(self.decklist), "--name", "Wish", "--wishlist")
        self.assertEqual(code, 0)
        self.assertIn("untouched (wishlist)", out)

    def test_import_defaults_name_to_filename(self):
        code, out, _ = self.run_cli("deck", "import", str(self.decklist))
        self.assertEqual(code, 0)
        self.assertIn("'deck'", out)

    def test_physical_and_wishlist_are_mutually_exclusive(self):
        code, _, err = self.run_cli(
            "deck", "import", str(self.decklist), "--physical", "--wishlist"
        )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", err)

    def test_missing_file_is_a_clean_error(self):
        code, _, err = self.run_cli("deck", "import", "nope.txt")
        self.assertEqual(code, 1)
        self.assertIn("could not read", err)

    def test_name_clash_without_a_console(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = self.run_cli(
                "deck", "import", str(self.decklist), "--name", "Red", "--physical"
            )
        self.assertEqual(code, 1)
        self.assertIn("--on-conflict", err)

    def test_name_clash_prompt_replace(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        with mock.patch("builtins.input", return_value="r"):
            code, out, _ = self.run_cli(
                "deck", "import", str(self.decklist), "--name", "Red", "--physical"
            )
        self.assertEqual(code, 0)
        self.assertIn("replaced", out)

    def test_name_clash_prompt_cancel(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        with mock.patch("builtins.input", return_value="c"):
            code, out, _ = self.run_cli(
                "deck", "import", str(self.decklist), "--name", "Red", "--physical"
            )
        self.assertEqual(code, 1)
        self.assertIn("cancelled", out)

    def test_on_conflict_skips_the_prompt(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, out, _ = self.run_cli(
            "deck", "import", str(self.decklist), "--name", "Red",
            "--physical", "--on-conflict", "replace",
        )
        self.assertEqual(code, 0)
        self.assertIn("replaced", out)

    def test_list_empty(self):
        code, out, _ = self.run_cli("deck", "list")
        self.assertEqual(code, 0)
        self.assertIn("no decks yet", out)

    def test_list_and_show(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, out, _ = self.run_cli("deck", "list")
        self.assertIn("Red", out)

        code, out, _ = self.run_cli("deck", "show", "Red")
        self.assertEqual(code, 0)
        self.assertIn("complete", out)
        self.assertIn(LEADER, out)

    def test_show_wishlist_reports_missing(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Wish", "--wishlist")
        code, out, _ = self.run_cli("deck", "show", "Wish")
        self.assertIn("missing 51", out)

    def test_show_unknown_deck(self):
        code, _, err = self.run_cli("deck", "show", "Nope")
        self.assertEqual(code, 1)
        self.assertIn("no deck named", err)

    def test_delete(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, out, _ = self.run_cli("deck", "delete", "Red")
        self.assertEqual(code, 0)
        self.assertIn("back to just-loose", out)

    def test_set_physical_warns(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Wish", "--wishlist")
        code, out, _ = self.run_cli("deck", "set-physical", "Wish")
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)

    def test_set_physical_off(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, out, _ = self.run_cli("deck", "set-physical", "Red", "--off")
        self.assertEqual(code, 0)
        self.assertIn("wishlist", out)


class CollectionCommandTests(CliTestCase):
    def test_add_move_and_remove(self):
        code, out, _ = self.run_cli("collection", "add", "OP05-001", "3")
        self.assertEqual(code, 0)
        self.assertIn("now 3 owned", out)
        self.assertIn("just-loose", out)

        self.run_cli("location", "add", "Binder A", "--kind", "binder")
        code, out, _ = self.run_cli("collection", "move", "OP05-001", "2", "--to", "Binder A")
        self.assertEqual(code, 0)
        self.assertIn("just-loose -> Binder A", out)

        code, out, _ = self.run_cli("collection", "remove", "OP05-001", "1")
        self.assertEqual(code, 0)
        self.assertIn("now 2 owned", out)

    def test_add_with_an_art(self):
        code, out, _ = self.run_cli("collection", "add", "OP05-001", "2", "--art", "p1")
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertIn("OP05-001_p1", out)

    def test_set_art_splits_the_pile(self):
        self.run_cli("collection", "add", "OP05-001", "4")
        code, out, _ = self.run_cli("collection", "set-art", "OP05-001", "1", "p1")
        self.assertEqual(code, 0)
        self.assertIn("3 still unsorted", out)

    def test_taking_more_than_you_have_is_reported_not_raised(self):
        self.run_cli("collection", "add", "OP05-001", "1")
        code, _, err = self.run_cli("collection", "remove", "OP05-001", "4")
        self.assertEqual(code, 1)
        self.assertIn("not 4", err)

    def test_unknown_card(self):
        code, _, err = self.run_cli("collection", "add", "ZZ99-999", "1")
        self.assertEqual(code, 1)
        self.assertIn("unknown card", err)

    def test_unknown_art_lists_the_real_ones(self):
        code, _, err = self.run_cli("collection", "add", "OP05-001", "1", "--art", "zz9")
        self.assertEqual(code, 1)
        self.assertIn("known arts", err)

    def test_show(self):
        self.run_cli("collection", "add", "OP05-001", "3")
        code, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertEqual(code, 0)
        self.assertIn("owned 3", out)
        self.assertIn("unsorted", out)
        self.assertIn("where they are", out)

    def test_show_unknown_card(self):
        code, out, _ = self.run_cli("collection", "show", "ZZ99-999")
        self.assertEqual(code, 1)
        self.assertIn("no card with id", out)

    def test_list_filters(self):
        self.run_cli("collection", "add", "OP05-001", "2")
        code, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertEqual(code, 0)
        self.assertIn("OP05-001", out)
        self.assertIn("2 printing(s)", out)      # base and its parallel

    def test_list_by_category(self):
        code, out, _ = self.run_cli("collection", "list", "--category", "Leader")
        self.assertIn(LEADER, out)
        self.assertIn("1 printing(s)", out)

    def test_list_by_name_search(self):
        code, out, _ = self.run_cli("collection", "list", "--search", "Card 1 ")
        self.assertEqual(code, 0)
        self.assertIn("Card 1", out)


class StorageCommandTests(CliTestCase):
    def test_location_lifecycle(self):
        code, out, _ = self.run_cli("location", "add", "Binder A", "--notes", "reds")
        self.assertEqual(code, 0)

        _, out, _ = self.run_cli("location", "list")
        self.assertIn("Binder A", out)
        self.assertIn("reds", out)

        _, out, _ = self.run_cli("location", "rename", "Binder A", "Binder One")
        self.assertIn("renamed", out)

        _, out, _ = self.run_cli("location", "delete", "Binder One")
        self.assertIn("back to just-loose", out)

    def test_location_list_always_has_just_loose(self):
        code, out, _ = self.run_cli("location", "list")
        self.assertEqual(code, 0)
        self.assertIn("just-loose", out)

    def test_just_loose_is_protected(self):
        code, _, err = self.run_cli("location", "delete", "just-loose")
        self.assertEqual(code, 1)
        self.assertIn("built in", err)

    def test_duplicate_location(self):
        self.run_cli("location", "add", "Binder A")
        code, _, err = self.run_cli("location", "add", "Binder A")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_location_show(self):
        self.run_cli("collection", "add", "OP05-001", "2")
        code, out, _ = self.run_cli("location", "show")
        self.assertEqual(code, 0)
        self.assertIn("OP05-001", out)
        self.assertIn("(unsorted)", out)

    def test_location_show_empty(self):
        self.run_cli("location", "add", "Binder A")
        code, out, _ = self.run_cli("location", "show", "Binder A")
        self.assertEqual(code, 0)
        self.assertIn("is empty", out)

    def test_moving_into_an_unknown_container(self):
        self.run_cli("collection", "add", "OP05-001", "1")
        code, _, err = self.run_cli("collection", "move", "OP05-001", "1", "--to", "Nowhere")
        self.assertEqual(code, 1)
        self.assertIn("no location or deck named", err)

    def test_ambiguous_container_asks_for_a_kind(self):
        self.run_cli("location", "add", "Box 1", "--kind", "storage")
        self.run_cli("location", "add", "Box 1", "--kind", "binder")
        self.run_cli("collection", "add", "OP05-001", "1")
        code, _, err = self.run_cli("collection", "move", "OP05-001", "1", "--to", "Box 1")
        self.assertEqual(code, 1)
        self.assertIn("--kind", err)

        code, _, _ = self.run_cli("collection", "move", "OP05-001", "1",
                                  "--to", "Box 1", "--to-kind", "binder")
        self.assertEqual(code, 0)

    def test_loan_keeps_ownership(self):
        self.run_cli("collection", "add", "OP05-001", "2")
        self.run_cli("location", "add", "Marco", "--kind", "loan")
        code, out, _ = self.run_cli("loan", "OP05-001", "1", "Marco")
        self.assertEqual(code, 0)
        self.assertIn("still own them", out)

        _, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertIn("owned 2", out)
        self.assertIn("Marco", out)

    def test_moving_copies_into_a_deck(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--wishlist")
        self.run_cli("collection", "add", "OP05-001", "1")
        code, out, _ = self.run_cli("collection", "move", "OP05-001", "1",
                                    "--to", "Red", "--to-kind", "deck")
        self.assertEqual(code, 0)
        self.assertIn("-> Red", out)


class CliEdgeTests(CliTestCase):
    def test_list_with_no_matches(self):
        code, out, _ = self.run_cli("collection", "list", "--set", "ZZ-99")
        self.assertEqual(code, 0)
        self.assertIn("no cards matched", out)

    def test_keyboard_interrupt_exits_cleanly(self):
        with mock.patch("src.cli.cmd_deck_list", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("deck", "list")
        self.assertEqual(code, 130)
        self.assertIn("cancelled", err)

    def test_moving_copies_back_out_of_a_container(self):
        self.run_cli("collection", "add", "OP05-001", "4")
        self.run_cli("location", "add", "Binder A")
        self.run_cli("collection", "move", "OP05-001", "3", "--to", "Binder A")
        code, out, _ = self.run_cli("collection", "move", "OP05-001", "3",
                                    "--from", "Binder A", "--to", "just-loose")
        self.assertEqual(code, 0)
        self.assertIn("Binder A -> just-loose", out)

        _, out, _ = self.run_cli("location", "show", "Binder A")
        self.assertIn("is empty", out)

    def test_deck_show_by_id(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, out, _ = self.run_cli("deck", "show", "1")
        self.assertEqual(code, 0)
        self.assertIn("Red", out)
