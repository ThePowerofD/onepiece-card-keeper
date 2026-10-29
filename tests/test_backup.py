"""Tests for optcg/backup.py — snapshots, export and import (D-032).

Run:
    python -m pytest tests/test_backup.py -v
"""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from optcg import backup, db_setup
from optcg import collection as coll
from optcg import storage as store
from optcg.backup import (
    AUTO_PREFIX,
    KEEP_AUTO_SNAPSHOTS,
    BackupError,
    auto_snapshot,
    export_collection,
    import_collection,
    parse_export,
    snapshot,
)
from optcg.cli import main
from optcg.db_setup import connect, create_schema
from optcg.decks import delete_deck, import_decklist

LEADER = "OP06-042"
CARDS = [f"OP06-{n:03d}" for n in range(60, 66)]

DECKLIST = "\n".join(
    [f"Leader: 1 Vinsmoke Reiju {LEADER}", "Character:"]
    + [f"4 Card {cid}" for cid in CARDS]
)


def seed_catalog(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO cards (card_image_id, base_card_id, name, category) "
        "VALUES (?, ?, 'Vinsmoke Reiju', 'Leader')",
        (LEADER, LEADER),
    )
    for cid in CARDS:
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, name, category) "
            "VALUES (?, ?, ?, 'Character')",
            (cid, cid, f"Card {cid}"),
        )
        conn.execute(
            "INSERT INTO cards (card_image_id, base_card_id, printing_variant, name, category) "
            "VALUES (?, ?, 'p1', ?, 'Character')",
            (f"{cid}_p1", cid, f"Card {cid} (Parallel)"),
        )
    conn.commit()


def new_db(directory: Path, name: str = "optcg.db", catalog: bool = True) -> sqlite3.Connection:
    conn = connect(directory / name)
    create_schema(conn)
    if catalog:
        seed_catalog(conn)
    return conn


def user_rows(conn: sqlite3.Connection) -> dict:
    """The owner's data, without row ids, for comparing two databases."""
    return {
        "locations": sorted(conn.execute(
            "SELECT name, kind, notes FROM storage_locations").fetchall()),
        "decks": sorted(conn.execute(
            "SELECT name, leader_id, is_physical, notes FROM decks").fetchall()),
        "recipes": sorted(conn.execute(
            "SELECT d.name, dc.base_card_id, dc.quantity FROM deck_cards dc "
            "JOIN decks d ON d.id = dc.deck_id").fetchall()),
        "copies": sorted(conn.execute(
            "SELECT c.base_card_id, COALESCE(c.card_image_id, ''), "
            "       COALESCE(l.name, ''), COALESCE(d.name, ''), c.quantity "
            "FROM copies c LEFT JOIN storage_locations l ON l.id = c.location_id "
            "LEFT JOIN decks d ON d.id = c.deck_id").fetchall()),
    }


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = new_db(self.dir)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def snapshots(self, prefix: str = "") -> list[Path]:
        return sorted((self.dir / "backups").glob(f"{prefix}*.db"))

    def build_collection(self) -> None:
        """A deck with sorted and unsorted copies, a binder, and loose cards."""
        import_decklist(self.conn, DECKLIST, "Reiju", is_physical=True)
        store.add_location(self.conn, "Binder A", "binder", "alt arts")
        binder = store.resolve_container(self.conn, "Binder A")
        coll.add(self.conn, CARDS[0], 2, "p1", binder)
        coll.add(self.conn, CARDS[1], 3, None, binder)
        coll.add(self.conn, CARDS[2], 1)  # just-loose
        deck = store.resolve_container(self.conn, "Reiju", "deck")
        coll.set_art(self.conn, CARDS[3], 1, "p1", deck)


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------

class SnapshotTests(TempDirCase):
    def test_snapshot_is_a_working_copy(self):
        self.build_collection()
        path = snapshot(self.conn, "before sorting")

        self.assertEqual(path.parent, self.dir / "backups")
        self.assertTrue(path.name.startswith("optcg-"))
        self.assertTrue(path.name.endswith("-before-sorting.db"))
        copy = sqlite3.connect(path)
        try:
            self.assertEqual(user_rows(copy), user_rows(self.conn))
        finally:
            copy.close()

    def test_same_second_snapshots_do_not_overwrite(self):
        paths = {snapshot(self.conn) for _ in range(3)}
        self.assertEqual(len(paths), 3)

    def test_auto_snapshot_skips_a_catalog_only_database(self):
        """Nothing there that sync can't rebuild - an empty snapshot would only push out a real one."""
        self.assertIsNone(auto_snapshot(self.conn, "x"))
        self.assertEqual(self.snapshots(), [])

    def test_auto_snapshot_skips_memory_databases(self):
        mem = sqlite3.connect(":memory:")
        create_schema(mem)
        mem.execute("INSERT INTO decks (name) VALUES ('Reiju')")
        self.assertIsNone(auto_snapshot(mem, "x"))
        mem.close()

    def test_a_named_location_counts_as_user_data(self):
        store.add_location(self.conn, "Binder A", "binder")
        self.assertIsNotNone(auto_snapshot(self.conn, "x"))

    def test_prune_keeps_the_newest_automatic_snapshots_only(self):
        self.build_collection()
        manual = snapshot(self.conn, "pre-M0")
        for _ in range(KEEP_AUTO_SNAPSHOTS + 3):
            auto_snapshot(self.conn, "x")

        autos = self.snapshots(AUTO_PREFIX)
        self.assertEqual(len(autos), KEEP_AUTO_SNAPSHOTS)
        self.assertTrue(manual.exists())

    def test_prune_leaves_hand_made_backups_alone(self):
        """The copy made by hand before M0 is named optcg-...; it must survive."""
        self.build_collection()
        backups = self.dir / "backups"
        backups.mkdir()
        hand_made = backups / "optcg-20261028-2345-pre-M0.db"
        hand_made.write_bytes(b"")
        for _ in range(KEEP_AUTO_SNAPSHOTS + 1):
            auto_snapshot(self.conn, "x")
        self.assertTrue(hand_made.exists())

    def test_list_snapshots_marks_automatic_ones(self):
        self.build_collection()
        snapshot(self.conn)
        auto_snapshot(self.conn, "x")
        kinds = sorted(s.automatic for s in backup.list_snapshots(self.conn))
        self.assertEqual(kinds, [False, True])


class AutomaticSnapshotTests(TempDirCase):
    """Every command that destroys the owner's data snapshots first."""

    def setUp(self):
        super().setUp()
        self.build_collection()

    def test_deck_delete(self):
        delete_deck(self.conn, "Reiju")
        self.assertEqual(len(self.snapshots(AUTO_PREFIX)), 1)
        self.assertIn("deck-delete", self.snapshots(AUTO_PREFIX)[0].name)

    def test_snapshot_still_holds_the_deleted_deck(self):
        delete_deck(self.conn, "Reiju")
        copy = sqlite3.connect(self.snapshots(AUTO_PREFIX)[0])
        try:
            self.assertEqual(copy.execute("SELECT name FROM decks").fetchall(), [("Reiju",)])
        finally:
            copy.close()

    def test_location_delete(self):
        store.delete_location(self.conn, "Binder A")
        self.assertIn("location-delete", self.snapshots(AUTO_PREFIX)[0].name)

    def test_deck_replace(self):
        import_decklist(self.conn, DECKLIST, "Reiju", True, on_conflict="replace")
        self.assertIn("deck-replace", self.snapshots(AUTO_PREFIX)[0].name)

    def test_importing_a_new_deck_takes_none(self):
        import_decklist(self.conn, DECKLIST, "Second", True)
        import_decklist(self.conn, DECKLIST, "Reiju", True, on_conflict="new")
        self.assertEqual(self.snapshots(), [])

    def test_a_refused_delete_takes_none(self):
        """A typo in the name mustn't cost one of the ten slots."""
        with self.assertRaises(Exception):
            delete_deck(self.conn, "Reijuu")
        with self.assertRaises(store.StorageError):
            store.delete_location(self.conn, "just-loose")
        self.assertEqual(self.snapshots(), [])

    def test_reset(self):
        self.conn.close()
        out = io.StringIO()
        argv = ["db_setup", "--reset", "--db", str(self.dir / "optcg.db")]
        with mock.patch("sys.argv", argv), redirect_stdout(out):
            db_setup.main()
        self.conn = connect(self.dir / "optcg.db")
        self.assertIn("before-reset", self.snapshots(AUTO_PREFIX)[0].name)
        self.assertIn("Saved your decks and collection", out.getvalue())


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

class ExportTests(TempDirCase):
    def test_copies_sit_inside_their_container(self):
        self.build_collection()
        data = export_collection(self.conn)

        binder = next(loc for loc in data["locations"] if loc["name"] == "Binder A")
        self.assertEqual(binder["kind"], "binder")
        self.assertEqual(binder["notes"], "alt arts")
        self.assertIn(
            {"card": CARDS[0], "art": f"{CARDS[0]}_p1", "quantity": 2,
             "name": f"Card {CARDS[0]}"},
            binder["copies"],
        )

        deck = data["decks"][0]
        self.assertEqual((deck["name"], deck["leader"], deck["physical"]), ("Reiju", LEADER, True))
        self.assertEqual(deck["recipe"][0]["card"], LEADER)  # leader first, as imported
        self.assertEqual(sum(c["quantity"] for c in deck["copies"]), 25)

    def test_uses_the_base_printing_name(self):
        self.build_collection()
        names = {c["name"] for loc in export_collection(self.conn)["locations"]
                 for c in loc["copies"]}
        self.assertNotIn(f"Card {CARDS[0]} (Parallel)", names)

    def test_holds_no_internal_ids(self):
        """Row ids differ between machines; nothing in the file may depend on them."""
        self.build_collection()
        text = json.dumps(export_collection(self.conn))
        for key in ('"id"', '"deck_id"', '"location_id"'):
            self.assertNotIn(key, text)

    def test_write_export_defaults_into_backups(self):
        path = backup.write_export(self.conn)
        self.assertEqual(path.parent, self.dir / "backups")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["format"], "optcg-collection")


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

class ImportTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.other = new_db(self.dir / "other_machine")

    def tearDown(self):
        self.other.close()
        super().tearDown()

    def test_round_trip_to_another_machine(self):
        self.build_collection()
        summary = import_collection(self.other, parse_export(export_collection(self.conn)))

        self.assertEqual(user_rows(self.other), user_rows(self.conn))
        self.assertEqual(summary.decks, 1)
        self.assertEqual(summary.copies, 25 + 6)
        self.assertEqual(summary.warnings, [])

    def test_survives_json_on_disk(self):
        self.build_collection()
        path = backup.write_export(self.conn, self.dir / "move.json")
        import_collection(self.other, backup.read_export(path))
        self.assertEqual(user_rows(self.other), user_rows(self.conn))

    def test_replaces_rather_than_merges(self):
        import_decklist(self.other, DECKLIST, "Old deck", True)
        store.add_location(self.other, "Old box", "booster-box")
        self.build_collection()

        import_collection(self.other, parse_export(export_collection(self.conn)))

        self.assertEqual(user_rows(self.other), user_rows(self.conn))
        self.assertEqual(coll.total_owned(self.other, LEADER), 1)  # not 2

    def test_snapshots_the_data_it_replaces(self):
        import_decklist(self.other, DECKLIST, "Old deck", True)
        import_collection(self.other, parse_export(export_collection(self.conn)))
        autos = sorted((self.dir / "other_machine" / "backups").glob(f"{AUTO_PREFIX}*.db"))
        self.assertEqual(len(autos), 1)
        self.assertIn("before-import", autos[0].name)

    def test_decks_sharing_a_name_keep_their_own_copies(self):
        import_decklist(self.conn, DECKLIST, "Reiju", True)
        import_decklist(self.conn, DECKLIST, "Reiju", True, on_conflict="new")
        import_collection(self.other, parse_export(export_collection(self.conn)))
        counts = self.other.execute(
            "SELECT deck_id, SUM(quantity) FROM copies GROUP BY deck_id").fetchall()
        self.assertEqual([qty for _, qty in counts], [25, 25])

    def test_empty_import_leaves_just_loose(self):
        self.build_collection()
        empty = {"format": "optcg-collection", "version": 1, "locations": [], "decks": []}
        import_collection(self.conn, parse_export(empty))
        self.assertEqual(
            self.conn.execute("SELECT name, kind FROM storage_locations").fetchall(),
            [("just-loose", "loose")],
        )
        self.assertEqual(backup.current_counts(self.conn), (0, 0, 0))

    def test_unknown_art_is_kept_as_unsorted(self):
        """The printing id changed upstream (D-029) - the copies must not be lost."""
        data = self._export_with_binder([
            {"card": CARDS[0], "art": f"{CARDS[0]}_p9", "quantity": 2},
            {"card": CARDS[0], "art": None, "quantity": 1},
        ])
        summary = import_collection(self.other, parse_export(data))

        rows = self.other.execute(
            "SELECT card_image_id, quantity FROM copies WHERE base_card_id = ?", (CARDS[0],)
        ).fetchall()
        self.assertEqual(rows, [(None, 3)])  # merged into one unsorted row
        self.assertIn("_p9 not in the card catalog", summary.warnings[0])

    def test_art_of_another_card_is_kept_as_unsorted(self):
        data = self._export_with_binder([{"card": CARDS[0], "art": f"{CARDS[1]}_p1", "quantity": 1}])
        summary = import_collection(self.other, parse_export(data))
        self.assertEqual(coll.total_owned(self.other, CARDS[0]), 1)
        self.assertIn(f"is not a printing of {CARDS[0]}", summary.warnings[0])

    def test_unknown_card_is_kept_with_a_warning(self):
        """The API lags the game; a card from a set it lacks is still owned."""
        data = self._export_with_binder([{"card": "OP17-001", "art": None, "quantity": 4}])
        summary = import_collection(self.other, parse_export(data))
        self.assertEqual(coll.total_owned(self.other, "OP17-001"), 4)
        self.assertIn("OP17-001", summary.warnings[0])

    def test_custom_storage_types_travel_with_the_export(self):
        store.add_type(self.conn, "deck-box")       # used by a place
        store.add_type(self.conn, "display-case")   # not used yet - must still move
        store.add_location(self.conn, "Red Box", "deck-box")
        import_collection(self.other, parse_export(export_collection(self.conn)))
        types = {name for (name,) in self.other.execute("SELECT name FROM storage_types")}
        self.assertTrue({"deck-box", "display-case"} <= types)
        self.assertIn(("Red Box", "deck-box"), self.other.execute(
            "SELECT name, kind FROM storage_locations").fetchall())

    def test_refused_without_a_card_catalog(self):
        bare = new_db(self.dir / "bare", catalog=False)
        try:
            with self.assertRaisesRegex(BackupError, "run the sync first"):
                import_collection(bare, parse_export(export_collection(self.conn)))
        finally:
            bare.close()

    def _export_with_binder(self, copies: list[dict]) -> dict:
        return {
            "format": "optcg-collection",
            "version": 1,
            "locations": [{"name": "Binder A", "kind": "binder", "copies": copies}],
            "decks": [],
        }


class ParseExportTests(unittest.TestCase):
    def base(self) -> dict:
        return {
            "format": "optcg-collection",
            "version": 1,
            "locations": [{"name": "Binder A", "kind": "binder",
                           "copies": [{"card": CARDS[0], "art": None, "quantity": 2}]}],
            "decks": [{"name": "Reiju", "leader": LEADER, "physical": True,
                       "recipe": [{"card": LEADER, "quantity": 1}], "copies": []}],
        }

    def assertRefused(self, data, pattern: str):
        with self.assertRaisesRegex(BackupError, pattern):
            parse_export(data)

    def test_accepts_a_valid_file(self):
        plan = parse_export(self.base())
        self.assertEqual(plan.copy_total, 2)
        self.assertEqual(plan.location_count, 1)

    def test_refuses_another_kind_of_json(self):
        self.assertRefused({"cards": []}, "not a collection export")
        self.assertRefused([1, 2], "expected an object")

    def test_refuses_a_newer_format(self):
        data = self.base()
        data["version"] = 2
        self.assertRefused(data, "update the code first")

    def test_points_at_the_bad_quantity(self):
        for bad in (0, -1, 1.5, "2", True, None):
            data = self.base()
            data["locations"][0]["copies"][0]["quantity"] = bad
            self.assertRefused(data, r"locations\[0\]\.copies\[0\]\.quantity")

    def test_refuses_an_unknown_kind(self):
        data = self.base()
        data["locations"][0]["kind"] = "deck"
        self.assertRefused(data, "not a storage type")

    def test_refuses_a_location_listed_twice(self):
        data = self.base()
        data["locations"].append(dict(data["locations"][0]))
        self.assertRefused(data, "appears twice")

    def test_refuses_a_card_twice_in_a_recipe(self):
        data = self.base()
        data["decks"][0]["recipe"].append({"card": LEADER, "quantity": 1})
        self.assertRefused(data, "appears twice in the recipe")

    def test_refuses_a_non_boolean_physical(self):
        data = self.base()
        data["decks"][0]["physical"] = "yes"
        self.assertRefused(data, "expected true or false")


class DamagedFileTests(TempDirCase):
    def test_a_bad_file_deletes_nothing(self):
        """Validation runs before the wipe: a broken last entry must not cost the data."""
        self.build_collection()
        before = user_rows(self.conn)
        data = export_collection(self.conn)
        data["decks"][-1]["copies"].append({"card": CARDS[0], "quantity": 0})

        with self.assertRaises(BackupError):
            import_collection(self.conn, parse_export(data))
        self.assertEqual(user_rows(self.conn), before)

    def test_a_failed_write_rolls_everything_back(self):
        self.build_collection()
        before = user_rows(self.conn)
        plan = parse_export(export_collection(self.conn))
        # Fail after the wipe, partway through the inserts.
        with mock.patch.object(backup, "_merge_copies", side_effect=sqlite3.IntegrityError("boom")):
            with self.assertRaises(sqlite3.IntegrityError):
                import_collection(self.conn, plan)
        self.assertEqual(user_rows(self.conn), before)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

class BackupCliTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.build_collection()
        self.db = self.dir / "optcg.db"
        self.export = self.dir / "move.json"

    def run_cli(self, *argv, stdin: str = "") -> tuple[int, str, str]:
        """Empty stdin by default: a prompt nobody answers reads end of input."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out), redirect_stderr(err):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_export_reports_what_it_wrote(self):
        code, out, _ = self.run_cli("backup", "export", str(self.export))
        self.assertEqual(code, 0)
        self.assertIn("1 deck(s), 1 storage place(s), 31 copies", out)
        self.assertTrue(self.export.exists())

    def test_import_asks_first_and_can_be_cancelled(self):
        self.run_cli("backup", "export", str(self.export))
        delete_deck(self.conn, "Reiju")
        before = user_rows(self.conn)

        code, out, _ = self.run_cli("backup", "import", str(self.export), stdin="n\n")
        self.assertEqual(code, 1)
        self.assertIn("REPLACES", out)
        self.assertIn("cancelled", out)
        self.assertEqual(user_rows(self.conn), before)

    def test_import_on_yes(self):
        self.run_cli("backup", "export", str(self.export))
        delete_deck(self.conn, "Reiju")
        code, out, _ = self.run_cli("backup", "import", str(self.export), stdin="y\n")
        self.assertEqual(code, 0)
        self.assertIn("imported 1 deck(s)", out)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 1)

    def test_import_without_a_console_needs_yes(self):
        self.run_cli("backup", "export", str(self.export))
        code, _, err = self.run_cli("backup", "import", str(self.export), stdin="")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)

    def test_import_with_yes_flag_does_not_prompt(self):
        self.run_cli("backup", "export", str(self.export))
        code, out, _ = self.run_cli("backup", "import", str(self.export), "--yes", stdin="")
        self.assertEqual(code, 0)
        self.assertNotIn("REPLACES", out)

    def test_bad_file_is_a_clean_error(self):
        self.export.write_text("{not json", encoding="utf-8")
        code, _, err = self.run_cli("backup", "import", str(self.export), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)

    def test_snapshot_and_list(self):
        code, out, _ = self.run_cli("backup", "snapshot", "--label", "pre M1")
        self.assertEqual(code, 0)
        self.assertIn("pre-M1.db", out)
        code, out, _ = self.run_cli("backup", "list")
        self.assertIn("manual", out)
        self.assertIn("pre-M1.db", out)


if __name__ == "__main__":
    unittest.main()
