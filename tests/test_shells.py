import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from treefort.cli_manifest import CommandSpec, ParamSpec
from treefort.command import Completion
from treefort.hooks import Hook
from treefort.shells import (
    CMD_PLACEHOLDER,
    DEFAULT_CMD_NAME,
    RESERVED_SHELL_WORDS,
    SHELLS,
    detect_shell,
    init_hook_line,
    is_valid_command_name,
    render_all_completions,
    resolve_invoked_command,
    shell_rc_path,
    wrapper,
)
from treefort.shells import bash as bash_shell
from treefort.shells import fish as fish_shell
from treefort.shells import zsh as zsh_shell


def param(**overrides) -> ParamSpec:
    values = {
        "name": "name",
        "cli_name": "NAME",
        "kind": "positional",
        "value_type": "str",
        "nullable": False,
        "required": True,
        "has_default": False,
        "default": None,
        "choices": (),
        "short": None,
        "completion": "none",
        "description": "",
    }
    values.update(overrides)
    return ParamSpec(**values)


REMOVE = CommandSpec(
    name="remove",
    aliases=("rm",),
    summary="Remove a worktree from the current project",
    description="",
    params=(
        param(
            name="worktree_name",
            cli_name="WORKTREE_NAME",
            completion=Completion.WORKTREE.value,
            description="The name of the worktree to remove",
        ),
        param(
            name="force",
            cli_name="--force",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=False,
            short="-f",
            description="Whether to force the removal of the worktree",
        ),
        param(
            name="fetch",
            cli_name="--fetch",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=True,
            short="-F",
        ),
    ),
    help_text="",
)

CHECKOUT = CommandSpec(
    name="checkout",
    aliases=(),
    summary="Checkout an existing branch or PR as a worktree.",
    description="",
    params=(
        param(
            name="branch_or_pr",
            cli_name="BRANCH_OR_PR",
            completion=Completion.BRANCH_REF.value,
        ),
        param(
            name="remote",
            cli_name="--remote",
            kind="flag",
            required=False,
            has_default=True,
            default=None,
            short="-r",
            completion=Completion.REMOTE.value,
            description="The remote to fetch from",
        ),
    ),
    help_text="",
)

SHELL_INIT = CommandSpec(
    name="shell-init",
    aliases=(),
    summary="Emit shell integration for the user's shell",
    description="",
    params=(
        param(name="shell", cli_name="SHELL", choices=("bash", "zsh", "fish")),
        param(name="hook", cli_name="HOOK", completion=Completion.HOOK.value),
        param(
            name="count",
            cli_name="--count",
            kind="flag",
            value_type="int",
            required=False,
            has_default=True,
            default=1,
        ),
    ),
    help_text="",
)

SYNC = CommandSpec(
    name="sync",
    aliases=(),
    summary="Synchronize hooks and templates",
    description="",
    params=(
        param(
            name="mode",
            cli_name="--mode",
            kind="flag",
            required=False,
            has_default=True,
            default="merge",
            short="-m",
            choices=("merge", "overwrite"),
        ),
    ),
    help_text="",
)

CREATE = CommandSpec(
    name="create",
    aliases=(),
    summary="Create a new worktree",
    description="",
    params=(
        param(name="worktree_name", cli_name="WORKTREE_NAME"),
        param(
            name="base_ref",
            cli_name="BASE_REF",
            required=False,
            has_default=True,
            completion=Completion.BASE_REF.value,
        ),
        param(
            name="target",
            cli_name="TARGET",
            kind="flag",
            required=False,
            has_default=True,
            completion=Completion.WORKTREE_SWITCH.value,
        ),
        param(
            name="current",
            cli_name="--current",
            kind="flag",
            required=False,
            has_default=True,
            completion=Completion.WORKTREE_CURRENT.value,
        ),
    ),
    help_text="",
)

VARIADIC = CommandSpec(
    name="multi",
    aliases=(),
    summary="",
    description="",
    params=(
        param(name="first", cli_name="FIRST", choices=("one", "two")),
        param(
            name="rest",
            cli_name="REST",
            kind="variadic_positional",
            required=False,
            choices=("alpha", "beta"),
        ),
    ),
    help_text="",
)

COMMANDS = {
    "checkout": CHECKOUT,
    "create": CREATE,
    "remove": REMOVE,
    "shell-init": SHELL_INIT,
    "sync": SYNC,
}
ALIAS_MAP = {"rm": "remove"}
HOOK_WORDS = ["list", *(hook.name for hook in Hook)]


class ShellsTestCase(TestCase):
    def setUp(self):
        self.scripts = render_all_completions(COMMANDS, ALIAS_MAP)

    def bash(self, words, cword, helper_override=None):
        """Source the bash script, run the completion function, and return COMPREPLY."""
        script = self.scripts["bash"]
        if helper_override:
            script += f"\n{helper_override}\n"
        program = (
            f"{script}\n"
            f"COMP_WORDS=({' '.join(repr(word) for word in words)})\n"
            f"COMP_CWORD={cword}\n"
            "_treefort_complete\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", program], capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual("", result.stderr, result.stderr)
        return result.stdout.split()

    def test_render_all_completions__covers_every_shell(self):
        self.assertEqual(set(SHELLS), set(self.scripts))

    def test_render_all_completions__is_deterministic(self):
        self.assertEqual(self.scripts, render_all_completions(COMMANDS, ALIAS_MAP))

    def test_bash_completion_script__passes_the_syntax_check(self):
        result = subprocess.run(
            ["bash", "-n"],
            input=self.scripts["bash"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_bash_completion_script__offers_commands_and_aliases_at_the_root(self):
        self.assertEqual(
            ["checkout", "create", "remove", "shell-init", "sync", "rm"],
            [word for word in self.bash(["treefort", ""], 1) if not word.startswith("-")],
        )

    def test_bash_completion_script__offers_global_flags_at_the_root(self):
        self.assertEqual(
            ["--help", "--verbose", "-h", "-v"],
            sorted(word for word in self.bash(["treefort", "-"], 1) if word.startswith("-")),
        )

    def test_bash_completion_script__offers_a_commands_flags(self):
        self.assertIn("--force", self.bash(["treefort", "remove", "-"], 2))

    def test_bash_completion_script__replicates_flags_under_an_alias(self):
        self.assertIn("--force", self.bash(["treefort", "rm", "-"], 2))

    def test_bash_completion_script__offers_negation_only_for_default_true_flags(self):
        words = self.bash(["treefort", "rm", "--no"], 2)

        self.assertIn("--no-fetch", words)
        self.assertNotIn("--no-force", words)

    def test_bash_completion_script__completes_a_positional_from_its_provider(self):
        stub = "_treefort_worktrees() { printf '%s\\n' alpha beta; }"

        words = self.bash(["treefort", "rm", ""], 2, helper_override=stub)

        self.assertEqual(["alpha", "beta"], words)

    def test_bash_completion_script__completes_a_flag_from_its_provider(self):
        stub = "_treefort_remotes() { printf '%s\\n' origin upstream; }"

        words = self.bash(["treefort", "checkout", "--remote", ""], 3, helper_override=stub)

        self.assertEqual(["origin", "upstream"], words)

    def test_bash_completion_script__completes_a_positional_after_a_value_flag(self):
        stub = "_treefort_branch_refs() { printf '%s\\n' main feature; }"

        words = self.bash(
            ["treefort", "checkout", "--remote", "origin", ""], 4, helper_override=stub
        )

        self.assertEqual(["main", "feature"], words)
        # The count advances past the filled positional instead of staying at zero
        self.assertEqual(
            [],
            self.bash(
                ["treefort", "checkout", "--remote", "origin", "main", ""],
                5,
                helper_override=stub,
            ),
        )

    def test_bash_completion_script__skips_the_argument_of_a_short_value_flag(self):
        stub = "_treefort_branch_refs() { printf '%s\\n' main feature; }"

        words = self.bash(["treefort", "checkout", "-r", "origin", ""], 4, helper_override=stub)

        self.assertEqual(["main", "feature"], words)

    def test_bash_completion_script__counts_a_bare_dash_as_a_positional(self):
        self.assertEqual(HOOK_WORDS, self.bash(["treefort", "shell-init", "-", ""], 3))

    def test_bash_completion_script__ignores_the_word_break_inside_an_inline_value(self):
        # Interactive bash splits --remote=origin into --remote, =, origin
        stub = "_treefort_branch_refs() { printf '%s\\n' main feature; }"

        words = self.bash(
            ["treefort", "checkout", "--remote", "=", "origin", ""], 5, helper_override=stub
        )

        self.assertEqual(["main", "feature"], words)

    def test_bash_completion_script__offers_nothing_for_a_value_flag_without_candidates(self):
        self.assertEqual([], self.bash(["treefort", "shell-init", "--count", ""], 3))

    def test_bash_completion_script__offers_nothing_for_an_inline_value_without_candidates(self):
        self.assertEqual([], self.bash(["treefort", "shell-init", "--count=3"], 2))

    def test_bash_completion_script__completes_static_choices(self):
        self.assertEqual(
            ["bash", "fish", "zsh"], sorted(self.bash(["treefort", "shell-init", ""], 2))
        )

    def test_bash_completion_script__completes_inline_static_choices(self):
        self.assertEqual(
            ["--mode=merge", "--mode=overwrite"],
            sorted(self.bash(["treefort", "sync", "--mode="], 2)),
        )

    def test_bash_completion_script__completes_a_short_inline_flag_value(self):
        self.assertEqual(
            ["-m=merge", "-m=overwrite"],
            sorted(self.bash(["treefort", "sync", "-m="], 2)),
        )

    def test_bash_completion_script__selects_the_positional_by_its_index(self):
        stub = "_treefort_worktrees() { printf '%s\\n' alpha; }"

        self.assertEqual(["alpha"], self.bash(["treefort", "remove", ""], 2, helper_override=stub))
        self.assertEqual(
            [], self.bash(["treefort", "remove", "alpha", ""], 3, helper_override=stub)
        )

    def test_bash_completion_script__emits_only_the_helpers_in_use(self):
        script = self.scripts["bash"]

        self.assertIn("_treefort_worktrees()", script)
        self.assertIn("_treefort_remotes()", script)
        self.assertIn("_treefort_branch_refs()", script)

    def test_bash_completion_script__omits_unused_helpers(self):
        script = bash_shell.completion_script({"shell-init": SHELL_INIT}, {})

        self.assertNotIn("_treefort_worktrees()", script)
        self.assertNotIn("_treefort_remotes()", script)

    def test_bash_completion_script__silences_git_stderr(self):
        script = self.scripts["bash"]

        for line in script.splitlines():
            if "git " in line:
                self.assertIn("2>/dev/null", line)

    def test_bash_completion_script__expands_the_hook_provider_statically(self):
        words = self.bash(["treefort", "shell-init", "bash", ""], 3)

        self.assertIn("list", words)
        for hook in Hook:
            self.assertIn(hook.name, words)

    def test_bash_completion_script__uses_the_command_placeholder(self):
        self.assertIn(f"complete -F _treefort_complete {CMD_PLACEHOLDER}", self.scripts["bash"])

    def test_bash_completion_script__honours_a_custom_command_name(self):
        script = bash_shell.completion_script(COMMANDS, ALIAS_MAP, cmd="mytreefort")

        self.assertIn("complete -F _treefort_complete mytreefort", script)
        self.assertIn("# bash completion for mytreefort", script)

    def test_zsh_completion_script__prepends_bashcompinit(self):
        script = self.scripts["zsh"]

        self.assertTrue(script.startswith("autoload -Uz bashcompinit && bashcompinit"))
        self.assertIn("_treefort_complete()", script)

    def test_fish_completion_script__registers_root_commands_with_descriptions(self):
        script = self.scripts["fish"]

        self.assertIn(
            "complete -c __CMD__ -n \"__fish_use_subcommand\" -a 'remove' "
            "-d 'Remove a worktree from the current project'",
            script,
        )
        self.assertIn("-a 'rm'", script)

    def test_fish_completion_script__registers_global_flags_with_descriptions(self):
        script = self.scripts["fish"]

        self.assertIn(
            "complete -c __CMD__ -n '__fish_use_subcommand' -l verbose "
            "-d 'Enable verbose debug logging'",
            script,
        )
        self.assertIn(
            "-n '__fish_seen_subcommand_from remove rm' -l verbose "
            "-d 'Enable verbose debug logging'",
            script,
        )

    def test_fish_completion_script__counts_only_positionals(self):
        script = self.scripts["fish"]

        self.assertIn("function __treefort_positional_count", script)
        self.assertIn(
            "shell-init; and test (__treefort_positional_count --count) -eq 1' -a 'list pre_init",
            script,
        )
        self.assertIn("remove rm; and test (__treefort_positional_count) -eq 0'", script)

    def test_fish_completion_script__registers_flags_for_every_alias(self):
        script = self.scripts["fish"]

        self.assertIn("-n '__fish_seen_subcommand_from remove rm' -l force -s f", script)

    def test_fish_completion_script__marks_value_flags_exclusive(self):
        script = self.scripts["fish"]

        self.assertIn('-l remote -s r -x -a "(__treefort_remotes)"', script)

    def test_fish_completion_script__offers_negation_only_for_default_true_flags(self):
        script = self.scripts["fish"]

        self.assertIn("-l no-fetch", script)
        self.assertNotIn("-l no-force", script)

    def test_fish_completion_script__escapes_single_quotes(self):
        spec = CommandSpec(
            name="sample",
            aliases=(),
            summary="Install to the user's PATH",
            description="",
            params=(),
            help_text="",
        )

        script = fish_shell.completion_script({"sample": spec}, {})

        self.assertIn(r"-d 'Install to the user\'s PATH'", script)

    def test_fish_completion_script__offers_provider_prefixes(self):
        script = self.scripts["fish"]

        self.assertIn('-a "@ ^ (__treefort_branch_refs)"', script)
        self.assertIn('"- ^ @ (__treefort_worktrees)"', script)
        self.assertIn('"@ (__treefort_worktrees)"', script)

    def test_fish_completion_script__uses_fish_helper_names(self):
        script = self.scripts["fish"]

        self.assertIn("function __treefort_worktrees", script)
        self.assertIn("function __treefort_branch_refs", script)

    def test_fish_completion_script__silences_git_stderr(self):
        script = self.scripts["fish"]

        for line in script.splitlines():
            if "git " in line:
                self.assertIn("2>/dev/null", line)

    def test_fish_completion_script__offers_a_variadic_positional_after_its_index(self):
        script = fish_shell.completion_script({"multi": VARIADIC}, {}, "treefort")

        self.assertIn("test (__treefort_positional_count) -ge 1'", script)

    def test_fish_completion_script__disables_file_completion(self):
        self.assertIn("complete -c __CMD__ -f", self.scripts["fish"])


class FishCompletionTestCase(TestCase):
    """Runs the generated fish script, which needs a fish binary to observe."""

    @skipUnless(shutil.which("fish"), "fish is not installed")
    def test_fish_completion__offers_the_positional_after_a_value_flag(self):
        script = fish_shell.completion_script(COMMANDS, ALIAS_MAP, "treefort")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "completions.fish"
            path.write_text(script, encoding="utf-8")
            self.assertEqual(
                ["bash", "fish", "zsh"], self.complete(path, tmp, "treefort shell-init ")
            )
            self.assertEqual(
                ["bash", "fish", "zsh"],
                self.complete(path, tmp, "treefort shell-init --count 2 "),
            )
            self.assertEqual(
                sorted(HOOK_WORDS), self.complete(path, tmp, "treefort shell-init bash ")
            )
            self.assertEqual(sorted(HOOK_WORDS), self.complete(path, tmp, "treefort shell-init - "))
            # The candidates come from git, so only their presence is stable here
            self.assertNotEqual([], self.complete(path, tmp, "treefort checkout --remote origin "))
            self.assertEqual(
                [], self.complete(path, tmp, "treefort checkout --remote origin main ")
            )

    @skipUnless(shutil.which("fish"), "fish is not installed")
    def test_fish_completion__offers_a_variadic_positional_repeatedly(self):
        script = fish_shell.completion_script({"multi": VARIADIC}, {}, "treefort")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "completions.fish"
            path.write_text(script, encoding="utf-8")
            self.assertEqual(["one", "two"], self.complete(path, tmp, "treefort multi "))
            self.assertEqual(["alpha", "beta"], self.complete(path, tmp, "treefort multi one "))
            self.assertEqual(
                ["alpha", "beta"], self.complete(path, tmp, "treefort multi one alpha ")
            )

    def complete(self, script: Path, home: str, line: str) -> list[str]:
        """Return the candidates fish offers for a command line."""
        environment = {
            **os.environ,
            "HOME": home,
            "XDG_CONFIG_HOME": home,
            "XDG_DATA_HOME": home,
            "XDG_CACHE_HOME": home,
        }
        program = f"source {shlex.quote(str(script))}; complete -C {shlex.quote(line)}"
        result = subprocess.run(
            ["fish", "--no-config", "-c", program],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        self.assertEqual("", result.stderr, result.stderr)
        return sorted(entry.split("\t")[0] for entry in result.stdout.splitlines())


class WrapperTestCase(TestCase):
    def test_wrapper__uses_the_cd_directive_file(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertIn("TREEFORT_CD_FILE", wrapper(shell))

    def test_wrapper__honours_a_custom_command_name(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertIn("mytreefort", wrapper(shell, "mytreefort"))

    def test_wrapper__returns_the_status_of_the_wrapped_command(self):
        """A live run returns the wrapped command's exit code, not the cleanup's."""
        for shell in ("bash", "fish"):
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} is not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    # A distinctive code: the cleanup commands all succeed, so their codes
                    # would mask it
                    target = Path(tmp) / "tfcmd"
                    target.write_text("#!/bin/sh\nexit 5\n")
                    target.chmod(0o700)
                    environment = {
                        **os.environ,
                        "HOME": tmp,
                        "XDG_CONFIG_HOME": tmp,
                        "XDG_DATA_HOME": tmp,
                        "XDG_CACHE_HOME": tmp,
                        "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                    }
                    program = f"{wrapper(shell, 'tfcmd')}\ntfcmd"
                    result = subprocess.run(
                        ["fish", "--no-config", "-c", program]
                        if shell == "fish"
                        else ["bash", "-c", program],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                        env=environment,
                    )
                    self.assertEqual(5, result.returncode)
                    self.assertEqual("", result.stderr, result.stderr)

    def wrapper_environment(self, tmp: str, extra_path: str = "") -> dict[str, str]:
        """Return an isolated environment with a private ``TMPDIR`` for ``mktemp``."""
        (Path(tmp) / "tmpdir").mkdir(exist_ok=True)
        return {
            **os.environ,
            "HOME": tmp,
            "XDG_CONFIG_HOME": tmp,
            "XDG_DATA_HOME": tmp,
            "XDG_CACHE_HOME": tmp,
            "TMPDIR": str(Path(tmp) / "tmpdir"),
            "PATH": f"{extra_path}{os.pathsep}{tmp}{os.pathsep}{os.environ['PATH']}",
        }

    def run_shell(self, shell: str, program: str, environment: dict[str, str]):
        """Run ``program`` in a live shell with the isolated environment."""
        command = (
            ["fish", "--no-config", "-c", program] if shell == "fish" else [shell, "-c", program]
        )
        return subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False, env=environment
        )

    def write_script(self, directory: str, name: str, body: str) -> Path:
        """Write an executable stub command into ``directory``."""
        script = Path(directory) / name
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o700)
        return script

    def leftover_directive_files(self, tmp: str) -> list[Path]:
        """Return the temporary directive files the wrapper failed to remove."""
        return list((Path(tmp) / "tmpdir").iterdir())

    def test_wrapper__changes_directory_with_the_double_dash_guard(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertIn("cd --", wrapper(shell))

    def test_fish_wrapper__quotes_the_directive_file(self):
        script = fish_shell.wrapper("tf")

        self.assertIn('if test -n "$cd_file"', script)
        self.assertIn('if test -s "$cd_file"', script)
        self.assertIn('rm -f -- "$cd_file"', script)
        self.assertNotIn("test -s $cd_file", script)

    def test_wrapper__cleans_up_when_the_command_fails_under_set_e(self):
        """The ``|| rc=$?`` guard keeps ``set -e`` from aborting before the cleanup."""
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} is not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    self.write_script(tmp, "tfcmd", "exit 5")
                    environment = self.wrapper_environment(tmp)
                    program = f"set -e\n{wrapper(shell, 'tfcmd')}\ntfcmd"
                    result = self.run_shell(shell, program, environment)

                    self.assertEqual(5, result.returncode)
                    self.assertEqual([], self.leftover_directive_files(tmp))

    def test_wrapper__returns_the_status_of_a_failed_cd(self):
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} is not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    self.write_script(
                        tmp,
                        "tfcmd",
                        'printf "%s\\n" /nonexistent-treefort-dir > "$TREEFORT_CD_FILE"',
                    )
                    environment = self.wrapper_environment(tmp)
                    result = self.run_shell(shell, f"{wrapper(shell, 'tfcmd')}\ntfcmd", environment)

                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual([], self.leftover_directive_files(tmp))

    def test_wrapper__survives_an_empty_mktemp_result(self):
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} is not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    fake_bin = Path(tmp) / "fakebin"
                    fake_bin.mkdir()
                    self.write_script(str(fake_bin), "mktemp", "exit 1")
                    self.write_script(tmp, "tfcmd", "exit 0")
                    environment = self.wrapper_environment(tmp, extra_path=str(fake_bin))
                    result = self.run_shell(shell, f"{wrapper(shell, 'tfcmd')}\ntfcmd", environment)

                    self.assertEqual(0, result.returncode, result.stderr)

    def test_wrapper__rejects_an_unknown_shell(self):
        with self.assertRaisesRegex(ValueError, "Unknown shell: pwsh"):
            wrapper("pwsh")

    def test_zsh_wrapper__matches_the_bash_wrapper(self):
        self.assertEqual(zsh_shell.wrapper(), bash_shell.wrapper())


class WorktreeHelperTestCase(TestCase):
    """Runs the generated worktree helper against a real treefort-shaped repository."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "treefort",
            "GIT_AUTHOR_EMAIL": "treefort@example.com",
            "GIT_COMMITTER_NAME": "treefort",
            "GIT_COMMITTER_EMAIL": "treefort@example.com",
        }
        (self.root / "home").mkdir()
        self.project = self.root / "project"
        self._git(self.root, "init", "-q", "-b", "main", "seed")
        self._git(self.root / "seed", "commit", "-q", "--allow-empty", "-m", "init")
        self.project.mkdir()
        self._git(self.root, "clone", "-q", "--bare", "seed", str(self.project / ".bare"))
        self._git(
            self.project / ".bare", "worktree", "add", "-q", "../alpha", "-b", "alpha", "main"
        )
        self._git(self.project / ".bare", "worktree", "add", "-q", "../beta", "-b", "beta", "main")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cwd, *args):
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_generated_helper__reports_worktree_names_relative_to_the_project(self):
        script = render_all_completions(COMMANDS, ALIAS_MAP)["bash"]

        result = subprocess.run(
            ["bash", "-c", f"{script}\n_treefort_worktrees"],
            cwd=self.project / "beta",
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertEqual("", result.stderr, result.stderr)
        self.assertEqual(["alpha", "beta"], sorted(result.stdout.split()))

    def test_generated_helper__stays_silent_outside_a_repository(self):
        script = render_all_completions(COMMANDS, ALIAS_MAP)["bash"]

        result = subprocess.run(
            ["bash", "-c", f"{script}\n_treefort_worktrees"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)


class ShellHelpersTestCase(TestCase):
    """Covers command-name validation, shell detection, hook lines, and startup files."""

    def test_is_valid_command_name__accepts_a_plain_name(self):
        self.assertTrue(is_valid_command_name("treefort"))

    def test_is_valid_command_name__accepts_an_alias_with_dashes_and_digits(self):
        for name in ("tf", "my-tf", "tf_2", "tf2"):
            with self.subTest(name=name):
                self.assertTrue(is_valid_command_name(name))

    def test_is_valid_command_name__rejects_path_traversal(self):
        for name in ("../evil", "a/b", "./tf", "..", "a b"):
            with self.subTest(name=name):
                self.assertFalse(is_valid_command_name(name))

    def test_is_valid_command_name__rejects_a_leading_digit_dash_or_underscore(self):
        for name in ("1tf", "-tf", "_tf"):
            with self.subTest(name=name):
                self.assertFalse(is_valid_command_name(name))

    def test_is_valid_command_name__rejects_an_empty_or_padded_name(self):
        for name in ("", " tf", "tf\n", "tf "):
            with self.subTest(name=name):
                self.assertFalse(is_valid_command_name(name))

    def test_is_valid_command_name__rejects_every_reserved_shell_word(self):
        for word in sorted(RESERVED_SHELL_WORDS):
            with self.subTest(word=word):
                self.assertFalse(is_valid_command_name(word))

    def test_resolve_invoked_command__uses_the_argv0_basename(self):
        self.assertEqual("treefort", resolve_invoked_command("/usr/local/bin/treefort"))

    def test_resolve_invoked_command__preserves_a_symlinked_alias_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "treefort"
            target.write_text("")
            alias = Path(tmp) / "tf"
            alias.symlink_to(target)

            self.assertEqual("tf", resolve_invoked_command(str(alias)))

    def test_resolve_invoked_command__falls_back_for_module_and_pex_invocations(self):
        for argv0 in ("__main__.py", "treefort.pex", "/tmp/treefort.pex", ""):
            with self.subTest(argv0=argv0):
                self.assertEqual(DEFAULT_CMD_NAME, resolve_invoked_command(argv0))

    def test_resolve_invoked_command__falls_back_for_a_reserved_name(self):
        self.assertEqual(DEFAULT_CMD_NAME, resolve_invoked_command("/usr/bin/rm"))

    def test_resolve_invoked_command__reads_sys_argv_by_default(self):
        with patch("sys.argv", ["/opt/bin/tf"]):
            self.assertEqual("tf", resolve_invoked_command())

    def test_detect_shell__recognizes_a_supported_shell(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertEqual(shell, detect_shell(f"/usr/local/bin/{shell}"))

    def test_detect_shell__returns_none_for_an_unsupported_shell(self):
        for value in ("/bin/pwsh", "", "/bin/sh", "/bin/bash5"):
            with self.subTest(value=value):
                self.assertIsNone(detect_shell(value))

    def test_detect_shell__reads_the_shell_variable_by_default(self):
        with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            self.assertEqual("zsh", detect_shell())

    def test_init_hook_line__evaluates_the_script_for_bash_and_zsh(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                self.assertEqual(f'eval "$(treefort shell-init {shell})"', init_hook_line(shell))

    def test_init_hook_line__sources_the_script_for_fish(self):
        self.assertEqual("treefort shell-init fish | source", init_hook_line("fish"))

    def test_init_hook_line__uses_the_given_command_name(self):
        self.assertEqual('eval "$(tf shell-init bash)"', init_hook_line("bash", "tf"))
        self.assertEqual("tf shell-init fish | source", init_hook_line("fish", "tf"))

    def test_init_hook_line__rejects_an_unknown_shell(self):
        with self.assertRaisesRegex(ValueError, "Unknown shell: pwsh"):
            init_hook_line("pwsh")

    def test_init_hook_line__rejects_an_invalid_command_name(self):
        with self.assertRaisesRegex(ValueError, "Invalid command name"):
            init_hook_line("bash", "../evil")

    def test_shell_rc_path__rejects_an_unknown_shell(self):
        with self.assertRaisesRegex(ValueError, "Unknown shell: pwsh"):
            shell_rc_path("pwsh")

    def test_shell_rc_path__prefers_zdotdir_for_zsh(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ZDOTDIR": tmp}):
            self.assertEqual(Path(tmp) / ".zshrc", shell_rc_path("zsh", Path("/home/u")))

    def test_shell_rc_path__falls_back_to_home_for_zsh(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZDOTDIR", None)

            self.assertEqual(Path("/home/u") / ".zshrc", shell_rc_path("zsh", Path("/home/u")))

    def test_shell_rc_path__uses_bashrc_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".bashrc").write_text("")
            (home / ".bash_profile").write_text("")

            with patch("treefort.shells.sys.platform", "darwin"):
                self.assertEqual(home / ".bashrc", shell_rc_path("bash", home))

    def test_shell_rc_path__uses_bash_profile_on_darwin_when_only_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".bash_profile").write_text("")

            with patch("treefort.shells.sys.platform", "darwin"):
                self.assertEqual(home / ".bash_profile", shell_rc_path("bash", home))

    def test_shell_rc_path__uses_bashrc_off_darwin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".bash_profile").write_text("")

            with patch("treefort.shells.sys.platform", "linux"):
                self.assertEqual(home / ".bashrc", shell_rc_path("bash", home))

    def test_shell_rc_path__uses_bashrc_on_darwin_when_neither_file_exists(self):
        """A fresh macOS home has no startup file, so the hook creates ``.bashrc``."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            with patch("treefort.shells.sys.platform", "darwin"):
                self.assertEqual(home / ".bashrc", shell_rc_path("bash", home))

    def test_shell_rc_path__uses_xdg_config_home_for_fish(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertEqual(
                Path(tmp) / "fish" / "config.fish", shell_rc_path("fish", Path("/home/u"))
            )

    def test_shell_rc_path__falls_back_to_home_config_for_fish(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_CONFIG_HOME", None)

            self.assertEqual(
                Path("/home/u") / ".config" / "fish" / "config.fish",
                shell_rc_path("fish", Path("/home/u")),
            )
