from dataclasses import FrozenInstanceError
from unittest import TestCase

from treefort.command import Arg, Command, Completion, command


class CommandDecoratorTestCase(TestCase):
    def test_sets_the_name_and_aliases(self):
        @command("remove", aliases=("rm",))
        class SampleCommand(Command):
            pass

        self.assertEqual("remove", SampleCommand._name)
        self.assertEqual(["rm"], SampleCommand._aliases)

    def test_defaults_to_no_aliases(self):
        @command("prune")
        class SampleCommand(Command):
            pass

        self.assertEqual([], SampleCommand._aliases)

    def test_accepts_a_list_of_aliases(self):
        @command("checkout", aliases=["co"])
        class SampleCommand(Command):
            pass

        self.assertEqual(["co"], SampleCommand._aliases)

    def test_copies_the_alias_list(self):
        aliases = ["co"]

        @command("checkout", aliases=aliases)
        class SampleCommand(Command):
            pass

        aliases.append("chk")

        self.assertEqual(["co"], SampleCommand._aliases)

    def test_gives_each_decorated_class_its_own_alias_list(self):
        @command("remove", aliases=("rm",))
        class FirstCommand(Command):
            pass

        @command("prune")
        class SecondCommand(Command):
            pass

        self.assertIsNot(FirstCommand._aliases, SecondCommand._aliases)

    def test_returns_the_decorated_class(self):
        class SampleCommand(Command):
            pass

        self.assertIs(SampleCommand, command("sample")(SampleCommand))


class ArgTestCase(TestCase):
    def test_defaults_to_no_short_flag_or_completion(self):
        arg = Arg()

        self.assertIsNone(arg.short)
        self.assertIs(Completion.NONE, arg.complete)

    def test_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            Arg().short = "-f"


class CompletionTestCase(TestCase):
    def test_str__returns_the_provider_name(self):
        self.assertEqual("worktree", str(Completion.WORKTREE))

    def test_str__agrees_with_format(self):
        self.assertEqual(str(Completion.WORKTREE_SWITCH), f"{Completion.WORKTREE_SWITCH}")
