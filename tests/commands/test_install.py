import inspect
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.command import Arg
from treefort.commands.install import InstallCommand


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
                "force": (bool, Arg(short="-f")),
            },
            {
                name: (get_args(parameter.annotation)[0], get_args(parameter.annotation)[1])
                for name, parameter in parameters.items()
                if name != "self"
            },
        )
        for name in ("alias", "gh_ext", "path_bin", "force"):
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
