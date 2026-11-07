"""Reusable fixture for integration tests backed by a real local Git repository.

The fixture builds a throwaway tree in a temporary directory that mirrors what
``treefort init`` produces, so tests can exercise real ``git`` behaviour without
touching the network::

    <temp>/home/                  isolated HOME, holds ~/.treefort configuration
    <temp>/origin.git             bare repository standing in for GitHub
    <temp>/project/               the initialized treefort project directory
        .bare/                    bare clone of ``origin.git``
        .git                      ``gitdir: ./.bare`` pointer file
        .treefort/config.toml     repository configuration

The ``origin`` remote resolves to the local bare repository, so no test can reach the network.
GitHub-shaped information that ``treefort`` reads from configuration, rather than from the remote,
is still populated: the repository owner, name, and URL mirror a real project.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from treefort.config import RepositoryConfig, TomlConfigIO
from treefort.errors import CommandError
from treefort.gh import GithubCLI
from treefort.git import GitRemote
from treefort.runtime import Runtime

TEST_AUTHOR_NAME = "Treefort Integration"
TEST_AUTHOR_EMAIL = "treefort-integration@example.com"


class _IntegrationRuntime(Runtime):
    """Runtime whose default remote resolves to the local ``origin`` bare repository."""

    __slots__ = ()

    def get_remote(
        self, name: str | None = None, owner_name: str | None = None
    ) -> GitRemote | None:
        remote = super().get_remote(name=name, owner_name=owner_name)
        if remote is None and owner_name:
            config = self.context.get_config()
            if config.owner and owner_name.lower() == config.owner.lower():
                return super().get_remote(name="origin")
        return remote


def _as_text(value: str | bytes | None) -> str:
    """Decode captured Git output, tolerating binary content."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _git_file_mode(mode: int) -> str:
    """Translate a POSIX file mode into the mode Git records for a file entry."""
    return "100755" if mode & 0o111 else "100644"


class LocalRepoIntegrationTestCase(TestCase):
    """Test case with an offline local-Git project and an isolated ``HOME``.

    Subclasses may override :attr:`owner`, :attr:`repo_name`, and :attr:`default_branch` to
    change the shape of the fixture before ``setUp`` runs.
    """

    owner = "octo"
    repo_name = "repo"
    default_branch = "main"

    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="treefort-integration-")
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._restore_cwd)
        self.temp_path = Path(self.temp_dir.name).resolve()
        self.home_dir = self.temp_path / "home"
        self.home_dir.mkdir()
        self.origin_dir = self.temp_path / "origin.git"
        self.project_dir = self.temp_path / "project"

        self._logging_state = self._snapshot_logging()
        self.addCleanup(self._restore_logging)

        env_patcher = patch.dict(os.environ, self._git_environment())
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self._init_origin()
        self._init_project()

        os.chdir(self.project_dir)
        self.runtime = _IntegrationRuntime(name="treefort-integration", verbose=False)
        self.runtime.gh = MagicMock(spec=GithubCLI)

    def tearDown(self):
        # Run before test-registered addCleanup hooks (e.g. assertions on Path.cwd()) and guard
        # against subclasses overriding tearDown; setUp's addCleanup hooks cover setUp failures.
        self._restore_cwd()
        self._restore_logging()

    def commit_on_origin(
        self,
        branch: str,
        files: dict[str, bytes | str],
        message: str,
        mode: int = 0o644,
    ) -> str:
        """Commit files directly to the bare origin repository.

        Simulates an upstream change, such as a commit on the default branch, the addition of
        ``.treefort/hooks`` or ``.treefort/templates`` files, or a pull request head. ``branch``
        may be a branch name or an explicit ref such as ``refs/pull/7/head``. The commit is
        parented on the current tip of that ref, or on the default branch tip when the ref does
        not exist yet.

        :param branch: Branch name, or fully qualified ref, to commit to
        :param files: Mapping of repository-relative path to file content
        :param message: Commit message
        :param mode: POSIX file mode recorded for every file in the commit
        :return: The SHA of the new commit
        """
        ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
        parent = self._origin_head(ref) or self._origin_head(f"refs/heads/{self.default_branch}")
        if parent is None:
            raise CommandError(f"Cannot commit to {ref}: origin has no commits")
        return self._create_origin_commit(ref, files, message, parent=parent, mode=mode)

    def commit_in_worktree(
        self, worktree_name: str, files: dict[str, bytes | str], message: str
    ) -> str:
        """Write files inside a managed worktree and commit them on its branch.

        :param worktree_name: Name of the worktree directory inside the project directory
        :param files: Mapping of worktree-relative path to file content
        :param message: Commit message
        :return: The SHA of the new commit
        """
        worktree_dir = self.project_dir / worktree_name
        for relative_path, content in files.items():
            path = worktree_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")

        self._run_git(["add", "-A"], cwd=worktree_dir)
        self._run_git(["commit", "-m", message], cwd=worktree_dir)
        return _as_text(self._run_git(["rev-parse", "HEAD"], cwd=worktree_dir).stdout).strip()

    def _git_environment(self) -> dict[str, str]:
        """Environment that keeps Git deterministic and independent of the developer's setup."""
        return {
            "HOME": str(self.home_dir),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.home_dir / ".gitconfig"),
            "GIT_AUTHOR_NAME": TEST_AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": TEST_AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": TEST_AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": TEST_AUTHOR_EMAIL,
        }

    def _init_origin(self):
        self._run_git(
            ["init", "--bare", "--initial-branch", self.default_branch, str(self.origin_dir)],
            cwd=self.temp_path,
        )
        self._origin_git(["config", "commit.gpgsign", "false"])
        self._create_origin_commit(
            f"refs/heads/{self.default_branch}",
            {"README.md": f"# {self.repo_name}\n"},
            "Initial commit",
            parent=None,
        )

    def _init_project(self):
        self.project_dir.mkdir()
        self._run_git(
            ["clone", "--bare", "--quiet", str(self.origin_dir), str(self.project_dir / ".bare")],
            cwd=self.temp_path,
        )
        (self.project_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")

        github_uri = f"https://github.com/{self.owner}/{self.repo_name}.git"
        self._run_git(
            ["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=self.project_dir,
        )
        self._run_git(["config", "commit.gpgsign", "false"], cwd=self.project_dir)
        self._run_git(["fetch", "--quiet", "origin"], cwd=self.project_dir)

        config = RepositoryConfig(TomlConfigIO.from_path(self.project_dir / ".treefort", "config"))
        config.update(
            default_branch=self.default_branch,
            owner=self.owner,
            name=self.repo_name,
            url=github_uri.removesuffix(".git"),
            is_private=False,
        )
        config.save()

    def _create_origin_commit(
        self,
        ref: str,
        files: dict[str, bytes | str],
        message: str,
        parent: str | None,
        mode: int = 0o644,
    ) -> str:
        """Build a commit in the bare origin repository with Git plumbing commands."""
        index_dir = Path(tempfile.mkdtemp(dir=self.temp_path))
        env = {"GIT_INDEX_FILE": str(index_dir / "index")}
        if parent is not None:
            self._origin_git(["read-tree", parent], env=env)

        for relative_path, content in files.items():
            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            blob = self._origin_git(
                ["hash-object", "-w", "--stdin"], env=env, input_bytes=content_bytes
            )
            self._origin_git(
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    _git_file_mode(mode),
                    _as_text(blob.stdout).strip(),
                    relative_path,
                ],
                env=env,
            )

        tree = _as_text(self._origin_git(["write-tree"], env=env).stdout).strip()
        commit_args = ["commit-tree", tree]
        if parent is not None:
            commit_args.extend(["-p", parent])
        commit_args.extend(["-m", message])
        commit = _as_text(self._origin_git(commit_args).stdout).strip()
        self._origin_git(["update-ref", ref, commit])
        return commit

    def _origin_head(self, ref: str) -> str | None:
        """Return the commit a ref points at in the origin repository, if it exists."""
        result = self._origin_git(["rev-parse", "--verify", "--quiet", ref], check=False)
        if result.returncode != 0:
            return None
        return _as_text(result.stdout).strip() or None

    def _origin_git(
        self,
        args: list[str],
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        return self._run_git(
            ["--git-dir", str(self.origin_dir), *args],
            cwd=self.temp_path,
            env=env,
            input_bytes=input_bytes,
            check=check,
        )

    def _run_git(
        self,
        args: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a Git command with the isolated environment and fail loudly on errors."""
        merged_env = {**os.environ, **(env or {})}
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.project_dir),
            env=merged_env,
            input=input_bytes,
            capture_output=True,
            # binary payloads, such as template images, must not be decoded
            text=input_bytes is None,
            check=False,
        )
        if check and result.returncode != 0:
            raise CommandError(
                f"git {' '.join(args)} failed with exit status {result.returncode}: "
                f"{_as_text(result.stderr).strip()}"
            )
        return result

    def _snapshot_logging(self) -> tuple[list[logging.Handler], int]:
        root = logging.getLogger()
        return list(root.handlers), root.level

    def _restore_cwd(self):
        if Path.cwd() != self.original_cwd:
            os.chdir(self.original_cwd)

    def _restore_logging(self):
        """Undo the root logger reconfiguration performed while creating the runtime."""
        handlers, level = self._logging_state
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)
