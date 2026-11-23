"""Offline integration coverage for ``treefort sync``.

Every test runs against the local-Git fixture in :mod:`tests.integration.base`, so no test
reaches the network, the developer's ``HOME``, or the developer's Git configuration.
"""

import os
from pathlib import Path
from unittest.mock import patch

from treefort.commands.sync import SyncCommand
from treefort.hooks import POST_INIT

from .base import LocalRepoIntegrationTestCase

# A minimal payload with a NUL byte and invalid UTF-8, so a text round trip would corrupt it.
BINARY_TEMPLATE = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe"


class SyncIntegrationTestCase(LocalRepoIntegrationTestCase):
    """Exercises ``treefort sync`` against a real local Git remote."""

    def sync(self, *args, **kwargs):
        SyncCommand(self.runtime)(*args, **kwargs)

    def hooks_dir(self) -> Path:
        return self.runtime.context.config_dir / "hooks"

    def templates_dir(self) -> Path:
        return self.runtime.context.config_dir / "templates"

    def test_sync__extracts_binary_templates_and_executable_modes(self):
        self.commit_on_origin(
            self.default_branch,
            {".treefort/templates/script.sh": "#!/bin/sh\necho hi\n"},
            "Add executable template",
            mode=0o755,
        )
        self.commit_on_origin(
            self.default_branch,
            {".treefort/templates/logo.png": BINARY_TEMPLATE},
            "Add binary template",
        )

        self.sync()

        script = self.templates_dir() / "script.sh"
        logo = self.templates_dir() / "logo.png"
        self.assertEqual(b"#!/bin/sh\necho hi\n", script.read_bytes())
        self.assertEqual(0o755, script.stat().st_mode & 0o777)
        self.assertEqual(BINARY_TEMPLATE, logo.read_bytes())
        self.assertEqual(0o644, logo.stat().st_mode & 0o777)

    def test_sync__updates_hooks_and_keeps_local_only_files(self):
        self.commit_on_origin(
            self.default_branch,
            {".treefort/hooks/pre_init": "#!/bin/sh\necho upstream\n"},
            "Add hook",
            mode=0o755,
        )
        self.sync()

        hook_file = self.hooks_dir() / "pre_init"
        self.assertEqual(b"#!/bin/sh\necho upstream\n", hook_file.read_bytes())
        self.assertEqual(0o755, hook_file.stat().st_mode & 0o777)

        local_only = self.hooks_dir() / "local_only"
        local_only.write_bytes(b"#!/bin/sh\nexit 0\n")

        self.commit_on_origin(
            self.default_branch,
            {".treefort/hooks/pre_init": "#!/bin/sh\necho updated\n"},
            "Update hook",
            mode=0o755,
        )
        self.sync()

        self.assertEqual(b"#!/bin/sh\necho updated\n", hook_file.read_bytes())
        self.assertTrue(local_only.exists())

    def test_sync__works_from_a_worktree_subdirectory(self):
        self.runtime.git.add_worktree("feature", f"origin/{self.default_branch}")
        worktree_dir = self.project_dir / "feature"
        os.chdir(worktree_dir)
        self.runtime.context.cwd = Path.cwd()

        self.commit_on_origin(
            self.default_branch,
            {".treefort/templates/logo.png": BINARY_TEMPLATE},
            "Add binary template",
        )

        self.sync()

        self.assertEqual(BINARY_TEMPLATE, (self.templates_dir() / "logo.png").read_bytes())

    def test_sync__modified_hook_prompts_for_approval_on_next_execution(self):
        self.commit_on_origin(
            self.default_branch,
            {".treefort/hooks/post_init": "#!/bin/sh\nexit 0\n"},
            "Add hook",
            mode=0o755,
        )
        self.sync()

        with patch("treefort.hooks.input", return_value="n") as prompt:
            self.assertFalse(self.runtime.hooks.fire(POST_INIT))

        prompt.assert_called_once()
        allowed_hooks = self.runtime.context.get_global_config().allowed_hooks
        self.assertEqual("", allowed_hooks.get(str(self.hooks_dir() / "post_init"), default=""))

    def test_sync__approved_hook_runs_and_records_its_checksum(self):
        self.commit_on_origin(
            self.default_branch,
            {".treefort/hooks/post_init": "#!/bin/sh\nexit 0\n"},
            "Add hook",
            mode=0o755,
        )
        self.sync()

        with patch("treefort.hooks.input", return_value="y"):
            self.assertTrue(self.runtime.hooks.fire(POST_INIT))

        allowed_hooks = self.runtime.context.get_global_config().allowed_hooks
        self.assertNotEqual("", allowed_hooks.get(str(self.hooks_dir() / "post_init"), default=""))

    def test_sync__no_fetch_reads_the_already_fetched_ref(self):
        self.commit_on_origin(
            self.default_branch,
            {".treefort/templates/logo.png": BINARY_TEMPLATE},
            "Add binary template",
        )
        self.runtime.git.fetch()

        self.sync(fetch=False)

        self.assertEqual(BINARY_TEMPLATE, (self.templates_dir() / "logo.png").read_bytes())
