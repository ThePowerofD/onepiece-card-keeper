"""Unit tests for src/deck_parser.py.

Run:
    python -m pytest tests/test_deck_parser.py -v
"""

from __future__ import annotations

import unittest

from src.deck_parser import (
    DeckCard,
    parse_card_line,
    parse_decklist,
    parse_quantity,
    split_header,
)

FULL_LIST = """\
Leader: 1 Monkey D. Luffy OP05-001
DON!!: x10
Character:
4 Monkey D. Luffy OP05-097
3 Roronoa Zoro OP05-020
Event:
2 Gum-Gum Giant OP05-060
Stage:
1 Thousand Sunny OP05-080
"""


class ParseQuantityTests(unittest.TestCase):
    def test_spellings(self):
        for token in ["4", "x4", "4x", "X4", "4X"]:
            self.assertEqual(parse_quantity(token), 4, msg=token)

    def test_whitespace_tolerated(self):
        self.assertEqual(parse_quantity("  3 "), 3)

    def test_rejects_non_numeric(self):
        for token in ["", "abc", "-1", "4.5", "x", "OP05-097"]:
            self.assertIsNone(parse_quantity(token), msg=repr(token))

    def test_rejects_zero(self):
        self.assertIsNone(parse_quantity("0"))


class SplitHeaderTests(unittest.TestCase):
    def test_header_with_content(self):
        self.assertEqual(
            split_header("Leader: 1 Luffy OP05-001"), ("leader", "1 Luffy OP05-001")
        )

    def test_standalone_header(self):
        self.assertEqual(split_header("Character:"), ("character", ""))

    def test_case_insensitive(self):
        self.assertEqual(split_header("EVENT:"), ("event", ""))

    def test_non_header_line_passes_through(self):
        self.assertEqual(split_header("4 Luffy OP05-097"), (None, "4 Luffy OP05-097"))

    def test_colon_in_a_card_name_is_not_a_header(self):
        line = "1 Gum-Gum: Giant OP05-060"
        self.assertEqual(split_header(line), (None, line))


class ParseCardLineTests(unittest.TestCase):
    def test_simple_line(self):
        card, reason = parse_card_line("4 Monkey D. Luffy OP05-097")
        self.assertIsNone(reason)
        self.assertEqual(card, DeckCard("OP05-097", 4, "Monkey D. Luffy"))

    def test_printing_suffix_reduced_to_gameplay_identity(self):
        # Decks are gameplay-level (D-003)
        card, _ = parse_card_line("4 Monkey D. Luffy OP05-097_p1")
        self.assertEqual(card.base_card_id, "OP05-097")

    def test_hyphen_style_suffix_also_reduced(self):
        card, _ = parse_card_line("1 Gum-Gum Giant OP09-078-r1")
        self.assertEqual(card.base_card_id, "OP09-078")

    def test_promo_id(self):
        card, _ = parse_card_line("2 Bartolomeo P-029")
        self.assertEqual(card, DeckCard("P-029", 2, "Bartolomeo"))

    def test_name_containing_digits_and_punctuation(self):
        card, _ = parse_card_line("1 Monkey.D.Luffy (041) P-041")
        self.assertEqual(card, DeckCard("P-041", 1, "Monkey.D.Luffy (041)"))

    def test_missing_name_still_parses(self):
        card, reason = parse_card_line("4 OP05-097")
        self.assertIsNone(reason)
        self.assertEqual(card, DeckCard("OP05-097", 4, ""))

    def test_bad_quantity_reports_reason(self):
        card, reason = parse_card_line("many Luffy OP05-097")
        self.assertIsNone(card)
        self.assertIn("quantity", reason)

    def test_bad_card_id_reports_reason(self):
        card, reason = parse_card_line("4 Luffy NOTANID")
        self.assertIsNone(card)
        self.assertIn("card id", reason)

    def test_too_short_reports_reason(self):
        card, reason = parse_card_line("4")
        self.assertIsNone(card)
        self.assertIsNotNone(reason)


class ParseDecklistTests(unittest.TestCase):
    def test_full_list(self):
        deck = parse_decklist(FULL_LIST)
        self.assertEqual(deck.leader_id, "OP05-001")
        self.assertEqual(
            deck.cards,
            [
                DeckCard("OP05-097", 4, "Monkey D. Luffy"),
                DeckCard("OP05-020", 3, "Roronoa Zoro"),
                DeckCard("OP05-060", 2, "Gum-Gum Giant"),
                DeckCard("OP05-080", 1, "Thousand Sunny"),
            ],
        )
        self.assertEqual(deck.warnings, [])

    def test_don_line_is_ignored(self):
        deck = parse_decklist(FULL_LIST)
        self.assertNotIn("DON", " ".join(c.base_card_id for c in deck.cards))
        self.assertEqual(sum(c.quantity for c in deck.cards), 10)

    def test_don_line_does_not_swallow_following_cards(self):
        """A DON line must not leave the parser stuck in a DON section."""
        deck = parse_decklist("Leader: 1 Luffy OP05-001\nDON!!: x10\n4 Zoro OP05-020\n")
        self.assertEqual(deck.cards, [DeckCard("OP05-020", 4, "Zoro")])

    def test_standalone_don_header_skips_until_next_section(self):
        text = "DON!!:\nx10\nCharacter:\n4 Zoro OP05-020\n"
        deck = parse_decklist(text)
        self.assertEqual(deck.cards, [DeckCard("OP05-020", 4, "Zoro")])

    def test_headerless_list(self):
        deck = parse_decklist("4 Luffy OP05-097\n3 Zoro OP05-020\n")
        self.assertIsNone(deck.leader_id)
        self.assertEqual(len(deck.cards), 2)
        self.assertIn("no leader", " ".join(deck.warnings))

    def test_leader_is_not_in_the_cards_list(self):
        deck = parse_decklist(FULL_LIST)
        self.assertNotIn("OP05-001", [c.base_card_id for c in deck.cards])

    def test_duplicate_ids_are_summed(self):
        text = "Character:\n2 Luffy OP05-097\n2 Luffy OP05-097\n"
        deck = parse_decklist(text)
        self.assertEqual(deck.cards, [DeckCard("OP05-097", 4, "Luffy")])

    def test_alt_art_and_base_printing_merge_into_one_entry(self):
        text = "Character:\n2 Luffy OP05-097\n2 Luffy OP05-097_p1\n"
        deck = parse_decklist(text)
        self.assertEqual(deck.cards, [DeckCard("OP05-097", 4, "Luffy")])

    def test_blank_lines_and_whitespace_tolerated(self):
        text = "\n\n  Leader: 1 Luffy OP05-001  \n\n   \n  4 Zoro OP05-020  \n\n"
        deck = parse_decklist(text)
        self.assertEqual(deck.leader_id, "OP05-001")
        self.assertEqual(deck.cards, [DeckCard("OP05-020", 4, "Zoro")])

    def test_empty_string(self):
        deck = parse_decklist("")
        self.assertIsNone(deck.leader_id)
        self.assertEqual(deck.cards, [])
        self.assertIn("no leader", " ".join(deck.warnings))

    def test_malformed_line_warns_without_losing_the_rest(self):
        text = "Leader: 1 Luffy OP05-001\nCharacter:\n4 Zoro OP05-020\ngarbage line\n2 Nami OP05-030\n"
        deck = parse_decklist(text)
        self.assertEqual(len(deck.cards), 2)
        self.assertEqual(len(deck.warnings), 1)
        self.assertIn("line 4", deck.warnings[0])

    def test_second_leader_warns_and_keeps_the_first(self):
        text = "Leader: 1 Luffy OP05-001\nLeader: 1 Zoro OP05-002\n"
        deck = parse_decklist(text)
        self.assertEqual(deck.leader_id, "OP05-001")
        self.assertIn("extra leader", " ".join(deck.warnings))

    def test_never_raises_on_hostile_input(self):
        for text in ["::::", "Leader:", "\x00\x01", "1", ":" * 500, "Character:\n" * 50]:
            parse_decklist(text)  # must not raise


if __name__ == "__main__":
    unittest.main()
