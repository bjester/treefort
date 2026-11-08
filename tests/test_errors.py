from unittest import TestCase

from treefort.errors import CLIUsageError, ValidationError


class CLIUsageErrorTestCase(TestCase):
    def test_is_a_validation_error(self):
        self.assertIsInstance(CLIUsageError("boom"), ValidationError)

    def test_defaults_command_name_to_none(self):
        self.assertIsNone(CLIUsageError("boom").command_name)

    def test_keeps_the_command_name(self):
        self.assertEqual("remove", CLIUsageError("boom", command_name="remove").command_name)

    def test_keeps_the_message(self):
        self.assertEqual("boom", str(CLIUsageError("boom")))
