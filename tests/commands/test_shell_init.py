import logging
import os
import re
import subprocess
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from treefort.commands.shell_init import ShellInitCommand
from treefort.errors import CompletionTemplateError, ValidationError
from treefort.main import WorktreeCommands

# Bash function definitions the generated completion script may contain; anything else
# leaking in from a future fire template fails the namespacing test.
EXPECTED_BASH_FUNCTIONS = {
    "_complete-treefort()",
    "_treefort_get_lastcommand()",
    "_treefort_filter_options()",
    "_treefort_option_already_entered()",
    "_treefort_is_prev_global()",
    "_treefort_list_branch_refs()",
    "_treefort_list_worktrees()",
    "_treefort_complete_worktrees()",
    "_treefort_dynamic_completion()",
}


class ShellInitCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.tmp_path = Path(self.tmp_dir.name)

        # Keep Runtime construction away from the developer's ~/.treefort: run it from the
        # empty temp directory so project and config lookups resolve inside the sandbox.
        original_cwd = Path.cwd()
        env_patcher = patch.dict(os.environ, {"HOME": str(self.tmp_path)})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.chdir(self.tmp_path)
        self.addCleanup(os.chdir, original_cwd)

        self.logging_state = self.snapshot_logging()
        self.addCleanup(self.restore_logging)

        self.cli = WorktreeCommands()
        self.command = ShellInitCommand(self.cli._runtime, self.cli)

    def snapshot_logging(self) -> tuple[list[logging.Handler], int]:
        root = logging.getLogger()
        return list(root.handlers), root.level

    def restore_logging(self):
        handlers, level = self.logging_state
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)

    def run_command(self, *args, **kwargs) -> str:
        stdout = StringIO()
        with redirect_stdout(stdout):
            self.command(*args, **kwargs)
        return stdout.getvalue()

    def test_call__emits_bash_wrapper_and_completion(self):
        output = self.run_command("bash")

        self.assertIn("treefort() {", output)
        self.assertIn('TREEFORT_CD_FILE="$cd_file" command treefort "$@"', output)
        self.assertIn('cd -- "$(<"$cd_file")"', output)
        self.assertIn('rm -f "$cd_file"', output)
        self.assertIn("complete -F _complete-treefort treefort", output)
        self.assertIn("_treefort_dynamic_completion", output)

    def test_call__bash_output_passes_shell_syntax_check(self):
        output = self.run_command("bash")

        result = subprocess.run(["bash", "-n"], input=output, text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_call__zsh_output_loads_bashcompinit(self):
        output = self.run_command("zsh")

        self.assertIn("autoload -Uz bashcompinit && bashcompinit", output)
        self.assertIn("treefort() {", output)
        self.assertIn("complete -F _complete-treefort treefort", output)
        self.assertIn("_treefort_dynamic_completion", output)

    def test_call__emits_fish_wrapper_and_completion(self):
        output = self.run_command("fish")

        self.assertIn("function treefort", output)
        self.assertIn("TREEFORT_CD_FILE=$cd_file command treefort $argv", output)
        self.assertIn("cd -- (cat $cd_file)", output)
        self.assertIn("complete -c treefort", output)
        self.assertIn("__treefort_worktree_names", output)
        self.assertIn("__treefort_positional_count", output)
        self.assertIn("__treefort_using_command", output)

    def test_call__defaults_to_bash(self):
        output = self.run_command()

        self.assertIn("treefort() {", output)
        self.assertIn("complete -F _complete-treefort treefort", output)

    def test_call__customizes_cmd_name(self):
        output = self.run_command("bash", cmd="tf")

        self.assertIn("tf() {", output)
        self.assertIn("complete -F _complete-tf tf", output)
        self.assertIn("complete -c tf", self.run_command("fish", cmd="tf"))

    def test_call__leaves_no_placeholders(self):
        for shell in ("bash", "zsh", "fish"):
            self.assertNotIn("__CMD__", self.run_command(shell))
            self.assertNotIn("__HOOK_NAMES__", self.run_command(shell))

    def test_call__rejects_unknown_shell(self):
        with self.assertRaisesRegex(ValidationError, "Unsupported shell"):
            self.run_command("powershell")

    def test_call__rejects_invalid_cmd_name(self):
        for invalid_cmd in ("tree fort", "foo;bar", "$(evil)", "", "treefort\n", "-tf"):
            with self.assertRaisesRegex(ValidationError, "Invalid command name"):
                self.run_command("bash", cmd=invalid_cmd)

    def test_call__excludes_positional_args_from_flag_suggestions(self):
        output = self.run_command("bash")

        for positional_flag in ("--worktree-name", "--branch-or-pr", "--target"):
            self.assertNotIn(positional_flag, output)
        for keyword_flag in ("--yes", "--remote", "--delete-branch", "--shell", "--cmd"):
            self.assertIn(keyword_flag, output)

    def test_call__completes_command_aliases(self):
        output = self.run_command("bash")

        # remove has flags, so its alias gets a case arm of its own; switch has none, so its
        # alias only joins the subcommand suggestions of the main command
        self.assertIn("rm)", output)
        self.assertIn(" sw ", output)

    def test_call__emits_nothing_when_fire_bash_template_changes(self):
        # A fire release that rewords its template must fail loudly at profile load instead of
        # silently shipping a script without the dynamic completion patch. Nothing may reach
        # stdout, or `eval "$(...)"` would install a wrapper that never gets completion.
        stdout = StringIO()
        with patch("treefort.shells.bash.fire_completion._BashScript", return_value="unexpected"):
            with self.assertRaisesRegex(CompletionTemplateError, "fire completion template"):
                with redirect_stdout(stdout):
                    self.command("bash")

        self.assertEqual(stdout.getvalue(), "")

    def test_call__emits_nothing_when_fire_fish_template_changes(self):
        stdout = StringIO()
        with patch("treefort.shells.fish.fire_completion._FishScript", return_value="unexpected"):
            with self.assertRaisesRegex(CompletionTemplateError, "fire completion template"):
                with redirect_stdout(stdout):
                    self.command("fish")

        self.assertEqual(stdout.getvalue(), "")

    def test_call__overrides_fire_completion_helpers(self):
        output = self.run_command("bash")

        # Two definitions each: fire's original plus the override appended after registration,
        # which bash resolves last so --flags complete after positional arguments.
        self.assertEqual(output.count("_treefort_get_lastcommand()"), 2)
        self.assertEqual(output.count("_treefort_is_prev_global()"), 2)
        self.assertGreater(
            output.rindex("_treefort_get_lastcommand()"),
            output.index("complete -F _complete-treefort treefort"),
        )
        self.assertIn("for ((i=1; i < COMP_CWORD; ++i))", output)
        self.assertIn("see BASH_HELPER_OVERRIDES", output)

    def test_call__namespaces_and_closes_the_bash_function_set(self):
        output = self.run_command("bash")

        function_definitions = set(re.findall(r"^[\w.-]+\(\)$", output, re.MULTILINE))
        self.assertEqual(function_definitions, EXPECTED_BASH_FUNCTIONS)
        self.assertIn("_treefort_get_lastcommand", output)
        self.assertIn("_treefort_filter_options", output)

    def test_call__completes_remote_values(self):
        output = self.run_command("bash")

        self.assertIn("git remote 2>/dev/null", output)

    def test_call__completes_branch_refs(self):
        output = self.run_command("bash")

        self.assertIn(
            "git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/ 2>/dev/null",
            output,
        )

    def test_call__completes_worktree_names_and_shortcuts(self):
        output = self.run_command("bash")

        self.assertIn("git worktree list --porcelain 2>/dev/null", output)
        self.assertIn('_treefort_complete_worktrees "- ^ @"', output)
        self.assertIn('_treefort_complete_worktrees "@"', output)

    def test_call__completes_hook_names(self):
        output = self.run_command("bash")

        for hook_name in (
            "pre_init",
            "post_init",
            "pre_checkout",
            "post_checkout",
            "pre_create",
            "post_create",
            "pre_remove",
            "post_remove",
        ):
            self.assertIn(hook_name, output)

    def test_call__fish_completions_use_namespaced_helpers_and_git(self):
        output = self.run_command("fish")

        self.assertIn("function __treefort_using_command", output)
        self.assertNotIn("__fish_using_command", output)
        self.assertIn("git remote 2>/dev/null", output)
        self.assertIn("git for-each-ref", output)
        self.assertIn("git worktree list --porcelain 2>/dev/null", output)

    def test_call__fish_flag_rules_drop_fire_broken_guard(self):
        output = self.run_command("fish")

        # fire compares "--" + option against the previous token, which never matches, so a
        # leftover guard would disable every subcommand flag rule; the dead helper is dropped
        self.assertNotIn("; and __treefort_is_prev_global;", output)
        self.assertNotIn("__treefort_is_prev_global", output)
        self.assertIn("__treefort_option_entered_check --yes", output)

    def test_call__fish_registers_short_v_flag(self):
        output = self.run_command("fish")

        self.assertIn("__treefort_option_entered_check -v' -s v", output)
        self.assertNotIn("--v'", output)
        self.assertNotIn("-l v\n", output)

    def test_call__fish_functions_are_balanced(self):
        output = self.run_command("fish")

        # every function ends with a column-0 `end`; nested for/if blocks indent theirs
        self.assertEqual(
            len(re.findall(r"^function ", output, re.MULTILINE)),
            len(re.findall(r"^end$", output, re.MULTILINE)),
        )
