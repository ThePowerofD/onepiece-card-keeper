"""Unit tests for src/sanitize.py — one class per function, edge cases covered.

Run:
    python -m unittest discover tests
    python -m unittest tests.test_sanitize -v
"""

from __future__ import annotations

import unittest

from src.sanitize import (
    detect_keywords,
    extract_printing_variant,
    game_set_from_id,
    normalize_attributes,
    normalize_colors,
    normalize_counter,
    normalize_card_image_id,
    normalize_life,
    normalize_null,
    parse_subtypes,
    printing_kind,
    repair_field_shift,
    strip_printing_suffix,
    to_int,
)


class NormalizeNullTests(unittest.TestCase):
    def test_string_null_likes(self):
        for value in ["NULL", "null", "?", "", "   ", "-", "--"]:
            self.assertIsNone(normalize_null(value), msg=repr(value))

    def test_none(self):
        self.assertIsNone(normalize_null(None))

    def test_real_string_stripped(self):
        self.assertEqual(normalize_null("  Red  "), "Red")

    def test_non_string_passthrough(self):
        self.assertEqual(normalize_null(5), 5)
        self.assertEqual(normalize_null(0), 0)


class ToIntTests(unittest.TestCase):
    def test_valid_ints(self):
        self.assertEqual(to_int("0"), 0)
        self.assertEqual(to_int("5000"), 5000)
        self.assertEqual(to_int(" 3 "), 3)
        self.assertEqual(to_int(10), 10)

    def test_null_likes(self):
        for value in ["NULL", "?", "", "  ", None]:
            self.assertIsNone(to_int(value), msg=repr(value))

    def test_garbage(self):
        self.assertIsNone(to_int("abc"))
        self.assertIsNone(to_int("1.5"))


class NormalizeCounterTests(unittest.TestCase):
    def test_character_keeps_int(self):
        self.assertEqual(normalize_counter("Character", "1000"), 1000)
        self.assertEqual(normalize_counter("Character", 0), 0)

    def test_non_character_forces_null(self):
        self.assertIsNone(normalize_counter("Leader", "1000"))
        self.assertIsNone(normalize_counter("Event", "1000"))
        self.assertIsNone(normalize_counter("Stage", "1000"))

    def test_a_character_with_no_counter_is_zero_not_null(self):
        """D-031: NULL is reserved for categories that can't have one (D-008),
        so 'no counter' and 'not applicable' stay distinguishable."""
        self.assertEqual(normalize_counter("Character", "NULL"), 0)
        self.assertEqual(normalize_counter("Character", "?"), 0)
        self.assertEqual(normalize_counter("Character", None), 0)

    def test_an_unknown_category_is_left_alone(self):
        """Only Characters get the 0 default; a category we don't recognise
        shouldn't have a counter invented for it."""
        self.assertIsNone(normalize_counter(None, "NULL"))
        self.assertIsNone(normalize_counter("Nonsense", "NULL"))


class NormalizeColorsTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(normalize_colors("Red"), "Red")

    def test_space_separated(self):
        self.assertEqual(normalize_colors("Blue Red"), "Blue/Red")
        self.assertEqual(normalize_colors("Red Green Blue"), "Red/Green/Blue")

    def test_already_slashed(self):
        self.assertEqual(normalize_colors("Blue/Red"), "Blue/Red")
        self.assertEqual(normalize_colors("Blue / Red"), "Blue/Red")

    def test_null(self):
        self.assertIsNone(normalize_colors("NULL"))
        self.assertIsNone(normalize_colors(None))


class NormalizeAttributesTests(unittest.TestCase):
    def test_strip_spaces(self):
        self.assertEqual(normalize_attributes("Slash / Special"), "Slash/Special")
        self.assertEqual(normalize_attributes("Strike"), "Strike")

    def test_null(self):
        self.assertIsNone(normalize_attributes(""))

    def test_space_separated_duals(self):
        """The form the API actually uses for most dual attributes."""
        self.assertEqual(normalize_attributes("Slash Strike"), "Slash/Strike")
        self.assertEqual(normalize_attributes("Ranged Strike"), "Ranged/Strike")
        self.assertEqual(normalize_attributes("Special Wisdom"), "Special/Wisdom")

    def test_single_values_survive(self):
        for value in ["Slash", "Strike", "Ranged", "Special", "Wisdom"]:
            self.assertEqual(normalize_attributes(value), value)

    def test_casing_is_canonicalised(self):
        self.assertEqual(normalize_attributes("slash strike"), "Slash/Strike")
        self.assertEqual(normalize_attributes("SLASH"), "Slash")

    def test_junk_the_api_actually_serves(self):
        """Every non-attribute value present in the 4,459 cached rows."""
        self.assertIsNone(normalize_attributes("N/A"))          # 2 reprint stages
        self.assertIsNone(normalize_attributes("?"))            # Imu, 2 printings
        self.assertIsNone(normalize_attributes("Straw Hat Crew ODYSSEY"))  # OP09-033

    def test_unknown_tokens_are_dropped_not_kept(self):
        self.assertEqual(normalize_attributes("Slash Nonsense"), "Slash")
        self.assertIsNone(normalize_attributes("Nonsense Entirely"))

    def test_duplicates_collapse(self):
        self.assertEqual(normalize_attributes("Slash Slash"), "Slash")


class NormalizeLifeTests(unittest.TestCase):
    def test_leaders_keep_life(self):
        self.assertEqual(normalize_life("Leader", "5"), 5)
        self.assertEqual(normalize_life("Leader", 4), 4)

    def test_everything_else_is_none(self):
        for category in ["Character", "Event", "Stage"]:
            self.assertIsNone(normalize_life(category, "5"), msg=category)

    def test_null_likes(self):
        self.assertIsNone(normalize_life("Leader", "NULL"))
        self.assertIsNone(normalize_life("Leader", "?"))
        self.assertIsNone(normalize_life(None, "5"))


class ParseSubtypesTests(unittest.TestCase):
    KNOWN = [
        "Straw Hat Crew",
        "Marine",
        "Worst Generation",
        "Supernovas",
        "Heart Pirates",
        "Animal Kingdom Pirates",
        "Big Mom Pirates",
    ]

    def test_single_match(self):
        matched, leftover = parse_subtypes("Marine", self.KNOWN)
        self.assertEqual(matched, ["Marine"])
        self.assertEqual(leftover, "")

    def test_multiple_slash_separated(self):
        matched, leftover = parse_subtypes(
            "Straw Hat Crew/Supernovas/Worst Generation", self.KNOWN
        )
        self.assertEqual(set(matched), {"Straw Hat Crew", "Supernovas", "Worst Generation"})
        self.assertEqual(leftover, "")

    def test_longest_match_wins(self):
        matched, _ = parse_subtypes("Heart Pirates", self.KNOWN)
        self.assertEqual(matched, ["Heart Pirates"])

    def test_unknown_leftover(self):
        matched, leftover = parse_subtypes("Marine/Mysterious Faction", self.KNOWN)
        self.assertEqual(matched, ["Marine"])
        self.assertIn("Mysterious", leftover)

    def test_dedupe(self):
        matched, _ = parse_subtypes("Marine/Marine", self.KNOWN)
        self.assertEqual(matched, ["Marine"])

    def test_null_input(self):
        matched, leftover = parse_subtypes("NULL", self.KNOWN)
        self.assertEqual(matched, [])
        self.assertEqual(leftover, "")

    def test_comma_separated(self):
        matched, leftover = parse_subtypes("Marine, Supernovas", self.KNOWN)
        self.assertEqual(set(matched), {"Marine", "Supernovas"})
        self.assertEqual(leftover, "")


class RepairFieldShiftTests(unittest.TestCase):
    def test_healthy_row_untouched(self):
        power, sub_types, repaired = repair_field_shift("5000", "Animal Straw Hat Crew")
        self.assertEqual(power, "5000")
        self.assertEqual(sub_types, "Animal Straw Hat Crew")
        self.assertFalse(repaired)

    def test_healthy_row_with_no_power(self):
        power, sub_types, repaired = repair_field_shift(None, "Straw Hat Crew")
        self.assertIsNone(power)
        self.assertEqual(sub_types, "Straw Hat Crew")
        self.assertFalse(repaired)

    def test_swap_recovers_both_fields(self):
        # EB03-050 Conis: power holds the type text, sub_types holds the power
        power, sub_types, repaired = repair_field_shift("Sky Island", "1000")
        self.assertEqual(power, 1000)
        self.assertEqual(sub_types, "Sky Island")
        self.assertTrue(repaired)

    def test_swap_multiword_type(self):
        # EB03-009 Makino
        power, sub_types, repaired = repair_field_shift("Windmill Village", "2000")
        self.assertEqual(power, 2000)
        self.assertEqual(sub_types, "Windmill Village")
        self.assertTrue(repaired)

    def test_shift_recovers_power_and_drops_lost_subtypes(self):
        # OP08-001 Chopper: sub_types are gone from this row entirely
        power, sub_types, repaired = repair_field_shift("4", "5000")
        self.assertEqual(power, 5000)
        self.assertIsNone(sub_types)
        self.assertTrue(repaired)

    def test_numeric_sub_types_with_missing_power(self):
        power, sub_types, repaired = repair_field_shift(None, "3000")
        self.assertEqual(power, 3000)
        self.assertIsNone(sub_types)
        self.assertTrue(repaired)

    def test_integer_sub_types_are_detected(self):
        power, sub_types, repaired = repair_field_shift("Sky Island", 1000)
        self.assertEqual(power, 1000)
        self.assertEqual(sub_types, "Sky Island")
        self.assertTrue(repaired)

    def test_null_like_sub_types_untouched(self):
        for value in [None, "NULL", "?", "", "-"]:
            power, sub_types, repaired = repair_field_shift("5000", value)
            self.assertEqual(power, "5000", msg=repr(value))
            self.assertEqual(sub_types, value, msg=repr(value))
            self.assertFalse(repaired, msg=repr(value))

    def test_type_containing_a_number_is_not_treated_as_bare(self):
        power, sub_types, repaired = repair_field_shift("5000", "Baroque Works 13")
        self.assertEqual(power, "5000")
        self.assertEqual(sub_types, "Baroque Works 13")
        self.assertFalse(repaired)


class NormalizeCardImageIdTests(unittest.TestCase):
    def test_variant_case_is_folded(self):
        """'OP04-016_P1' and '_p1' are one printing, not two primary keys."""
        from src.sanitize import normalize_card_image_id as norm
        self.assertEqual(norm("OP04-016_P1"), "OP04-016_p1")

    def test_clean_ids_untouched(self):
        for value in ["OP05-097", "OP05-097_p1", "EB03_OP05-006_p1", "P-029"]:
            self.assertEqual(normalize_card_image_id(value), value, msg=repr(value))

    def test_strips_file_extension(self):
        self.assertEqual(normalize_card_image_id("EB02-052_p2.jpg"), "EB02-052_p2")

    def test_folds_hyphen_variant_to_underscore(self):
        self.assertEqual(normalize_card_image_id("OP09-078-r1"), "OP09-078_r1")
        self.assertEqual(normalize_card_image_id("P-089-pr6"), "P-089_pr6")

    def test_plain_card_number_is_not_a_variant(self):
        # 'OP05-097' must not read '-097' as a variant (digits only, no letters)
        self.assertEqual(normalize_card_image_id("OP05-097"), "OP05-097")
        self.assertEqual(normalize_card_image_id("P-029"), "P-029")

    def test_null_likes(self):
        for value in [None, "NULL", "?", "", "  "]:
            self.assertIsNone(normalize_card_image_id(value), msg=repr(value))


class StripPrintingSuffixTests(unittest.TestCase):
    def test_strips_underscore_variant(self):
        self.assertEqual(strip_printing_suffix("P-029_r1"), "P-029")
        self.assertEqual(strip_printing_suffix("OP05-097_p1"), "OP05-097")

    def test_strips_hyphen_variant(self):
        self.assertEqual(strip_printing_suffix("OP09-078-r1"), "OP09-078")

    def test_leaves_base_ids_alone(self):
        for value in ["OP05-097", "P-029", "EB03-050", "ST30-001"]:
            self.assertEqual(strip_printing_suffix(value), value, msg=repr(value))

    def test_null_likes(self):
        for value in [None, "NULL", ""]:
            self.assertIsNone(strip_printing_suffix(value), msg=repr(value))

    def test_every_printing_of_a_card_collapses_to_one_identity(self):
        printings = ["P-029", "P-029_p3", "P-029_p4", "P-029_pr1", "P-029_r1", "P-029_r2"]
        self.assertEqual({strip_printing_suffix(p) for p in printings}, {"P-029"})


class ExtractPrintingVariantTests(unittest.TestCase):
    def test_p1(self):
        self.assertEqual(extract_printing_variant("OP05-097_p1"), "p1")

    def test_p2(self):
        self.assertEqual(extract_printing_variant("OP05-097_p2"), "p2")

    def test_base(self):
        self.assertIsNone(extract_printing_variant("OP05-097"))

    def test_null(self):
        self.assertIsNone(extract_printing_variant(None))
        self.assertIsNone(extract_printing_variant("NULL"))

    def test_promo_card_id(self):
        self.assertEqual(extract_printing_variant("P-001_p1"), "p1")


class DetectKeywordsTests(unittest.TestCase):
    def test_all_false_for_none(self):
        result = detect_keywords(None)
        self.assertTrue(all(v is False for v in result.values()))

    def test_trigger_and_blocker(self):
        result = detect_keywords("[Blocker] Some text. [Trigger] More text.")
        self.assertTrue(result["has_blocker"])
        self.assertTrue(result["has_trigger"])
        self.assertFalse(result["has_rush"])

    def test_double_attack(self):
        self.assertTrue(detect_keywords("[Double Attack] foo")["has_double_attack"])

    def test_banish_and_rush(self):
        result = detect_keywords("[Rush][Banish]")
        self.assertTrue(result["has_rush"])
        self.assertTrue(result["has_banish"])

    def test_case_insensitive(self):
        self.assertTrue(detect_keywords("[trigger]")["has_trigger"])

    def test_vanilla_card(self):
        result = detect_keywords("NULL")
        self.assertTrue(all(v is False for v in result.values()))


if __name__ == "__main__":
    unittest.main()


class GameSetFromIdTests(unittest.TestCase):
    """Rule 13 — the set a card belongs to, from base_card_id (D-028)."""

    def test_main_sets(self):
        self.assertEqual(game_set_from_id("OP05-006"), "OP05")
        self.assertEqual(game_set_from_id("OP16-001"), "OP16")

    def test_starter_extra_and_premium(self):
        self.assertEqual(game_set_from_id("ST30-001"), "ST30")
        self.assertEqual(game_set_from_id("EB04-061"), "EB04")
        self.assertEqual(game_set_from_id("PRB02-001"), "PRB02")

    def test_promos_are_their_own_set(self):
        self.assertEqual(game_set_from_id("P-024"), "P")

    def test_the_sets_the_api_set_code_cannot_name(self):
        """OP14/OP15 exist only as 'OP14-EB04' in set_code; the id knows."""
        self.assertEqual(game_set_from_id("OP14-120"), "OP14")
        self.assertEqual(game_set_from_id("OP15-001"), "OP15")

    def test_null_likes(self):
        for value in [None, "", "NULL", "?"]:
            self.assertIsNone(game_set_from_id(value), msg=repr(value))

    def test_unparseable_returns_none_rather_than_guessing(self):
        self.assertIsNone(game_set_from_id("nonsense"))


class PrintingKindTests(unittest.TestCase):
    """Rule 14 — base / parallel / reprint / promo (D-028)."""

    def test_no_variant_is_the_base_printing(self):
        self.assertEqual(printing_kind(None), "base")
        self.assertEqual(printing_kind(""), "base")

    def test_parallels(self):
        for v in ["p1", "p2", "p8"]:
            self.assertEqual(printing_kind(v), "parallel", msg=v)

    def test_reprints(self):
        for v in ["r1", "r2", "r3"]:
            self.assertEqual(printing_kind(v), "reprint", msg=v)

    def test_promos_beat_parallels_on_prefix_order(self):
        """'pr1' starts with 'p' — testing 'p' first would call it parallel."""
        for v in ["pr1", "pr9", "pr12"]:
            self.assertEqual(printing_kind(v), "promo", msg=v)

    def test_uppercase_variant(self):
        """OP04-016 ships as '_P1' where 691 other parallels use '_p1'."""
        self.assertEqual(printing_kind("P1"), "parallel")

    def test_unknown_suffix_is_unclassified_not_guessed(self):
        self.assertIsNone(printing_kind("zz9"))
