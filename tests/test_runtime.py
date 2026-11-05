from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import DEFAULT, Mock, PropertyMock, patch

from treefort.errors import RemoteUsageError
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
