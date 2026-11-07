from pathlib import Path
from unittest.mock import Mock

from .base import LocalRepoIntegrationTestCase


class LocalRepoIntegrationFixtureTestCase(LocalRepoIntegrationTestCase):
    """Validates the fixture itself, so failures in dependent tests stay interpretable."""

    def test_setUp__initializes_isolated_bare_repo_and_home(self):
        self.assertTrue(self.origin_dir.is_dir())
        self.assertTrue((self.project_dir / ".bare").is_dir())
        self.assertEqual(
            (self.project_dir / ".git").read_text(encoding="utf-8"), "gitdir: ./.bare\n"
        )
        self.assertTrue((self.project_dir / ".treefort" / "config.toml").is_file())
        self.assertEqual(Path.cwd(), self.project_dir)
        self.assertEqual(self.runtime.context.cwd, self.project_dir)

        config = self.runtime.context.get_config()
        self.assertEqual(config.default_branch, self.default_branch)
        self.assertEqual(config.owner, self.owner)
        self.assertEqual(config.name, self.repo_name)
        self.assertEqual(config.url, f"https://github.com/{self.owner}/{self.repo_name}")

        self.assertEqual(Path.home(), self.home_dir)
        self.assertEqual(self.runtime.context.global_config_dir, self.home_dir / ".treefort")
        self.assertTrue((self.home_dir / ".treefort" / "config.toml").is_file())
        self.assertTrue((self.home_dir / ".treefort" / "allowed_hooks.json").is_file())

        self.assertIsInstance(self.runtime.gh.merged_pr_by_head, Mock)

        remotes = self.runtime.git.remote()
        self.assertEqual({remote.name for remote in remotes}, {"origin"})
        self.assertEqual({remote.uri for remote in remotes}, {str(self.origin_dir)})
        self.assertEqual(self.runtime.get_default_remote().name, "origin")
        self.assertEqual(self.runtime.get_remote(owner_name=self.owner).name, "origin")
        self.assertIsNone(self.runtime.get_remote(owner_name="other-owner"))

        head = self.runtime.git.get_branch_head(self.default_branch)
        self.assertEqual(len(head), 40)
        self.assertEqual(self.runtime.git.get_branch_head(f"origin/{self.default_branch}"), head)

    def test_setUp__restores_working_directory_on_tearDown(self):
        # cleanups registered by a test run after tearDown, which is when the
        # working directory must already be back where the test found it
        self.addCleanup(lambda: self.assertEqual(self.original_cwd, Path.cwd()))

    def test_commit_on_origin__pushes_commit_to_bare_origin(self):
        before = self.runtime.git.get_branch_head(f"origin/{self.default_branch}")

        sha = self.commit_on_origin(self.default_branch, {"CHANGELOG.md": "notes\n"}, "Add notes")

        self.assertEqual(len(sha), 40)
        self.runtime.git.fetch()

        self.assertEqual(self.runtime.git.get_branch_head(f"origin/{self.default_branch}"), sha)
        self.assertEqual(
            list(self.runtime.git.cat_file(f"origin/{self.default_branch}", "CHANGELOG.md")),
            ["notes"],
        )
        self.assertEqual(self.runtime.git.get_branch_head(f"{sha}^"), before)

    def test_commit_on_origin__creates_branch_with_requested_file_mode(self):
        sha = self.commit_on_origin(
            "feature", {"script.sh": "#!/bin/sh\necho hi\n"}, "Add script", mode=0o755
        )

        self.runtime.git.fetch()

        self.assertEqual(self.runtime.git.get_branch_head("origin/feature"), sha)
        entries = list(self.runtime.git.ls_tree("origin/feature", "script.sh"))
        self.assertEqual(len(entries), 1)
        mode_info, path = entries[0].split("\t")
        self.assertEqual(mode_info.split()[:2], ["100755", "blob"])
        self.assertEqual(path, "script.sh")

    def test_commit_on_origin__supports_pull_request_refs(self):
        sha = self.commit_on_origin("refs/pull/7/head", {"README.md": "PR body\n"}, "PR commit")

        self.runtime.git.fetch("origin", "+refs/pull/*/head:refs/remotes/origin/pr/*")

        self.assertEqual(self.runtime.git.get_branch_head("origin/pr/7"), sha)
        self.assertEqual(list(self.runtime.git.cat_file("origin/pr/7", "README.md")), ["PR body"])

    def test_commit_in_worktree__creates_commit_in_worktree(self):
        base = self.runtime.git.get_branch_head(f"origin/{self.default_branch}")
        self.runtime.git.add_worktree("feature", f"origin/{self.default_branch}")

        sha = self.commit_in_worktree("feature", {"notes.md": "hello\n"}, "Add notes")

        self.assertEqual(len(sha), 40)
        self.assertEqual(
            (self.project_dir / "feature" / "notes.md").read_text(encoding="utf-8"), "hello\n"
        )
        self.assertEqual(self.runtime.git.get_branch_head("feature"), sha)
        self.assertEqual(self.runtime.git.get_branch_head(f"{sha}^"), base)
