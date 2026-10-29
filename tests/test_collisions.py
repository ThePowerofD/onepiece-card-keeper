"""Unit tests for optcg/collisions.py (D-029).

Fixtures are the real shapes the API serves, trimmed to the fields that matter.

Run:
    python -m pytest tests/test_collisions.py -v
"""

from __future__ import annotations

import unittest

from optcg.collisions import (
    core_name,
    filename_id,
    kind_for,
    name_suffix,
    resolve_collisions,
)

IMG = "https://optcgapi.com/media/static/Card_Images/"


def row(cid, name, image, set_id="OP-01", source="set", **fields):
    base = {
        "card_image_id": cid, "card_name": name, "card_image": IMG + image if image else None,
        "set_id": set_id, "rarity": "R", "card_type": "Character", "card_cost": "1",
        "card_power": "2000", "counter_amount": 2000, "card_color": "Red",
        "attribute": "Special", "life": "NULL", "card_text": "text", "sub_types": "Navy",
        "date_scraped": "2026-09-01", "_source": source,
    }
    base.update(fields)
    return base


def ids(rows):
    return [r["card_image_id"] for r in rows]


class FilenameIdTests(unittest.TestCase):
    def test_reads_a_real_printing_id_from_the_image(self):
        self.assertEqual(filename_id(row("x", "n", "OP07-076_r1.jpg")), "OP07-076_r1")

    def test_strips_the_storage_token(self):
        self.assertEqual(filename_id(row("x", "n", "OP04-056_p2_6KicMKv.jpg")), "OP04-056_p2")

    def test_name_based_filenames_are_not_ids(self):
        self.assertIsNone(filename_id(row("x", "n", "Nami_-_OP01-016_Alternate_Art_img.jpg")))

    def test_no_image(self):
        self.assertIsNone(filename_id(row("x", "n", None)))


class NameSuffixTests(unittest.TestCase):
    def test_core_name_drops_id_and_parentheticals(self):
        self.assertEqual(core_name("Roronoa Zoro - OP01-001 (Alternate Art)"), "Roronoa Zoro")
        self.assertEqual(core_name("Otama (Online Regional 2023) [Winner]"), "Otama")

    def test_keeps_only_what_the_duplicate_adds(self):
        self.assertEqual(name_suffix("Nami - OP01-016 (Alternate Art)", "Nami", "OP01-016"),
                         "alternate_art")
        self.assertEqual(name_suffix("Otama (Online Regional 2023) [Winner]", "Otama", "OP01-006"),
                         "online_regional_2023_winner")

    def test_base_row_with_its_own_parenthetical(self):
        """Both rows claim _p2; stripping the base's *full* name would leave the core."""
        self.assertEqual(
            name_suffix("Gum-Gum Red Roc (Jolly Roger Foil)",
                        "Gum-Gum Red Roc (Alternate Art)", "OP04-056_p2"),
            "jolly_roger_foil",
        )

    def test_empty_when_nothing_is_added(self):
        self.assertEqual(name_suffix("Alvida", "Alvida", "ST25-001"), "")


class KindForTests(unittest.TestCase):
    def test_reprint_by_name(self):
        self.assertEqual(kind_for(row("x", "Koala (Reprint)", None)), "reprint")

    def test_promo_by_feed_or_wording(self):
        self.assertEqual(kind_for(row("x", "Nami (Alternate Art)", None, source="promos")), "promo")
        self.assertEqual(kind_for(row("x", "Otama (Regional 2023)", None)), "promo")

    def test_parallel_otherwise(self):
        self.assertEqual(kind_for(row("x", "Nami (Jolly Roger Foil)", None)), "parallel")


class ResolveCollisionsTests(unittest.TestCase):
    def test_unique_rows_pass_through_untouched(self):
        rows = [row("OP01-016", "Nami", "OP01-016.jpg"), row("OP01-017", "Zoro", "OP01-017.jpg")]
        out, stats = resolve_collisions(rows)
        self.assertEqual(ids(out), ["OP01-016", "OP01-017"])
        self.assertEqual(stats["collisions"], 0)
        self.assertNotIn("_printing_kind", out[0])

    def test_promo_reusing_a_base_id_is_re_identified_not_overwriting(self):
        """The Nami case: the regular card keeps its id and its counter."""
        regular = row("OP01-016", "Nami", "OP01-016.jpg", counter_amount=2000)
        promo = row("OP01-016", "Nami - OP01-016 (Alternate Art)",
                    "Nami_-_OP01-016_Alternate_Art_img.jpg", set_id="OP01",
                    source="promos", counter_amount=1000)
        out, stats = resolve_collisions([regular, promo])  # promo last, as in the feed
        by_id = {r["card_image_id"]: r for r in out}
        self.assertEqual(set(by_id), {"OP01-016", "OP01-016_alternate_art"})
        self.assertEqual(by_id["OP01-016"]["card_name"], "Nami")
        self.assertEqual(by_id["OP01-016"]["counter_amount"], 2000)
        self.assertEqual(stats["collisions"], 1)
        self.assertEqual(stats["by_name"], 1)

    def test_duplicate_inherits_gameplay_fields_but_keeps_its_own_identity(self):
        regular = row("OP01-016", "Nami", "OP01-016.jpg", counter_amount=2000)
        promo = row("OP01-016", "Nami (Alternate Art)", "Nami_alt_img.jpg", set_id="OP01",
                    rarity="PR", source="promos", counter_amount=1000, card_text="old text")
        out, _ = resolve_collisions([regular, promo])
        dup = next(r for r in out if r["card_image_id"] != "OP01-016")
        self.assertEqual(dup["counter_amount"], 2000)        # inherited
        self.assertEqual(dup["card_text"], "text")           # inherited
        self.assertEqual(dup["rarity"], "PR")                # its own
        self.assertEqual(dup["set_id"], "OP01")              # its own
        self.assertEqual(dup["card_image"], IMG + "Nami_alt_img.jpg")
        self.assertEqual(dup["_base_card_id"], "OP01-016")
        self.assertEqual(dup["_printing_variant"], "alternate_art")
        self.assertEqual(dup["_printing_kind"], "promo")

    def test_filename_that_carries_the_real_id_wins_over_a_slug(self):
        rows = [
            row("OP07-076", "Slow-Slow Beam Sword", "OP07-076.jpg", set_id="OP-07"),
            row("OP07-076", "Slow-Slow Beam Sword (Pirate Foil)", "OP07-076_p1.jpg", set_id="PRB-02"),
            row("OP07-076", "Slow-Slow Beam Sword (Reprint)", "OP07-076_r1.jpg", set_id="PRB-02"),
        ]
        out, stats = resolve_collisions(rows)
        self.assertEqual(sorted(ids(out)), ["OP07-076", "OP07-076_p1", "OP07-076_r1"])
        self.assertEqual(stats["by_filename"], 2)
        # A real suffix is left for the ordinary sanitizer rules to parse.
        self.assertNotIn("_printing_kind", next(r for r in out if r["card_image_id"] == "OP07-076_r1"))

    def test_filename_id_is_not_used_if_another_row_already_owns_it(self):
        rows = [
            row("OP07-076", "Sword", "OP07-076.jpg", set_id="OP-07"),
            row("OP07-076", "Sword (Reprint)", "OP07-076_r1.jpg", set_id="PRB-02"),
            row("OP07-076_r1", "Sword (Reprint)", "OP07-076_r1_x.jpg", set_id="PRB-02"),
        ]
        out, _ = resolve_collisions(rows)
        self.assertEqual(len(set(ids(out))), 3)

    def test_both_rows_claiming_the_same_variant(self):
        rows = [
            row("OP04-056_p2", "Gum-Gum Red Roc (Alternate Art)", "OP04-056_p2.jpg", set_id="PRB-01"),
            row("OP04-056_p2", "Gum-Gum Red Roc (Jolly Roger Foil)", "OP04-056_p2_6KicMKv.jpg",
                set_id="PRB-01"),
        ]
        out, _ = resolve_collisions(rows)
        self.assertEqual(sorted(ids(out)), ["OP04-056_p2", "OP04-056_p2_jolly_roger_foil"])
        dup = next(r for r in out if r["card_image_id"].endswith("foil"))
        self.assertEqual(dup["_base_card_id"], "OP04-056")
        self.assertEqual(dup["_printing_variant"], "p2_jolly_roger_foil")
        self.assertEqual(dup["_printing_kind"], "parallel")

    def test_base_choice_is_deterministic_regardless_of_feed_order(self):
        regular = row("OP01-016", "Nami", "OP01-016.jpg")
        promo = row("OP01-016", "Nami (Alternate Art)", "Nami_alt_img.jpg", source="promos")
        a, _ = resolve_collisions([regular, promo])
        b, _ = resolve_collisions([promo, regular])
        base_a = next(r for r in a if r["card_image_id"] == "OP01-016")
        base_b = next(r for r in b if r["card_image_id"] == "OP01-016")
        self.assertEqual(base_a["card_name"], "Nami")
        self.assertEqual(base_b["card_name"], "Nami")

    def test_exact_duplicate_rows_collapse_to_one(self):
        rows = [
            row("ST25-001", "Alvida", "ST25-001.jpg", set_id="ST-25"),
            row("ST25-001", "Alvida", "ST25-001_Ab12Cd3.jpg", set_id="ST-25"),
        ]
        out, stats = resolve_collisions(rows)
        self.assertEqual(ids(out), ["ST25-001"])
        self.assertEqual(stats["exact_duplicates"], 1)
        self.assertEqual(stats["collisions"], 0)

    def test_no_image_duplicate_falls_back_to_name(self):
        rows = [
            row("OP01-001", "Roronoa Zoro (001)", "OP01-001.jpg"),
            row("OP01-001", "Roronoa Zoro - OP01-001 (Alternate Art)", None,
                set_id="OP01", source="promos"),
        ]
        out, _ = resolve_collisions(rows)
        self.assertEqual(sorted(ids(out)), ["OP01-001", "OP01-001_alternate_art"])

    def test_set_code_breaks_a_name_tie_and_ordinal_is_the_last_resort(self):
        rows = [
            row("OP01-001", "Zoro", "OP01-001.jpg"),
            row("OP01-001", "Zoro (Foil)", "a_img.jpg", set_id="PRB-01"),
            row("OP01-001", "Zoro (Foil)", "b_img.jpg", set_id="PRB-02"),
            row("OP01-001", "Zoro (Foil)", "c_img.jpg", set_id="PRB-02", rarity="C"),
        ]
        out, stats = resolve_collisions(rows)
        self.assertEqual(len(set(ids(out))), 4)
        self.assertEqual(stats["by_name"], 1)
        self.assertEqual(stats["by_name_and_set"], 1)
        self.assertEqual(stats["by_ordinal"], 1)

    def test_rows_without_an_id_pass_through_for_sync_to_log(self):
        out, _ = resolve_collisions([row(None, "Ghost", None)])
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["card_image_id"])


if __name__ == "__main__":
    unittest.main()
