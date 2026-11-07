import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import DEFAULT, Mock, PropertyMock, patch

import tomlkit.exceptions

from treefort.errors import AncestorNotFoundError, ConfigTypeError, RemoteUsageError
from treefort.git import GitRemote
from treefort.runtime import Runtime


class RuntimeTestCase(TestCase):
    def setUp(self):
        # the lookup methods only need `context` and `git`, so skip real runtime
        # construction to avoid logging and migration side effects
        self.runtime = Runtime.__new__(Runtime)
        self.config = SimpleNamespace(owner="octo", name="repo")
        self.runtime.context = SimpleNamespace(
            get_config=lambda: self.config,
        )
        self.remotes = [
            GitRemote("origin", "git@github.com:octo/repo.git", "fetch"),
            GitRemote("origin", "git@github.com:octo/repo.git", "push"),
            GitRemote("other", "git@github.com:other/repo.git", "fetch"),
        ]
        self.runtime.git = SimpleNamespace(remote=lambda: self.remotes)

    def test_init__migrates_before_accessing_global_config_dir(self):
        """Migration must complete before the log directory is resolved or operators exist."""
        events: list[str] = []
        log_root = Path("/tmp/treefort-global-config")

        def record(event: str):
            def record_event(*args, **kwargs):
                events.append(event)
                return DEFAULT

            return record_event

        def read_global_config_dir(*args, **kwargs):
            events.append("global_config_dir")
            return log_root

        class TracedContext:
            """Stand-in for Context that surfaces migration and directory access as events."""

            instances: list["TracedContext"] = []

            global_config_dir = PropertyMock(side_effect=read_global_config_dir)

            def __init__(self):
                self.migrate = Mock(side_effect=record("migrate"))
                TracedContext.instances.append(self)

        logger = Mock()
        log_opener = Mock(side_effect=record("setup_logger"), return_value=logger)
        operators = {
            "hooks": Mock(side_effect=record("hooks")),
            "git": Mock(side_effect=record("git")),
            "gh": Mock(side_effect=record("gh")),
            "templates": Mock(side_effect=record("templates")),
        }

        with (
            patch("treefort.runtime.Context", TracedContext),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks", operators["hooks"]),
            patch("treefort.runtime.GitCLI", operators["git"]),
            patch("treefort.runtime.GithubCLI", operators["gh"]),
            patch("treefort.runtime.Templates", operators["templates"]),
        ):
            runtime = Runtime(name="test", verbose=True)

        self.assertEqual(
            events,
            ["migrate", "global_config_dir", "setup_logger", "hooks", "git", "gh", "templates"],
        )

        context = TracedContext.instances[0]
        context.migrate.assert_called_once_with()
        log_opener.assert_called_once_with(
            "test",
            log_dir=log_root / "logs",
            verbose=True,
        )
        self.assertEqual(runtime.logger, logger)
        for operator in operators.values():
            operator.assert_called_once()

    def test_init__resolves_verbose_from_env_var(self):
        context = Mock()
        context.global_config_dir = Path("/tmp/treefort-global-config")
        log_opener = Mock(return_value=Mock())

        with (
            patch.dict(os.environ, {"TREEFORT_VERBOSE": "1"}),
            patch("treefort.runtime.Context", return_value=context),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks"),
            patch("treefort.runtime.GitCLI"),
            patch("treefort.runtime.GithubCLI"),
            patch("treefort.runtime.Templates"),
        ):
            runtime = Runtime(name="test", verbose=False)

        self.assertTrue(runtime.verbose)
        log_opener.assert_called_once_with(
            "test",
            log_dir=Path("/tmp/treefort-global-config/logs"),
            verbose=True,
        )

    def test_init__resolves_verbose_from_repository_config(self):
        context = Mock()
        context.global_config_dir = Path("/tmp/treefort-global-config")
        context.get_config.return_value = SimpleNamespace(verbose=True)
        context.get_global_config.return_value = SimpleNamespace(verbose=False)
        log_opener = Mock(return_value=Mock())

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("treefort.runtime.Context", return_value=context),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks"),
            patch("treefort.runtime.GitCLI"),
            patch("treefort.runtime.GithubCLI"),
            patch("treefort.runtime.Templates"),
        ):
            os.environ.pop("TREEFORT_VERBOSE", None)
            runtime = Runtime(name="test", verbose=False)

        self.assertTrue(runtime.verbose)
        log_opener.assert_called_once_with(
            "test",
            log_dir=Path("/tmp/treefort-global-config/logs"),
            verbose=True,
        )

    def test_init__resolves_verbose_from_global_config_when_outside_project(self):
        context = Mock()
        context.global_config_dir = Path("/tmp/treefort-global-config")
        context.get_config.side_effect = AncestorNotFoundError("not in a project")
        context.get_global_config.return_value = SimpleNamespace(verbose=True)
        log_opener = Mock(return_value=Mock())

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("treefort.runtime.Context", return_value=context),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks"),
            patch("treefort.runtime.GitCLI"),
            patch("treefort.runtime.GithubCLI"),
            patch("treefort.runtime.Templates"),
        ):
            os.environ.pop("TREEFORT_VERBOSE", None)
            runtime = Runtime(name="test", verbose=False)

        self.assertTrue(runtime.verbose)
        log_opener.assert_called_once_with(
            "test",
            log_dir=Path("/tmp/treefort-global-config/logs"),
            verbose=True,
        )
        runtime.logger.warning.assert_not_called()

    def test_init__falls_back_to_false_when_config_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            home_dir = tmp_path / "home"
            project_dir = tmp_path / "project"
            (project_dir / ".bare").mkdir(parents=True)
            (project_dir / ".treefort").mkdir(parents=True)
            (home_dir / ".treefort").mkdir(parents=True)
            (project_dir / ".treefort" / "config.toml").write_text(
                "invalid = [toml", encoding="utf-8"
            )
            (home_dir / ".treefort" / "config.toml").write_text(
                'type = "repository"\nverbose = true\n', encoding="utf-8"
            )
            logger = Mock()
            log_opener = Mock(return_value=logger)

            with (
                patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False),
                patch("treefort.context.Path.cwd", return_value=project_dir),
                patch("treefort.runtime.setup_logger", log_opener),
                patch("treefort.runtime.Hooks"),
                patch("treefort.runtime.GitCLI"),
                patch("treefort.runtime.GithubCLI"),
                patch("treefort.runtime.Templates"),
            ):
                os.environ.pop("TREEFORT_VERBOSE", None)
                runtime = Runtime(name="test", verbose=False)

                self.assertFalse(runtime.verbose)
                log_opener.assert_called_once_with(
                    "test",
                    log_dir=home_dir / ".treefort" / "logs",
                    verbose=False,
                )
                self.assertEqual(logger.warning.call_count, 2)
                self.assertIn(
                    "Ignoring invalid repository configuration for verbose mode:",
                    logger.warning.call_args_list[0][0][0],
                )
                self.assertIn(
                    "Ignoring invalid global configuration for verbose mode: "
                    "Invalid config type: repository",
                    logger.warning.call_args_list[1][0][0],
                )
                with self.assertRaises(tomlkit.exceptions.TOMLKitError):
                    runtime.context.get_config()
                with self.assertRaises(ConfigTypeError):
                    runtime.context.get_global_config()

    def test_resolve_config_verbose__catches_config_and_os_errors(self):
        context = Mock()
        context.global_config_dir = Path("/tmp/treefort-global-config")
        context.get_config.side_effect = ConfigTypeError("bad type")
        context.get_global_config.side_effect = OSError("cannot read")
        logger = Mock()
        log_opener = Mock(return_value=logger)

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("treefort.runtime.Context", return_value=context),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks"),
            patch("treefort.runtime.GitCLI"),
            patch("treefort.runtime.GithubCLI"),
            patch("treefort.runtime.Templates"),
        ):
            os.environ.pop("TREEFORT_VERBOSE", None)
            runtime = Runtime(name="test", verbose=False)

        self.assertFalse(runtime.verbose)
        context.get_config.assert_called_once_with()
        context.get_global_config.assert_called_once_with()
        self.assertEqual(
            [call[0][0] for call in logger.warning.call_args_list],
            [
                "Ignoring invalid repository configuration for verbose mode: bad type",
                "Ignoring invalid global configuration for verbose mode: cannot read",
            ],
        )

    def test_get_remote__no_args_raises(self):
        with self.assertRaises(RemoteUsageError, msg="Must provide either owner_name or name"):
            self.runtime.get_remote()

    def test_get_remote__owner_name(self):
        remote = self.runtime.get_remote(owner_name="octo")
        self.assertEqual(remote, self.remotes[0])

    def test_get_remote__owner_name_ignores_prefixed_repo_name(self):
        self.remotes = [
            GitRemote(
                "repo-ghsa-j64v-3ewp-1234",
                "git@github.com:octo/repo-ghsa-j64v-3ewp-1234.git",
                "fetch",
            ),
            GitRemote(
                "repo-ghsa-j64v-3ewp-1234",
                "git@github.com:octo/repo-ghsa-j64v-3ewp-1234.git",
                "push",
            ),
            GitRemote("origin", "git@github.com:other/repo.git", "fetch"),
            GitRemote("origin", "git@github.com:other/repo.git", "push"),
            GitRemote("upstream", "https://github.com/octo/repo.git", "fetch"),
            GitRemote("upstream", "https://github.com/octo/repo.git", "push"),
        ]

        remote = self.runtime.get_remote(owner_name="octo")
        self.assertEqual(remote, self.remotes[4])

    def test_get_remote__owner_name_without_git_extension(self):
        self.remotes = [
            GitRemote("upstream", "https://github.com/octo/repo", "fetch"),
        ]

        remote = self.runtime.get_remote(owner_name="octo")
        self.assertEqual(remote, self.remotes[0])

    def test_get_remote__owner_name_ignores_prefixed_owner_name(self):
        self.remotes = [
            GitRemote("prefixed", "git@github.com:my-octo/repo.git", "fetch"),
            GitRemote("origin", "git@github.com:octo/repo.git", "fetch"),
        ]

        remote = self.runtime.get_remote(owner_name="octo")
        self.assertEqual(remote, self.remotes[1])

    def test_get_remote__owner_name_case_insensitive_and_trailing_slash(self):
        self.config = SimpleNamespace(owner="LearningEquality", name="kolibri")
        self.remotes = [
            GitRemote("origin", "git@github.com:learningequality/Kolibri.git", "fetch"),
            GitRemote("upstream", "https://github.com/LearningEquality/KOLIBRI/", "fetch"),
        ]

        self.assertEqual(
            self.runtime.get_remote(owner_name="LearningEquality"),
            self.remotes[0],
        )
        self.assertEqual(
            self.runtime.get_remote(owner_name="LEARNINGEQUALITY"),
            self.remotes[0],
        )

    def test_get_remote__name(self):
        remote = self.runtime.get_remote(name="other")
        self.assertEqual(remote, self.remotes[2])

    def test_get_remote__not_found(self):
        remote = self.runtime.get_remote(name="test")
        self.assertIsNone(remote)

    def test_get_default_remote(self):
        remote = self.runtime.get_default_remote()
        self.assertEqual(remote, self.remotes[0])
