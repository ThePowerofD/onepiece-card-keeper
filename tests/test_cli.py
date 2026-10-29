"""Smoke tests for optcg/cli.py.

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

from optcg.cli import main
from optcg.db_setup import create_schema

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
        code, out, _ = self.run_cli("deck", "delete", "Red", "--yes")
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


class DeckPasteTests(CliTestCase):
    def paste(self, lines, *extra) -> tuple[int, str, str]:
        with mock.patch("builtins.input", side_effect=lines):
            return self.run_cli("deck", "paste", "--name", "Red", *extra)

    def deck_lines(self) -> list[str]:
        return self.decklist.read_text(encoding="utf-8").splitlines()

    def test_paste_ending_with_end(self):
        code, out, _ = self.paste([*self.deck_lines(), "END"])
        self.assertEqual(code, 0)
        self.assertIn("created physical deck 'Red'", out)
        self.assertIn("+51 copies", out)

    def test_paste_ending_with_end_of_input(self):
        code, out, _ = self.paste([*self.deck_lines(), EOFError])
        self.assertEqual(code, 0)
        self.assertIn("+51 copies", out)

    def test_blank_lines_do_not_end_the_paste(self):
        lines = self.deck_lines()
        lines.insert(3, "")
        code, out, _ = self.paste([*lines, "end"])
        self.assertEqual(code, 0)
        self.assertIn("+51 copies", out)

    def test_paste_matches_file_import(self):
        _, pasted, _ = self.paste([*self.deck_lines(), "end"], "--wishlist")
        _, imported, _ = self.run_cli("deck", "import", str(self.decklist),
                                      "--name", "Red", "--wishlist", "--on-conflict", "new")
        self.assertEqual(pasted.splitlines()[-3:], imported.splitlines()[-3:])

    def test_empty_paste_is_a_clean_error(self):
        code, _, err = self.paste(["end"])
        self.assertEqual(code, 1)
        self.assertIn("no recognisable cards", err)

    def test_name_is_required(self):
        with self.assertRaises(SystemExit):
            self.run_cli("deck", "paste")

    def test_name_clash_asks_after_the_paste(self):
        self.paste([*self.deck_lines(), "end"])
        code, out, _ = self.paste([*self.deck_lines(), "end", "r"])
        self.assertEqual(code, 0)
        self.assertIn("replaced", out)


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
        code, _, err = self.run_cli("collection", "show", "ZZ99-999")
        self.assertEqual(code, 1)
        self.assertIn("unknown card 'ZZ99-999'", err)

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
        with mock.patch("optcg.cli.cmd_deck_list", side_effect=KeyboardInterrupt):
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


class ShellTests(CliTestCase):
    def run_shell(self, *lines) -> tuple[int, str, str]:
        """Feed `lines` to the prompt, then end input as Ctrl+Z would."""
        with mock.patch("builtins.input", side_effect=[*lines, EOFError]):
            return self.run_cli("shell")

    def test_runs_commands_until_end_of_input(self):
        code, out, _ = self.run_shell("collection add OP05-001 4", "collection show OP05-001")
        self.assertEqual(code, 0)
        self.assertIn("optcg shell", out)
        _, out, _ = self.run_cli("collection", "show", "OP05-001")
        self.assertIn("4", out)

    def test_exit_stops_reading(self):
        with mock.patch("builtins.input", side_effect=["exit", "collection add OP05-001 4"]):
            code, _, _ = self.run_cli("shell")
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertNotIn("OP05-001", out)

    def test_bad_arguments_do_not_end_the_session(self):
        code, _, err = self.run_shell("collection add OP05-001", "collection add OP05-001 2")
        self.assertEqual(code, 0)
        self.assertIn("required", err)
        _, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertIn("OP05-001", out)

    def test_command_errors_do_not_end_the_session(self):
        code, _, err = self.run_shell("collection remove OP05-001 1", "collection add OP05-002 1")
        self.assertEqual(code, 0)
        self.assertIn("error", err)
        _, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertIn("OP05-002", out)

    def test_quoted_names_and_windows_paths(self):
        windows_path = str(self.decklist).replace("/", "\\")
        code, out, _ = self.run_shell(f'deck import {windows_path} --name "Red Luffy"')
        self.assertEqual(code, 0)
        self.assertIn("'Red Luffy'", out)

    def test_help_blank_lines_and_unclosed_quotes(self):
        code, out, err = self.run_shell("", "help", 'location add "Binder A')
        self.assertEqual(code, 0)
        self.assertIn("collection", out)
        self.assertIn("unclosed quote", err)

    def test_ctrl_c_cancels_the_line_not_the_shell(self):
        with mock.patch("builtins.input",
                        side_effect=[KeyboardInterrupt, "collection add OP05-001 1", EOFError]):
            code, _, _ = self.run_cli("shell")
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("collection", "list", "--owned-only")
        self.assertIn("OP05-001", out)

    def test_deck_paste_inside_the_shell(self):
        code, out, _ = self.run_shell(
            "deck paste --name Red", *self.decklist.read_text(encoding="utf-8").splitlines(),
            "end", "deck list",
        )
        self.assertEqual(code, 0)
        self.assertIn("created physical deck 'Red'", out)

    def test_help_on_a_group_and_a_command(self):
        code, out, _ = self.run_shell("help deck", "help collection set-art")
        self.assertEqual(code, 0)
        self.assertIn("paste", out)
        self.assertIn("examples:", out)
        self.assertIn("collection set-art OP06-042 1 p1", out)

    def test_nested_shell_is_refused(self):
        code, out, _ = self.run_shell("shell")
        self.assertEqual(code, 0)
        self.assertIn("already in the shell", out)


class ConfirmTests(CliTestCase):
    """D-034: ask only when something would actually be lost."""

    def setUp(self):
        super().setUp()
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        self.run_cli("location", "add", "Binder A", "--kind", "binder")

    def deck_count(self) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
        finally:
            conn.close()

    def test_deck_delete_says_what_moves_and_waits_for_yes(self):
        with mock.patch("builtins.input", return_value="y") as prompt:
            code, out, _ = self.run_cli("deck", "delete", "Red")
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        self.assertIn("51 sleeved copies go to just-loose", out)
        self.assertEqual(self.deck_count(), 0)

    def test_anything_but_yes_cancels(self):
        for answer in ("n", "", "nope"):
            with mock.patch("builtins.input", return_value=answer):
                code, out, _ = self.run_cli("deck", "delete", "Red")
            self.assertEqual(code, 1)
            self.assertIn("cancelled", out)
        self.assertEqual(self.deck_count(), 1)

    def test_no_console_means_no_delete(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = self.run_cli("deck", "delete", "Red")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(self.deck_count(), 1)

    def test_wishlist_deck_has_no_copies_to_mention(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Wish", "--wishlist")
        with mock.patch("builtins.input", return_value="y"):
            _, out, _ = self.run_cli("deck", "delete", "Wish")
        self.assertIn("wishlist deck 'Wish'", out)
        self.assertNotIn("sleeved copies", out)

    def test_unknown_deck_errors_before_asking(self):
        with mock.patch("builtins.input") as prompt:
            code, _, err = self.run_cli("deck", "delete", "Nope")
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        self.assertIn("no deck named", err)

    def test_empty_location_is_deleted_without_asking(self):
        with mock.patch("builtins.input") as prompt:
            code, _, _ = self.run_cli("location", "delete", "Binder A")
        self.assertEqual(code, 0)
        prompt.assert_not_called()

    def test_location_with_copies_asks(self):
        self.run_cli("collection", "add", "OP05-001", "3", "--to", "Binder A")
        with mock.patch("builtins.input", return_value="n"):
            code, out, _ = self.run_cli("location", "delete", "Binder A")
        self.assertEqual(code, 1)
        self.assertIn("holds 3 copies of 1 card(s)", out)
        _, out, _ = self.run_cli("location", "list")
        self.assertIn("Binder A", out)

    def test_yes_flag_skips_the_question(self):
        self.run_cli("collection", "add", "OP05-001", "3", "--to", "Binder A")
        with mock.patch("builtins.input") as prompt:
            code, _, _ = self.run_cli("location", "delete", "Binder A", "--yes")
        self.assertEqual(code, 0)
        prompt.assert_not_called()

    def test_just_loose_is_refused_without_asking(self):
        self.run_cli("collection", "add", "OP05-001", "3")
        with mock.patch("builtins.input") as prompt:
            code, _, err = self.run_cli("location", "delete", "just-loose")
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        self.assertIn("built in", err)


class DryRunTests(CliTestCase):
    """D-034: --dry-run on the imports runs against a copy and keeps nothing."""

    def owned(self) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM copies").fetchone()[0]
        finally:
            conn.close()

    def test_deck_import_shows_the_result_and_saves_nothing(self):
        code, out, _ = self.run_cli("deck", "import", str(self.decklist), "--name", "Red", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("+51 copies", out)
        self.assertIn("dry run - nothing was saved", out)
        self.assertEqual(self.owned(), 0)
        _, out, _ = self.run_cli("deck", "list")
        self.assertIn("no decks yet", out)

    def test_deck_paste(self):
        lines = self.decklist.read_text(encoding="utf-8").splitlines()
        with mock.patch("builtins.input", side_effect=[*lines, "end"]):
            code, out, _ = self.run_cli("deck", "paste", "--name", "Red", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("created physical deck", out)
        self.assertEqual(self.owned(), 0)

    def test_replace_is_previewed_too(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        code, out, _ = self.run_cli("deck", "import", str(self.decklist), "--name", "Red",
                                    "--wishlist", "--on-conflict", "replace", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("replaced wishlist deck", out)
        _, out, _ = self.run_cli("deck", "list")
        self.assertIn("physical", out)  # still the real one

    def test_takes_no_snapshot_where_the_real_ones_live(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red",
                     "--on-conflict", "replace", "--dry-run")
        self.assertFalse((self.db.parent / "backups").exists())

    def test_backup_import_does_not_ask(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        export = Path(self.tmp.name) / "move.json"
        self.run_cli("backup", "export", str(export))
        self.run_cli("deck", "delete", "Red", "--yes")
        before = self.owned()

        with mock.patch("builtins.input") as prompt:
            code, out, _ = self.run_cli("backup", "import", str(export), "--dry-run")
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        self.assertIn("imported 1 deck(s)", out)
        _, out, _ = self.run_cli("deck", "list")
        self.assertIn("no decks yet", out)
        self.assertEqual(self.owned(), before)

    def test_a_failing_import_still_says_nothing_was_saved(self):
        code, out, err = self.run_cli("deck", "import", "missing.txt", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("could not read", err)
        self.assertIn("nothing was saved", out)

    def test_not_offered_on_other_commands(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--db", str(self.db), "collection", "add", "OP05-001", "1", "--dry-run"])
        self.assertEqual(self.owned(), 0)


class SetupCommandTests(unittest.TestCase):
    """`optcg setup` and `optcg sync` - one command for what used to be three."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fresh.db"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str]:
        row = {"card_image_id": "OP05-097", "card_set_id": "OP05-097", "card_name": "Luffy",
               "card_type": "Character", "sub_types": "Straw Hat Crew", "set_id": "OP-05"}
        out = io.StringIO()
        with mock.patch("optcg.sync.fetch_all_set_cards", return_value=[row]), \
             mock.patch("optcg.sync.fetch_all_st_cards", return_value=[]), \
             mock.patch("optcg.sync.fetch_all_promos", return_value=[]), \
             redirect_stdout(out):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue()

    def test_setup_builds_a_working_database_from_nothing(self):
        code, out = self.run_cli("setup")
        self.assertEqual(code, 0)
        self.assertIn("cards table count:  1", out)
        code, out = self.run_cli("collection", "add", "OP05-097", "2")
        self.assertEqual(code, 0)

    def test_setup_twice_keeps_your_data(self):
        self.run_cli("setup")
        self.run_cli("collection", "add", "OP05-097", "2")
        self.run_cli("setup")
        _, out = self.run_cli("collection", "show", "OP05-097")
        self.assertIn("owned 2", out)

    def test_sync_learns_new_card_types_by_itself(self):
        self.run_cli("setup")
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM known_types WHERE type_name = 'Straw Hat Crew'")
        conn.commit()
        conn.close()
        _, out = self.run_cli("sync")
        self.assertIn("1 new card type(s) learned", out)
        self.assertIn("Unknown sub_types:  0", out)


class EntryPointTests(CliTestCase):
    def test_no_arguments_opens_the_shell(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("optcg shell", out)

    def test_help_is_written_for_the_optcg_command(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--help"])
        self.assertIn("usage: optcg", out.getvalue())
        self.assertNotIn("src.cli", out.getvalue())


class CardNameTests(CliTestCase):
    """D-037: a card can be named, with a numbered list when the name is shared."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, name, category, color, cost) "
            "VALUES ('OP06-072', 'OP06-072', 'Cosette', 'Character', 'Purple', 2)"
        )
        conn.commit()
        conn.close()

    def owned(self, card) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM copies WHERE base_card_id = ?", (card,)
            ).fetchone()[0]
        finally:
            conn.close()

    def test_lowercase_id(self):
        code, _, _ = self.run_cli("collection", "add", "op05-001", "2")
        self.assertEqual(code, 0)
        self.assertEqual(self.owned("OP05-001"), 2)

    def test_a_unique_name_is_used_and_said(self):
        code, out, _ = self.run_cli("collection", "add", "cosette", "2")
        self.assertEqual(code, 0)
        self.assertIn("'cosette' is OP06-072 Cosette", out)
        self.assertEqual(self.owned("OP06-072"), 2)

    def test_a_shared_name_offers_a_numbered_list(self):
        # 'card 1' matches Card 1 and Card 10 to Card 13: five cards.
        with mock.patch("builtins.input", return_value="2") as prompt:
            code, out, _ = self.run_cli("collection", "add", "card 1", "3")
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        self.assertIn("[1] OP05-001", out)
        self.assertIn("[5] OP05-013", out)
        self.assertEqual(self.owned("OP05-010"), 3)  # the second one listed

    def test_c_cancels(self):
        with mock.patch("builtins.input", return_value="c"):
            code, out, _ = self.run_cli("collection", "add", "card 1", "3")
        self.assertEqual(code, 1)
        self.assertIn("cancelled", out)
        self.assertEqual(self.owned("OP05-001") + self.owned("OP05-010"), 0)

    def test_a_bad_pick_asks_again(self):
        with mock.patch("builtins.input", side_effect=["9", "x", "1"]):
            code, out, _ = self.run_cli("collection", "add", "card 1", "1")
        self.assertEqual(code, 0)
        self.assertIn("type a number from 1 to 5", out)
        self.assertEqual(self.owned("OP05-001"), 1)

    def test_no_console_lists_and_stops(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = self.run_cli("collection", "add", "card 1", "1")
        self.assertEqual(code, 1)
        self.assertIn("use the id", err)

    def test_too_many_matches_asks_for_more_of_the_name(self):
        with mock.patch("builtins.input") as prompt:
            code, out, _ = self.run_cli("collection", "show", "card")
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        self.assertIn("matches 13 cards - type more of the name", out)
        self.assertIn("`find card` lists all 13", out)

    def test_a_typo_suggests_the_right_words(self):
        code, _, err = self.run_cli("collection", "show", "cosete")
        self.assertEqual(code, 1)
        self.assertIn("did you mean 'cosette'?", err)

    def test_every_card_command_accepts_a_name(self):
        self.run_cli("location", "add", "Binder A", "--kind", "binder")
        self.run_cli("location", "add", "Marco", "--kind", "loan")
        for argv in (["collection", "add", "cosette", "4"],
                     ["collection", "move", "cosette", "1", "--to", "Binder A"],
                     ["collection", "set-art", "cosette", "1", "base", "--in", "Binder A"],
                     ["loan", "cosette", "1", "Marco"],
                     ["collection", "remove", "cosette", "1"],
                     ["collection", "show", "cosette"]):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 0, f"{argv}: {err}")
        self.assertEqual(self.owned("OP06-072"), 3)

    def test_find(self):
        self.run_cli("collection", "add", "cosette", "2")
        code, out, _ = self.run_cli("find", "cosette")
        self.assertEqual(code, 0)
        self.assertIn("OP06-072", out)
        self.assertIn("own 2", out)
        self.assertIn("2-cost", out)

    def test_find_takes_several_words_without_quotes(self):
        code, out, _ = self.run_cli("find", "card", "12")
        self.assertEqual(code, 0)
        self.assertIn("OP05-012", out)
        self.assertIn("1 card(s)", out)

    def test_find_by_id(self):
        code, out, _ = self.run_cli("find", "op06-072")
        self.assertEqual(code, 0)
        self.assertIn("Cosette", out)

    def test_find_nothing(self):
        code, out, _ = self.run_cli("find", "zzzz")
        self.assertEqual(code, 1)
        self.assertIn("no card matches", out)


class FriendlyErrorTests(CliTestCase):
    """The audit's fixes: say what to do next, never a traceback."""

    def run_err(self, *argv) -> tuple[int, str]:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                code = main(["--db", str(self.db), *argv])
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_a_database_that_was_never_set_up(self):
        fresh = Path(self.tmp.name) / "never.db"
        for argv in (["deck", "list"], ["find", "nami"], ["collection", "add", "OP05-001", "1"]):
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(["--db", str(fresh), *argv])
            self.assertEqual(code, 1)
            self.assertIn("run `optcg setup` first", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())

    def test_set_up_but_no_cards_downloaded(self):
        empty = Path(self.tmp.name) / "empty.db"
        conn = sqlite3.connect(empty)
        create_schema(conn)
        conn.close()
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(["--db", str(empty), "find", "nami"])
        self.assertEqual(code, 1)
        self.assertIn("run `optcg sync`", err.getvalue())

    def test_locations_work_before_any_cards(self):
        empty = Path(self.tmp.name) / "empty.db"
        conn = sqlite3.connect(empty)
        create_schema(conn)
        conn.close()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(empty), "location", "add", "Binder A"]), 0)

    def test_copies_elsewhere_names_the_flag_to_add(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        code, _, err = self.run_cli("collection", "set-art", "OP05-001", "1", "p1")
        self.assertEqual(code, 1)
        self.assertIn("copies are in: deck 'Red' 4 unsorted", err)
        self.assertIn('add --in "Red"', err)

        self.run_cli("location", "add", "Binder A", "--kind", "binder")
        _, _, err = self.run_cli("collection", "move", "OP05-001", "1", "--to", "Binder A")
        self.assertIn('add --from "Red"', err)

    def test_copies_in_several_places_are_listed_without_guessing(self):
        self.run_cli("deck", "import", str(self.decklist), "--name", "Red")
        self.run_cli("location", "add", "Binder A", "--kind", "binder")
        self.run_cli("collection", "add", "OP05-001", "1", "--to", "Binder A")
        _, _, err = self.run_cli("collection", "remove", "OP05-001", "1")
        self.assertIn("binder 'Binder A'", err)
        self.assertIn("deck 'Red'", err)
        self.assertNotIn("add --from", err)

    def test_a_wrong_command_shows_its_examples_and_more_help(self):
        code, err = self.run_err("collection", "add", "OP05-001")
        self.assertEqual(code, 2)
        self.assertIn("required: quantity", err)
        self.assertIn("examples:", err)
        self.assertIn("collection add OP06-060 2", err)
        self.assertIn("more help: optcg collection add --help", err)

    def test_a_mistyped_group_suggests_the_right_one(self):
        code, err = self.run_err("colection", "add", "OP05-001", "1")
        self.assertEqual(code, 2)
        self.assertIn("did you mean 'collection'?", err)

    def test_a_mistyped_command_suggests_the_right_one(self):
        _, err = self.run_err("collection", "ad", "OP05-001", "1")
        self.assertIn("did you mean 'add'?", err)
        self.assertIn("more help: optcg collection --help", err)

    def test_in_the_shell_more_help_reads_the_shell_way(self):
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=["collection add OP05-001", EOFError]), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            main(["--db", str(self.db), "shell"])
        self.assertIn("more help: help collection add", err.getvalue())

    def test_list_notes_unsorted_copies_once_per_card(self):
        self.run_cli("collection", "add", "OP05-001", "3")
        self.run_cli("collection", "set-art", "OP05-001", "1", "p1")
        _, out, _ = self.run_cli("collection", "list", "--set", "OP05")
        lines = [l for l in out.splitlines() if "OP05-001" in l]
        self.assertEqual(len(lines), 2)  # the base printing and _p1
        self.assertIn("+ 2 of this card, art not sorted yet", lines[0])
        self.assertNotIn("not sorted", lines[1])
        self.assertIn(" 1 ", lines[1])  # one recorded as p1
        self.assertIn("printing", out.splitlines()[0])

