"""Tests for src/sync.py — the fetch/sanitize/upsert orchestrator.

Covers the parts that actually decide what lands in `cards`: field aliasing,
row sanitization, the log-don't-crash contract, and idempotency.

Run:
    python -m pytest tests/test_sync.py -v
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from src.db_setup import create_schema
from src.known_types import SEED_TYPES, seed_known_types
from src.sync import FIELD_ALIASES, pick, sanitize_row, sync

NOW = "2026-08-11T00:00:00+00:00"


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    seed_known_types(conn, SEED_TYPES)
    return conn


def api_row(**overrides) -> dict:
    """A row shaped like a real OptcgAPI response."""
    row = {
        "card_image_id": "OP05-097",
        "card_set_id": "OP05-097",
        "card_name": "Monkey D. Luffy",
        "card_type": "Character",
        "card_cost": "4",
        "card_power": "5000",
        "counter_amount": "1000",
        "card_color": "Red",
        "sub_types": "Straw Hat Crew",
        "card_text": "[Rush] This card can attack on the turn it is played.",
        "set_id": "OP-05",
        "rarity": "SR",
        "card_image": "https://optcgapi.com/media/OP05-097.jpg",
    }
    row.update(overrides)
    return row


class FieldAliasTests(unittest.TestCase):
    """The API's field names differ from the column names (D-012)."""

    def test_every_internal_key_has_an_alias(self):
        for key, aliases in FIELD_ALIASES.items():
            self.assertTrue(aliases, msg=f"{key} has no aliases")

    def test_pick_uses_the_api_names(self):
        row = api_row()
        self.assertEqual(pick(row, "name"), "Monkey D. Luffy")
        self.assertEqual(pick(row, "category"), "Character")
        self.assertEqual(pick(row, "cost"), "4")
        self.assertEqual(pick(row, "power"), "5000")
        self.assertEqual(pick(row, "image_url"), "https://optcgapi.com/media/OP05-097.jpg")

    def test_pick_falls_back_through_aliases(self):
        self.assertEqual(pick({"name": "X"}, "name"), "X")
        self.assertEqual(pick({"card_name": "Y"}, "name"), "Y")

    def test_pick_returns_none_when_absent(self):
        self.assertIsNone(pick({}, "name"))


class SanitizeRowTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.known = [r[0] for r in self.conn.execute("SELECT type_name FROM known_types")]

    def tearDown(self):
        self.conn.close()

    def san(self, **overrides):
        return sanitize_row(api_row(**overrides), self.known, NOW)

    def test_maps_a_normal_row(self):
        row = self.san()
        self.assertEqual(row["card_image_id"], "OP05-097")
        self.assertEqual(row["base_card_id"], "OP05-097")
        self.assertIsNone(row["printing_variant"])
        self.assertEqual(row["name"], "Monkey D. Luffy")
        self.assertEqual(row["category"], "Character")
        self.assertEqual(row["cost"], 4)
        self.assertEqual(row["power"], 5000)
        self.assertEqual(row["counter"], 1000)
        self.assertEqual(row["color"], "Red")
        self.assertTrue(row["has_rush"])
        self.assertFalse(row["has_blocker"])

    def test_rows_without_an_id_are_skipped(self):
        for value in [None, "", "NULL", "?"]:
            self.assertIsNone(self.san(card_image_id=value), msg=repr(value))

    def test_printing_variant_extracted(self):
        row = self.san(card_image_id="OP05-097_p1")
        self.assertEqual(row["printing_variant"], "p1")
        self.assertEqual(row["base_card_id"], "OP05-097")

    def test_base_card_id_is_suffix_stripped(self):
        """D-014: a suffixed card_set_id must not split the gameplay identity."""
        row = self.san(card_image_id="P-029_r1", card_set_id="P-029_r1")
        self.assertEqual(row["base_card_id"], "P-029")

    def test_dirty_image_id_is_normalized(self):
        row = self.san(card_image_id="EB02-052_p2.jpg", card_set_id="EB02-052")
        self.assertEqual(row["card_image_id"], "EB02-052_p2")

    def test_hyphen_variant_is_folded(self):
        row = self.san(card_image_id="OP09-078-r1", card_set_id="OP09-078")
        self.assertEqual(row["card_image_id"], "OP09-078_r1")
        self.assertEqual(row["printing_variant"], "r1")

    def test_multicolour_normalized(self):
        self.assertEqual(self.san(card_color="Green Red")["color"], "Green/Red")

    def test_counter_forced_null_for_leaders(self):
        row = self.san(card_type="Leader", counter_amount="1000")
        self.assertIsNone(row["counter"])

    def test_vanilla_card_has_no_effect(self):
        self.assertIsNone(self.san(card_text="NULL")["effect"])

    def test_null_likes_become_none(self):
        row = self.san(card_cost="NULL", card_power="?", rarity="")
        self.assertIsNone(row["cost"])
        self.assertIsNone(row["power"])
        self.assertIsNone(row["rarity"])

    def test_subtypes_matched_against_known_types(self):
        row = self.san(sub_types="Straw Hat Crew")
        self.assertIn("Straw Hat Crew", row["_matched_types"])
        self.assertEqual(row["_leftover_types"], "")

    def test_navy_admiral_yields_two_types(self):
        """D-015 - the OP16 case that prompted seeding 'Admiral'."""
        row = self.san(sub_types="Navy Admiral")
        self.assertEqual(sorted(row["_matched_types"]), ["Admiral", "Navy"])
        self.assertEqual(row["_leftover_types"], "")

    def test_longest_match_wins(self):
        row = self.san(sub_types="Neo Navy")
        self.assertEqual(row["_matched_types"], ["Neo Navy"])

    def test_unmatched_subtype_becomes_leftover(self):
        row = self.san(sub_types="Totally Made Up Faction")
        self.assertNotEqual(row["_leftover_types"], "")

    def test_field_shift_is_repaired(self):
        """D-013 - EB03-050 Conis had power and sub_types swapped."""
        row = self.san(card_image_id="EB03-050", card_set_id="EB03-050",
                       card_power="Sky Island", sub_types="1000")
        self.assertEqual(row["power"], 1000)
        self.assertEqual(row["card_type"], "Sky Island")
        self.assertTrue(row["_repaired"])

    def test_healthy_rows_are_not_flagged_as_repaired(self):
        self.assertFalse(self.san()["_repaired"])


class SyncTests(unittest.TestCase):
    """sync() with the network stubbed out."""

    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def run_sync(self, set_cards=None, st_cards=None, promos=None):
        with mock.patch("src.sync.fetch_all_set_cards", return_value=set_cards or []), \
             mock.patch("src.sync.fetch_all_st_cards", return_value=st_cards or []), \
             mock.patch("src.sync.fetch_all_promos", return_value=promos or []):
            return sync(self.conn)

    def test_inserts_cards_and_types(self):
        stats = self.run_sync(set_cards=[api_row()])
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)
        types = [r[0] for r in self.conn.execute("SELECT type_name FROM card_types")]
        self.assertEqual(types, ["Straw Hat Crew"])

    def test_merges_all_three_endpoints(self):
        stats = self.run_sync(
            set_cards=[api_row(card_image_id="OP05-097", card_set_id="OP05-097")],
            st_cards=[api_row(card_image_id="ST01-001", card_set_id="ST01-001")],
            promos=[api_row(card_image_id="P-029", card_set_id="P-029")],
        )
        self.assertEqual(stats["total"], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 3)

    def test_duplicate_printings_across_endpoints_collapse(self):
        """The same printing legitimately appears in more than one endpoint (D-012)."""
        stats = self.run_sync(set_cards=[api_row()], st_cards=[api_row()])
        self.assertEqual(stats["total"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)

    def test_running_twice_does_not_duplicate(self):
        self.run_sync(set_cards=[api_row()])
        self.run_sync(set_cards=[api_row()])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_types").fetchone()[0], 1)

    def test_bad_rows_are_logged_not_raised(self):
        """D-017 - a sync must never die on third-party data."""
        stats = self.run_sync(set_cards=[api_row(), api_row(card_image_id=None)])
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["inserted"], 1)
        logged = self.conn.execute(
            "SELECT reason FROM skipped_cards_log"
        ).fetchone()[0]
        self.assertIn("card_image_id", logged)

    def test_unknown_subtypes_are_logged(self):
        stats = self.run_sync(set_cards=[api_row(sub_types="Nonexistent Faction")])
        self.assertEqual(stats["unknown_types"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM unknown_type_log").fetchone()[0], 1
        )

    def test_repairs_are_counted(self):
        stats = self.run_sync(set_cards=[
            api_row(card_image_id="EB03-050", card_set_id="EB03-050",
                    card_power="Sky Island", sub_types="1000"),
        ])
        self.assertEqual(stats["repaired"], 1)

    def test_empty_fetch_is_not_an_error(self):
        stats = self.run_sync()
        self.assertEqual(stats, {"total": 0, "inserted": 0, "skipped": 0,
                                 "unknown_types": 0, "repaired": 0})

    def test_garbage_rows_do_not_crash_the_sync(self):
        rows = [api_row(), {}, {"card_image_id": "X"}, api_row(card_name=None)]
        stats = self.run_sync(set_cards=rows)  # must not raise
        self.assertGreaterEqual(stats["inserted"], 1)

    def test_alt_art_and_base_share_one_gameplay_identity(self):
        self.run_sync(set_cards=[
            api_row(card_image_id="OP05-097"),
            api_row(card_image_id="OP05-097_p1", card_name="Luffy (Parallel)"),
        ])
        bases = {r[0] for r in self.conn.execute("SELECT base_card_id FROM cards")}
        self.assertEqual(bases, {"OP05-097"})
        # card_types is keyed on the gameplay id, so it holds one row not two
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM card_types").fetchone()[0], 1
        )



class SyncCliTests(unittest.TestCase):
    """`python -m src.sync` - the command the README tells you to run."""

    def run_main(self, rows, *argv) -> str:
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from src import db_setup, known_types, sync as sync_module

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "x.db")
            with mock.patch("sys.argv", ["db_setup", "--db", db]), redirect_stdout(io.StringIO()):
                db_setup.main()
            with mock.patch("sys.argv", ["known_types", "--db", db]), redirect_stdout(io.StringIO()):
                known_types.main()

            out = io.StringIO()
            with mock.patch("src.sync.fetch_all_set_cards", return_value=rows), \
                 mock.patch("src.sync.fetch_all_st_cards", return_value=[]), \
                 mock.patch("src.sync.fetch_all_promos", return_value=[]), \
                 mock.patch("sys.argv", ["sync", "--db", db, *argv]), \
                 redirect_stdout(out):
                sync_module.main()
            return out.getvalue()

    def test_prints_a_summary(self):
        output = self.run_main([api_row()])
        self.assertIn("=== Sync summary ===", output)
        self.assertIn("Total rows fetched: 1", output)
        self.assertIn("Inserted/updated:   1", output)
        self.assertIn("cards table count:  1", output)

    def test_summary_reports_skipped_and_repaired(self):
        output = self.run_main([
            api_row(card_image_id=None),
            api_row(card_image_id="EB03-050", card_set_id="EB03-050",
                    card_power="Sky Island", sub_types="1000"),
        ])
        self.assertIn("Skipped (no id):    1", output)
        self.assertIn("Field-shift repairs:1", output)

    def test_use_cache_flag_is_accepted(self):
        output = self.run_main([api_row()], "--use-cache")
        self.assertIn("Sync summary", output)

if __name__ == "__main__":
    unittest.main()
