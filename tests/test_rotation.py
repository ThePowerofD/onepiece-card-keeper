"""Unit tests for src/rotation.py (D-027).

SET_BLOCKS is empty by design, so these tests pin the *tri-state* contract
rather than any particular mapping: "unknown" must never read as "illegal".

Run:
    python -m pytest tests/test_rotation.py -v
"""

from __future__ import annotations

import unittest
import unittest.mock

from src import rotation
from src.rotation import ROTATION_LEGAL_FROM, block_for, is_rotation_legal


class BlockForTests(unittest.TestCase):
    def test_unmapped_set_is_none(self):
        self.assertIsNone(block_for("OP-05"))

    def test_missing_set_code_is_none(self):
        self.assertIsNone(block_for(None))
        self.assertIsNone(block_for(""))

    def test_reads_the_mapping_when_populated(self):
        with unittest.mock.patch.dict(rotation.SET_BLOCKS, {"OP-05": 2}, clear=False):
            self.assertEqual(block_for("OP-05"), 2)


class IsRotationLegalTests(unittest.TestCase):
    def test_unknown_block_is_none_not_false(self):
        """The whole collection would read as illegal if this returned False."""
        self.assertIsNone(is_rotation_legal(None))

    def test_block_one_has_rotated_out(self):
        self.assertFalse(is_rotation_legal(1))

    def test_anything_above_block_one_is_legal(self):
        for block in range(ROTATION_LEGAL_FROM, 6):
            self.assertTrue(is_rotation_legal(block), msg=block)


class MappingStateTests(unittest.TestCase):
    def test_mapping_is_still_empty(self):
        """Fails deliberately once someone populates it, as the prompt to
        write the tests that mapping then deserves (D-027)."""
        self.assertEqual(rotation.SET_BLOCKS, {})


if __name__ == "__main__":
    unittest.main()
