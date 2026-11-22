import inspect
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.command import Arg
from treefort.commands.install import InstallCommand, _user_search_path
from treefort.errors import ValidationError


class InstallCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.runtime = SimpleNamespace(logger=Mock())
        self.command = InstallCommand(self.runtime)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_call__declares_every_flag_as_keyword_only_with_a_short_flag(self):
        parameters = inspect.signature(InstallCommand.__call__).parameters

        self.assertEqual(
            {
                "alias": (str | None, Arg(short="-a")),
                "gh_ext": (bool, Arg(short="-g")),
                "path_bin": (bool, Arg(short="-p")),
                "shell": (bool, Arg(short="-s")),
                "force": (bool, Arg(short="-f")),
            },
            {
                name: (get_args(parameter.annotation)[0], get_args(parameter.annotation)[1])
                for name, parameter in parameters.items()
                if name != "self"
            },
        )
        for name in ("alias", "gh_ext", "path_bin", "shell", "force"):
            self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters[name].kind)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__installs_as_gh_extension_with_default_name(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(gh_ext=True)

        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__installs_as_gh_extension_with_custom_alias(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(alias="wktr", gh_ext=True)

        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-wktr" / "gh-wktr"
        )
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__installs_as_gh_extension_and_adds_gh_prefix(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(alias="worktree", gh_ext=True)

        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-worktree" / "gh-worktree"
        )
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch.dict(os.environ, {"PATH": f"{Path.home()}/.local/bin:/usr/bin"})
    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__installs_to_path_bin(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(path_bin=True)

        expected_target = Path.home() / ".local" / "bin" / "treefort"
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch.dict(os.environ, {"PATH": f"{Path.home()}/.local/bin:/usr/bin"})
    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__installs_to_path_bin_with_custom_alias(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(alias="wktr", path_bin=True)

        expected_target = Path.home() / ".local" / "bin" / "wktr"
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch.dict(os.environ, {"PATH": "/usr/bin:/usr/local/bin"})
    @patch("sys.argv", ["/usr/bin/treefort"])
    def test_call__fails_when_no_suitable_path_directory_found(self):
        with self.assertRaises(SystemExit) as cm:
            self.command(path_bin=True)

        self.assertEqual(cm.exception.code, 1)
        self.runtime.logger.error.assert_called_with(
            "Could not find a suitable user binary directory in your PATH."
        )

    @patch("sys.argv", ["/home/user/treefort/src/treefort/main.py"])
    @patch("pathlib.Path.symlink_to", autospec=True)
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__uses_symlink_when_running_from_source(
        self, mock_is_symlink, mock_exists, mock_mkdir, mock_symlink
    ):
        self.command(gh_ext=True)

        expected_source = Path("/home/user/treefort/src/treefort/main.py")
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_symlink.assert_called_once_with(expected_target, expected_source)

    @patch("sys.argv", ["/home/user/treefort/.venv/bin/treefort"])
    @patch("pathlib.Path.symlink_to", autospec=True)
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__uses_symlink_when_running_from_venv(
        self, mock_is_symlink, mock_exists, mock_mkdir, mock_symlink
    ):
        self.command(gh_ext=True)

        expected_source = Path("/home/user/treefort/.venv/bin/treefort")
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_symlink.assert_called_once_with(expected_target, expected_source)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__uses_copy_for_compiled_binary(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(gh_ext=True)

        expected_source = Path("/usr/bin/treefort")
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_copy.assert_called_once_with(expected_source, expected_target)
        mock_chmod.assert_called_once()

    @patch("sys.argv", ["/usr/bin/treefort.pex"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__uses_copy_for_pex_file(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(gh_ext=True)

        expected_source = Path("/usr/bin/treefort.pex")
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_copy.assert_called_once_with(expected_source, expected_target)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("builtins.input", return_value="1")
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__prompts_user_when_no_options_specified_and_chooses_gh_ext(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
        mock_input,
    ):
        self.command()

        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch.dict(os.environ, {"PATH": f"{Path.home()}/.local/bin:/usr/bin"})
    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("builtins.input", return_value="2")
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__prompts_user_when_no_options_specified_and_chooses_path_bin(
        self,
        mock_is_symlink,
        mock_exists,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
        mock_input,
    ):
        self.command()

        expected_target = Path.home() / ".local" / "bin" / "treefort"
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("builtins.input", return_value="3")
    @patch("sys.exit")
    def test_call__exits_when_invalid_choice(self, mock_exit, mock_input):
        self.command()

        mock_exit.assert_called_once_with(1)
        self.runtime.logger.error.assert_called_with("Invalid choice")

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__exits_when_target_exists_without_force(self, mock_is_symlink, mock_exists):
        with self.assertRaises(SystemExit) as cm:
            self.command(gh_ext=True)

        self.assertEqual(cm.exception.code, 1)
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        self.runtime.logger.warning.assert_called_with(
            f"Path already exists, use --force to overwrite: {expected_target}"
        )

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_symlink", return_value=True)
    def test_call__exits_when_target_is_symlink_without_force(self, mock_is_symlink, mock_exists):
        with self.assertRaises(SystemExit) as cm:
            self.command(gh_ext=True)

        self.assertEqual(cm.exception.code, 1)
        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        self.runtime.logger.warning.assert_called_with(
            f"Path already exists, use --force to overwrite: {expected_target}"
        )

    @patch("sys.argv", ["/usr/bin/treefort"])
    @patch("shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.chmod")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.unlink")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.is_symlink", return_value=False)
    def test_call__overwrites_when_target_exists_with_force(
        self,
        mock_is_symlink,
        mock_exists,
        mock_unlink,
        mock_stat,
        mock_chmod,
        mock_mkdir,
        mock_copy,
    ):
        self.command(gh_ext=True, force=True)

        expected_target = (
            Path.home() / ".local" / "share" / "gh" / "extensions" / "gh-treefort" / "gh-treefort"
        )
        mock_unlink.assert_called_once_with(True)
        mock_copy.assert_called_once_with(Path("/usr/bin/treefort"), expected_target)

    def test_user_search_path__drops_relative_and_virtualenv_entries(self):
        venv_dir = self.tmp_path / "venv"
        venv_bin = venv_dir / "bin"
        venv_bin.mkdir(parents=True)
        path = os.pathsep.join([".", "relative/dir", "/usr/bin", str(venv_bin), ""])

        with patch.dict(os.environ, {"PATH": path, "VIRTUAL_ENV": str(venv_dir)}):
            self.assertEqual("/usr/bin", _user_search_path())

    @contextmanager
    def shell_environment(self, shell="bash", path_entries=None, with_binary=False):
        """Isolate HOME and the shell startup file, with a real source binary to install.

        :param shell: The shell name reported by ``$SHELL``
        :param path_entries: The ``PATH`` entries, defaulting to the user binary directory
        :param with_binary: Whether a ``treefort`` binary already sits in the user directory
        :return: A context yielding the isolated home, binary, and user binary directory
        """
        home = self.tmp_path / "home"
        home.mkdir(exist_ok=True)
        user_bin = home / ".local" / "bin"
        user_bin.mkdir(parents=True, exist_ok=True)
        source = self.tmp_path / "dist" / "treefort"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("#!/bin/sh\n")
        source.chmod(0o700)
        if with_binary:
            installed = user_bin / "treefort"
            installed.write_text("installed\n")
            installed.chmod(0o700)
        entries = [str(user_bin)] if path_entries is None else path_entries
        environment = {
            "HOME": str(home),
            "PATH": os.pathsep.join([*entries, "/usr/bin", "/bin"]),
            "SHELL": f"/bin/{shell}",
        }
        with patch.dict(os.environ, environment, clear=False), patch("sys.argv", [str(source)]):
            for name in ("ZDOTDIR", "XDG_CONFIG_HOME", "VIRTUAL_ENV"):
                os.environ.pop(name, None)
            yield SimpleNamespace(home=home, source=source, user_bin=user_bin)

    def test_call__installs_to_the_path_and_configures_bash(self):
        with self.shell_environment() as env:
            self.command(path_bin=True, shell=True)

            self.assertTrue((env.user_bin / "treefort").exists())
            hook = (env.home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn('eval "$(treefort shell-init bash)"', hook)
            self.assertIn("# treefort shell integration", hook)

    def test_call__configures_the_hook_under_the_alias_name(self):
        with self.shell_environment() as env:
            self.command(alias="wktr", path_bin=True, shell=True)

            hook = (env.home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn('eval "$(wktr shell-init bash)"', hook)

    def test_call__configures_zsh_through_zdotdir(self):
        with self.shell_environment(shell="zsh") as env:
            zdotdir = env.home / "zdot"
            zdotdir.mkdir()

            with patch.dict(os.environ, {"ZDOTDIR": str(zdotdir)}):
                self.command(path_bin=True, shell=True)

            hook = (zdotdir / ".zshrc").read_text(encoding="utf-8")
            self.assertIn('eval "$(treefort shell-init zsh)"', hook)

    def test_call__configures_fish_through_xdg_config_home(self):
        with self.shell_environment(shell="fish") as env:
            config_home = env.home / "xdg"

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                self.command(path_bin=True, shell=True)

            hook = (config_home / "fish" / "config.fish").read_text(encoding="utf-8")
            self.assertIn("treefort shell-init fish | source", hook)

    def test_call__keeps_an_existing_binary_and_still_configures_the_shell(self):
        """The documented setup command must be safe to run twice."""
        with self.shell_environment(with_binary=True) as env:
            self.command(path_bin=True, shell=True)

            self.assertEqual("installed\n", (env.user_bin / "treefort").read_text())
            hook = (env.home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn('eval "$(treefort shell-init bash)"', hook)
            self.runtime.logger.info.assert_any_call(
                f"Keeping existing {env.user_bin / 'treefort'}, use --force to overwrite"
            )

    def test_call__fails_on_an_existing_binary_without_shell_integration(self):
        with self.shell_environment(with_binary=True):
            with self.assertRaises(SystemExit) as context:
                self.command(path_bin=True)

            self.assertEqual(1, context.exception.code)

    def test_call__reuses_an_existing_path_binary_for_shell_integration(self):
        with self.shell_environment(with_binary=True) as env:
            self.command(shell=True)

            self.assertEqual("installed\n", (env.user_bin / "treefort").read_text())
            self.assertIn(
                'eval "$(treefort shell-init bash)"',
                (env.home / ".bashrc").read_text(encoding="utf-8"),
            )

    def test_call__implies_a_path_install_when_the_binary_is_absent(self):
        with self.shell_environment() as env:
            self.command(shell=True)

            self.assertTrue((env.user_bin / "treefort").exists())

    def test_call__ignores_the_virtualenv_binary_when_resolving_the_path_install(self):
        with self.shell_environment(path_entries=[]) as env:
            venv_dir = self.tmp_path / "venv"
            venv_bin = venv_dir / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "treefort").write_text("virtualenv\n")

            with patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": str(venv_dir),
                    "PATH": os.pathsep.join([str(venv_bin), str(env.user_bin), "/usr/bin", "/bin"]),
                },
            ):
                self.command(shell=True)

            self.assertTrue((env.user_bin / "treefort").exists())
            self.assertEqual("virtualenv\n", (venv_bin / "treefort").read_text())

    def test_call__appends_the_hook_only_once(self):
        with self.shell_environment(with_binary=True) as env:
            self.command(shell=True)
            self.command(shell=True)

            hook = (env.home / ".bashrc").read_text(encoding="utf-8")
            self.assertEqual(1, hook.count("treefort shell-init bash"))
            self.runtime.logger.info.assert_any_call(
                f"Shell integration already configured in {env.home / '.bashrc'}"
            )

    def test_call__preserves_a_symlinked_startup_file(self):
        with self.shell_environment() as env:
            dotfiles = env.home / "dotfiles"
            dotfiles.mkdir()
            target = dotfiles / "bashrc"
            target.write_text("# existing\n")
            (env.home / ".bashrc").symlink_to(target)

            self.command(path_bin=True, shell=True)

            self.assertTrue((env.home / ".bashrc").is_symlink())
            self.assertIn("treefort shell-init bash", target.read_text(encoding="utf-8"))

    def test_call__rejects_gh_ext_with_shell_without_path_bin(self):
        with self.shell_environment():
            with self.assertRaisesRegex(
                ValidationError, "Shell integration \\(--shell\\) requires a PATH installation"
            ):
                self.command(gh_ext=True, shell=True)

    def test_call__rejects_an_invalid_alias(self):
        with self.shell_environment():
            with self.assertRaisesRegex(ValidationError, "Invalid alias name: '\\.\\./evil'"):
                self.command(alias="../evil", shell=True)

    def test_call__rejects_an_unsupported_shell(self):
        with self.shell_environment():
            with patch.dict(os.environ, {"SHELL": "/bin/pwsh"}):
                with self.assertRaisesRegex(
                    ValidationError, "Could not detect a supported shell from \\$SHELL"
                ):
                    self.command(path_bin=True, shell=True)

    def test_call__skips_the_self_copy_when_force_targets_the_running_binary(self):
        with self.shell_environment(with_binary=True) as env:
            installed = env.user_bin / "treefort"

            with patch("sys.argv", [str(installed)]):
                self.command(path_bin=True, force=True, shell=True)

            self.assertEqual("installed\n", installed.read_text())
            self.runtime.logger.info.assert_any_call(f"Installed to {installed}")

    def test_call__does_not_touch_the_startup_file_without_shell(self):
        with self.shell_environment() as env:
            self.command(path_bin=True)

            self.assertFalse((env.home / ".bashrc").exists())
