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
                self.get_config = Mock(return_value=SimpleNamespace(verbose=False))
                self.get_global_config = Mock(return_value=SimpleNamespace(verbose=False))
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

    def _init_runtime_with_context(
        self,
        context,
        verbose: bool = False,
        env: dict[str, str] | None = None,
    ) -> tuple[Runtime, Mock]:
        log_opener = Mock(return_value=Mock())
        with (
            patch.dict(os.environ, env or {}, clear=False),
            patch("treefort.runtime.Context", return_value=context),
            patch("treefort.runtime.setup_logger", log_opener),
            patch("treefort.runtime.Hooks"),
            patch("treefort.runtime.GitCLI"),
            patch("treefort.runtime.GithubCLI"),
            patch("treefort.runtime.Templates"),
        ):
            if not env or "TREEFORT_VERBOSE" not in env:
                os.environ.pop("TREEFORT_VERBOSE", None)
            runtime = Runtime(name="test", verbose=verbose)
        return runtime, log_opener

    def test_init__resolves_verbose_from_env_var(self):
        context = Mock()
        context.global_config_dir = Path("/tmp/treefort-global-config")
        context.get_config.return_value = SimpleNamespace(verbose=False)
        context.get_global_config.return_value = SimpleNamespace(verbose=False)

        runtime, log_opener = self._init_runtime_with_context(
            context,
            verbose=False,
            env={"TREEFORT_VERBOSE": "1"},
        )

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

        runtime, log_opener = self._init_runtime_with_context(context, verbose=False)

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

        runtime, log_opener = self._init_runtime_with_context(context, verbose=False)

        self.assertTrue(runtime.verbose)
        log_opener.assert_called_once_with(
            "test",
            log_dir=Path("/tmp/treefort-global-config/logs"),
            verbose=True,
        )

    def test_init__defaults_to_false_when_global_config_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            home_dir = tmp_path / "home"
            outside_dir = tmp_path / "outside"
            home_dir.mkdir(parents=True)
            outside_dir.mkdir(parents=True)
            log_opener = Mock(return_value=Mock())

            with (
                patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False),
                patch("treefort.context.Path.cwd", return_value=outside_dir),
                patch("treefort.runtime.setup_logger", log_opener),
            ):
                os.environ.pop("TREEFORT_VERBOSE", None)
                runtime = Runtime(name="test", verbose=False)

            self.assertFalse(runtime.verbose)
            log_opener.assert_called_once_with(
                "test",
                log_dir=home_dir / ".treefort" / "logs",
                verbose=False,
            )

    def test_init__fails_fast_when_config_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            home_dir = tmp_path / "home"
            project_dir = tmp_path / "project"
            (project_dir / ".bare").mkdir(parents=True)
            (project_dir / ".treefort").mkdir(parents=True)
            (home_dir / ".treefort").mkdir(parents=True)
            repo_config_path = project_dir / ".treefort" / "config.toml"
            global_config_path = home_dir / ".treefort" / "config.toml"
            repo_config_path.write_text("invalid = [toml", encoding="utf-8")
            global_config_path.write_text('type = "global"\nverbose = false\n', encoding="utf-8")
            log_opener = Mock(return_value=Mock())

            with (
                patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False),
                patch("treefort.context.Path.cwd", return_value=project_dir),
                patch("treefort.runtime.setup_logger", log_opener),
            ):
                os.environ.pop("TREEFORT_VERBOSE", None)
                with self.assertRaises(tomlkit.exceptions.TOMLKitError):
                    Runtime(name="test", verbose=False)

                log_opener.assert_called_once_with(
                    "test",
                    log_dir=home_dir / ".treefort" / "logs",
                    verbose=False,
                )

                # Once repository config is valid, a malformed global config also fails fast.
                log_opener.reset_mock()
                repo_config_path.write_text(
                    'type = "repository"\nverbose = true\n', encoding="utf-8"
                )
                global_config_path.write_text(
                    'type = "global"\nverbose = "invalid"\n', encoding="utf-8"
                )
                with self.assertRaisesRegex(ConfigTypeError, "verbose must be a boolean"):
                    Runtime(name="test", verbose=False)

                log_opener.assert_called_once_with(
                    "test",
                    log_dir=home_dir / ".treefort" / "logs",
                    verbose=False,
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
