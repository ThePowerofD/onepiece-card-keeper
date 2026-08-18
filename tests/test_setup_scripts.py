"""Tests for src/db_setup.py and src/known_types.py, including their CLIs.

Run:
    python -m pytest tests/test_setup_scripts.py -v
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from src import db_setup, known_types
from src.db_setup import TABLE_NAMES, VIEW_NAMES, connect, create_schema, drop_all
from src.known_types import SEED_TYPES, list_known_types, seed_known_types


class SchemaCreationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_creates_every_declared_table_and_view(self):
        create_schema(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        for table in TABLE_NAMES:
            self.assertIn(table, names)
        for view in VIEW_NAMES:
            self.assertIn(view, names)

    def test_idempotent(self):
        create_schema(self.conn)
        create_schema(self.conn)  # must not raise

    def test_drop_all_removes_tables_and_views(self):
        create_schema(self.conn)
        drop_all(self.conn)
        remaining = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        } - {"sqlite_sequence"}
        self.assertEqual(remaining, set())

    def test_drop_all_on_an_empty_database_is_safe(self):
        drop_all(self.conn)  # must not raise

    def test_drop_order_survives_foreign_keys(self):
        """Children must drop before parents or a reset fails with FKs on."""
        self.conn.execute("PRAGMA foreign_keys = ON")
        create_schema(self.conn)
        drop_all(self.conn)
        create_schema(self.conn)

    def test_indexes_created(self):
        create_schema(self.conn)
        indexes = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
            )
        }
        self.assertEqual(len(indexes), 13)


class ConnectTests(unittest.TestCase):
    def test_creates_the_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "dir" / "optcg.db"
            conn = connect(db_path)
            try:
                self.assertTrue(db_path.parent.exists())
            finally:
                conn.close()

    def test_foreign_keys_are_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "x.db")
            try:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                conn.close()


class DbSetupCliTests(unittest.TestCase):
    def run_main(self, *argv) -> str:
        out = io.StringIO()
        with mock.patch("sys.argv", ["db_setup", *argv]), redirect_stdout(out):
            db_setup.main()
        return out.getvalue()

    def test_creates_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = self.run_main("--db", str(Path(tmp) / "x.db"))
        self.assertIn("Tables present:", output)
        self.assertIn("available_cards", output)

    def test_reset_drops_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "x.db")
            self.run_main("--db", db)
            output = self.run_main("--db", db, "--reset")
        self.assertIn("Dropping existing tables", output)

    def test_reset_clears_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.db"
            self.run_main("--db", str(db))

            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO cards (card_image_id, base_card_id, name, category) "
                "VALUES ('X-001', 'X-001', 'Test', 'Character')"
            )
            conn.commit()
            conn.close()

            self.run_main("--db", str(db), "--reset")

            conn = sqlite3.connect(db)
            count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            conn.close()
        self.assertEqual(count, 0)


class SeedKnownTypesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        create_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_seeds_everything(self):
        added, present = seed_known_types(self.conn, SEED_TYPES)
        self.assertEqual(added, len(SEED_TYPES))
        self.assertEqual(present, 0)

    def test_second_run_adds_nothing(self):
        seed_known_types(self.conn, SEED_TYPES)
        added, present = seed_known_types(self.conn, SEED_TYPES)
        self.assertEqual(added, 0)
        self.assertEqual(present, len(SEED_TYPES))

    def test_list_is_sorted(self):
        seed_known_types(self.conn, ["Navy", "Admiral", "Marine"])
        self.assertEqual(list_known_types(self.conn), ["Admiral", "Marine", "Navy"])

    def test_seed_list_has_no_duplicates(self):
        self.assertEqual(len(SEED_TYPES), len(set(SEED_TYPES)))

    def test_seed_list_has_no_blank_entries(self):
        self.assertTrue(all(t and t.strip() == t for t in SEED_TYPES))

    def test_admiral_is_seeded(self):
        """D-015 - OP16's 'Navy Admiral' resolves to Navy + Admiral."""
        self.assertIn("Admiral", SEED_TYPES)
        self.assertIn("Navy", SEED_TYPES)

    def test_compound_navy_types_present(self):
        for t in ["Navy", "Neo Navy", "Former Navy", "Happo Navy"]:
            self.assertIn(t, SEED_TYPES, msg=t)


class KnownTypesCliTests(unittest.TestCase):
    def test_main_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "x.db")
            with mock.patch("sys.argv", ["db_setup", "--db", db]), redirect_stdout(io.StringIO()):
                db_setup.main()

            out = io.StringIO()
            with mock.patch("sys.argv", ["known_types", "--db", db]), redirect_stdout(out):
                known_types.main()
            first = out.getvalue()

            out = io.StringIO()
            with mock.patch("sys.argv", ["known_types", "--db", db]), redirect_stdout(out):
                known_types.main()
            second = out.getvalue()

        self.assertIn(f"{len(SEED_TYPES)} added", first)
        self.assertIn("0 added", second)
        self.assertIn(f"{len(SEED_TYPES)} already present", second)


if __name__ == "__main__":
    unittest.main()
