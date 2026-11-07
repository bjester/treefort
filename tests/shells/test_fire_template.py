"""Tests for the fire completion template patch helpers."""

from unittest import TestCase

from treefort.errors import CompletionTemplateError
from treefort.shells.fire_template import replace_required, replace_required_regex


class FireTemplateTestCase(TestCase):
    def test_replace_required__replaces_every_occurrence(self):
        self.assertEqual(replace_required("a b a", "a", "x"), "x b x")

    def test_replace_required__honors_count(self):
        self.assertEqual(replace_required("a b a", "a", "x", 1), "x b a")

    def test_replace_required__raises_when_anchor_is_missing(self):
        with self.assertRaisesRegex(CompletionTemplateError, "expected 'gone'"):
            replace_required("a b a", "gone", "x")

    def test_replace_required_regex__replaces_matching_block(self):
        script = "keep\nfunction dead\n    body\nend\n\nkeep"

        self.assertEqual(
            replace_required_regex(script, r"function dead\n.*?\nend\n\n", ""), "keep\nkeep"
        )

    def test_replace_required_regex__raises_when_pattern_is_missing(self):
        with self.assertRaisesRegex(CompletionTemplateError, "function dead"):
            replace_required_regex("keep", r"function dead\n.*?\nend\n\n", "")
