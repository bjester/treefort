"""Integration tests for removing the worktree the process is standing in."""

import os
from pathlib import Path
from unittest.mock import patch

from treefort.commands.remove import RemoveCommand

from .base import LocalRepoIntegrationTestCase


class RemoveIntegrationTestCase(LocalRepoIntegrationTestCase):
    def test_remove__ends_in_project_dir_when_removing_the_active_worktree(self):
        self.runtime.git.add_worktree("feature-a", "main")
        worktree_dir = self.project_dir / "feature-a"
        cd_file = self.temp_path / "cd-file"
        os.chdir(worktree_dir)
        self.runtime.context.cwd = worktree_dir

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            RemoveCommand(self.runtime)("feature-a", yes=True)

        # The command deletes the directory it was standing in, so the shell is directed to the
        # project root and the process ends there instead of in a deleted directory.
        self.assertFalse(worktree_dir.exists())
        self.assertEqual(Path.cwd(), self.project_dir.resolve())
        self.assertEqual(self.runtime.context.cwd, self.project_dir.resolve())
        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir.resolve()}\n")

    def test_remove__restores_cwd_when_removing_another_worktree(self):
        self.runtime.git.add_worktree("feature-a", "main")
        self.runtime.git.add_worktree("feature-b", "main")
        active_dir = self.project_dir / "feature-a"
        cd_file = self.temp_path / "cd-file"
        os.chdir(active_dir)
        self.runtime.context.cwd = active_dir

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            RemoveCommand(self.runtime)("feature-b", yes=True)

        # Only the removed worktree disappears; the shell stays where the user was.
        self.assertFalse((self.project_dir / "feature-b").exists())
        self.assertEqual(Path.cwd(), active_dir)
        self.assertFalse(cd_file.exists())
