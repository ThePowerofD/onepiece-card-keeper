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
        self.assertIn("collection is unchanged", out)

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
    def test_add_set_remove(self):
        code, out, _ = self.run_cli("collection", "add", "OP05-001", "3")
        self.assertEqual(code, 0)
        self.assertIn("now 3", out)

        _, out, _ = self.run_cli("collection", "set", "OP05-001", "5")
        self.assertIn("now 5", out)

        _, out, _ = self.run_cli("collection", "remove", "OP05-001", "2")
        self.assertIn("now 3", out)

    def test_guard_is_reported_not_raised(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        code, _, err = self.run_cli("collection", "remove", "OP05-001", "4")
        self.assertEqual(code, 1)
        self.assertIn("physical decks", err)

    def test_unknown_printing(self):
        code, _, err = self.run_cli("collection", "add", "ZZ99-999", "1")
        self.assertEqual(code, 1)
        self.assertIn("unknown printing", err)

    def test_show(self):
        self.run_cli("collection", "add", "OP05-001", "3")
        code, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertEqual(code, 0)
        self.assertIn("owned 3", out)
        self.assertIn("unplaced", out)

    def test_show_unknown_card(self):
        code, out, _ = self.run_cli("collection", "show", "ZZ99-999")
        self.assertEqual(code, 1)
        self.assertIn("no card with id", out)

    def test_list_filters(self):
        self.run_cli("collection", "add", "OP05-001", "2")
        code, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertEqual(code, 0)
        self.assertIn("OP05-001", out)
        self.assertIn("1 card(s)", out)

    def test_list_by_category(self):
        code, out, _ = self.run_cli("collection", "list", "--category", "Leader")
        self.assertIn(LEADER, out)
        self.assertIn("1 card(s)", out)


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
        self.assertIn("still own the cards", out)

    def test_location_list_empty(self):
        code, out, _ = self.run_cli("location", "list")
        self.assertIn("no locations yet", out)

    def test_duplicate_location(self):
        self.run_cli("location", "add", "Binder A")
        code, _, err = self.run_cli("location", "add", "Binder A")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_place_and_unplace(self):
        self.run_cli("collection", "add", "OP05-001", "4")
        self.run_cli("location", "add", "Binder A")

        code, out, _ = self.run_cli("place", "OP05-001", "Binder A", "2")
        self.assertEqual(code, 0)
        self.assertNotIn("warning", out)

        code, out, _ = self.run_cli("unplace", "OP05-001", "Binder A", "1")
        self.assertEqual(code, 0)
        self.assertIn(": 1", out)

    def test_place_more_than_owned_warns_but_succeeds(self):
        self.run_cli("collection", "add", "OP05-001", "1")
        self.run_cli("location", "add", "Binder A")
        code, out, _ = self.run_cli("place", "OP05-001", "Binder A", "9")
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)

    def test_place_into_unknown_location(self):
        code, _, err = self.run_cli("place", "OP05-001", "Nowhere", "1")
        self.assertEqual(code, 1)
        self.assertIn("no storage location", err)

    def test_over_placement_shows_a_readable_summary(self):
        """A negative 'unplaced' must not leak into the output."""
        # 4 owned and all 4 sleeved, then 2 more bought -> only 2 are loose.
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--physical")
        self.run_cli("collection", "add", "OP05-001", "2")
        self.run_cli("location", "add", "Binder A")
        self.run_cli("place", "OP05-001", "Binder A", "4")  # claims 4 loose, only 2 are

        code, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertEqual(code, 0)
        self.assertNotIn("unplaced -", out)
        self.assertIn("more copies placed in locations than are loose", out)


if __name__ == "__main__":
    unittest.main()
