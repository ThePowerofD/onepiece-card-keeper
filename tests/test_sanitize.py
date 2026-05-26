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
    normalize_attributes,
    normalize_colors,
    normalize_counter,
    normalize_null,
    parse_subtypes,
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

    def test_character_null_value(self):
        self.assertIsNone(normalize_counter("Character", "NULL"))
        self.assertIsNone(normalize_counter("Character", "?"))


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
