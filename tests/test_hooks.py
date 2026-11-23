import hashlib
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
from unittest.mock import Mock

from treefort.context import Context
from treefort.errors import HookError, HookExistsError, ValidationError, WorktreeNotFoundError
from treefort.git import GitTreeEntry
from treefort.hooks import (
    HOOKS,
    POST_CHECKOUT,
    POST_CREATE,
    POST_INIT,
    POST_REMOVE,
    PRE_CHECKOUT,
    PRE_CREATE,
    PRE_INIT,
    PRE_REMOVE,
    ApprovalStatus,
    BaseRefHook,
    CheckoutHook,
    CreateHook,
    HookRequest,
    Hooks,
    PostInitHook,
    PreInitHook,
    WorktreeHook,
)


class HooksTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        # isolate the global configuration so tests never write to the developer's ~/.treefort
        self.home_dir = self.tmp_path / "home"
        self.home_dir.mkdir()
        home_patcher = mock.patch.dict(os.environ, {"HOME": str(self.home_dir)})
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

        self.context = Context()
        self.context.cwd = self.tmp_path
        self.hooks_path = self.tmp_path / ".treefort" / "hooks"
        self.global_hooks_path = self.home_dir / ".treefort" / "hooks"
        self.global_hooks_path.mkdir(parents=True)
        (self.tmp_path / ".bare").mkdir()
        self.context.config_dir.mkdir(parents=True, exist_ok=True)
        self.hooks_path.mkdir()

        self.hooks = Hooks(self.context, Mock())

    def tearDown(self):
        self.tmp_dir.cleanup()

    @mock.patch("treefort.hooks.input")
    def test_check_allowed__existing_hook(self, mock_input):
        global_config = self.context.get_global_config()

        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()

        global_config.allow_hook(str(hook_file), checksum)

        self.assertTrue(self.hooks._check_allowed(hook_file))

        mock_input.assert_not_called()
        self.assertEqual(global_config.allowed_hooks.get(str(hook_file)), checksum)

    @mock.patch("treefort.hooks.input")
    def test_check_allowed__prompts_and_saves(self, mock_input):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()

        mock_input.return_value = "y"

        self.assertTrue(self.hooks._check_allowed(hook_file))

        mock_input.assert_called_once()
        global_config = self.context.get_global_config()
        self.assertEqual(global_config.allowed_hooks.get(str(hook_file)), checksum)

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__allowed(self, mock_check_allowed, mock_stream_exec):
        hook_file = self.global_hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        # Make the hook file executable
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 0

        self.assertTrue(
            self.hooks.fire(PRE_INIT, "https://github.com/octo/repo.git", self.tmp_path)
        )
        mock_stream_exec.assert_called_once_with(
            [str(hook_file), "https://github.com/octo/repo.git", str(self.tmp_path)],
            env={
                "TREEFORT_HOOK": "pre_init",
                "TREEFORT_REPO_NAME": "repo",
                "TREEFORT_REPO_DIR": str(self.tmp_path),
            },
            stream_info=True,
        )

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__pre_init_ignores_a_project_hook(self, mock_check_allowed, mock_stream_exec):
        # `init` fires pre_init before the project exists, so a project-level pre_init is dead
        project_hook = self.hooks_path / PRE_INIT.name
        project_hook.write_bytes(b"echo ok")
        project_hook.chmod(project_hook.stat().st_mode | stat.S_IEXEC)

        self.assertFalse(self.hooks.fire(PRE_INIT))

        mock_check_allowed.assert_not_called()
        mock_stream_exec.assert_not_called()

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__no_hooks_skips_everything(self, mock_check_allowed, mock_stream_exec):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        self.assertFalse(self.hooks.fire(PRE_INIT, "arg1", no_hooks=True))

        mock_check_allowed.assert_not_called()
        mock_stream_exec.assert_not_called()
        self.hooks.logger.debug.assert_any_call("Skipping hook pre_init (--no-hooks)")

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__merges_env_overrides_over_built_environment(
        self, mock_check_allowed, mock_stream_exec
    ):
        hook_file = self.global_hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 0

        self.hooks.fire(
            PRE_INIT,
            "https://github.com/octo/repo.git",
            self.tmp_path,
            env={"TREEFORT_EXTRA": "1", "TREEFORT_REPO_NAME": "override"},
        )

        self.assertEqual(
            {
                "TREEFORT_HOOK": "pre_init",
                "TREEFORT_REPO_NAME": "override",
                "TREEFORT_REPO_DIR": str(self.tmp_path),
                "TREEFORT_EXTRA": "1",
            },
            mock_stream_exec.call_args.kwargs["env"],
        )

    def test_build_env__pre_init_without_arguments(self):
        self.assertEqual(
            {"TREEFORT_HOOK": "pre_init", "TREEFORT_REPO_NAME": "", "TREEFORT_REPO_DIR": ""},
            PRE_INIT.build_env(self.context),
        )

    def test_build_env__pre_init_with_uri_only(self):
        self.assertEqual(
            {"TREEFORT_HOOK": "pre_init", "TREEFORT_REPO_NAME": "repo", "TREEFORT_REPO_DIR": ""},
            PRE_INIT.build_env(self.context, "https://github.com/octo/repo.git"),
        )

    def test_build_env__pre_init_with_uri_and_project_dir(self):
        project_dir = self.tmp_path / "repo"

        self.assertEqual(
            {
                "TREEFORT_HOOK": "pre_init",
                "TREEFORT_REPO_NAME": "repo",
                "TREEFORT_REPO_DIR": str(project_dir),
            },
            PRE_INIT.build_env(self.context, "https://github.com/octo/repo.git", project_dir),
        )

    def test_build_env__pre_init_never_reads_the_project_context(self):
        # pre_init runs before the clone, so neither .bare nor config.toml exists yet
        with (
            mock.patch.object(
                Context, "project_dir", new_callable=mock.PropertyMock
            ) as project_dir,
            mock.patch.object(Context, "get_config") as get_config,
        ):
            env = PRE_INIT.build_env(self.context, "https://github.com/octo/repo.git", "/tmp/x")

        project_dir.assert_not_called()
        get_config.assert_not_called()
        self.assertEqual("/tmp/x", env["TREEFORT_REPO_DIR"])

    def test_build_env__post_init_reads_the_project_context(self):
        config = SimpleNamespace(name="repo", default_branch="develop")

        with mock.patch.object(Context, "get_config", return_value=config):
            env = POST_INIT.build_env(self.context)

        self.assertEqual(
            {
                "TREEFORT_HOOK": "post_init",
                "TREEFORT_REPO_NAME": "repo",
                "TREEFORT_REPO_DIR": str(self.tmp_path),
                "TREEFORT_DEFAULT_BRANCH": "develop",
            },
            env,
        )

    def test_build_env__post_init_defaults_missing_config_values(self):
        with mock.patch.object(Context, "get_config", return_value=SimpleNamespace()):
            env = POST_INIT.build_env(self.context)

        self.assertEqual("", env["TREEFORT_REPO_NAME"])
        self.assertEqual("main", env["TREEFORT_DEFAULT_BRANCH"])

    def test_build_env__post_create(self):
        config = SimpleNamespace(name="repo", default_branch="main")

        with mock.patch.object(Context, "get_config", return_value=config):
            env = POST_CREATE.build_env(self.context, "feature/one", "origin/main", "feature-one")

        self.assertEqual(
            {
                "TREEFORT_HOOK": "post_create",
                "TREEFORT_REPO_NAME": "repo",
                "TREEFORT_REPO_DIR": str(self.tmp_path),
                "TREEFORT_DEFAULT_BRANCH": "main",
                "TREEFORT_WORKTREE_NAME": "feature/one",
                "TREEFORT_WORKTREE_NAME_NORMALIZED": "feature-one",
                "TREEFORT_WORKTREE_DIR": str(self.tmp_path / "feature/one"),
                "TREEFORT_BASE_REF": "origin/main",
            },
            env,
        )

    def test_build_env__pre_remove(self):
        config = SimpleNamespace(name="repo", default_branch="main")

        with mock.patch.object(Context, "get_config", return_value=config):
            env = PRE_REMOVE.build_env(self.context, "feature/one", "feature-one")

        self.assertEqual(
            {
                "TREEFORT_HOOK": "pre_remove",
                "TREEFORT_REPO_NAME": "repo",
                "TREEFORT_REPO_DIR": str(self.tmp_path),
                "TREEFORT_DEFAULT_BRANCH": "main",
                "TREEFORT_WORKTREE_NAME": "feature/one",
                "TREEFORT_WORKTREE_NAME_NORMALIZED": "feature-one",
                "TREEFORT_WORKTREE_DIR": str(self.tmp_path / "feature/one"),
            },
            env,
        )

    def test_build_env__worktree_hooks_tolerate_missing_arguments(self):
        config = SimpleNamespace(name="repo", default_branch="main")

        with mock.patch.object(Context, "get_config", return_value=config):
            for hook in (PRE_CREATE, POST_CHECKOUT, PRE_REMOVE):
                with self.subTest(hook=hook):
                    env = hook.build_env(self.context)

                    self.assertEqual("", env["TREEFORT_WORKTREE_NAME"])
                    self.assertEqual("", env["TREEFORT_WORKTREE_NAME_NORMALIZED"])
                    self.assertEqual("", env["TREEFORT_WORKTREE_DIR"])

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__logs_step_header_before_streaming(self, mock_check_allowed, mock_stream_exec):
        hook_file = self.global_hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        events = []
        mock_check_allowed.return_value = True
        mock_stream_exec.side_effect = lambda *args, **kwargs: events.append("stream") or 0
        self.hooks.logger.info.side_effect = lambda *args, **kwargs: events.append("header")

        with mock.patch("treefort.ui.is_color_enabled", return_value=False):
            self.hooks.fire(PRE_INIT)

        self.assertEqual(events, ["header", "stream"])
        self.hooks.logger.info.assert_called_once_with("◎ Running hook pre_init")

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__failure_includes_last_output_in_hook_error(
        self, mock_check_allowed, mock_stream_exec
    ):
        hook_file = self.global_hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"exit 2")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 2
        self.hooks.last_output = ["permission denied: /etc/hosts"]

        with self.assertRaises(HookError) as ctx:
            self.hooks.fire(PRE_INIT, "arg1")

        self.assertIn("Hook pre_init failed with exit code 2", str(ctx.exception))
        self.assertIn("permission denied: /etc/hosts", str(ctx.exception))

    @mock.patch.object(Hooks, "_check_allowed")
    @mock.patch.object(Hooks, "stream_exec")
    def test_fire__not_executable(self, mock_stream_exec, mock_check_allowed):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(stat.S_IREAD)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 0

        self.assertFalse(self.hooks.fire(PRE_INIT, "arg1"))
        mock_stream_exec.assert_not_called()

    @mock.patch.object(Hooks, "_check_allowed")
    @mock.patch.object(Hooks, "stream_exec")
    def test_fire__disallowed(self, mock_stream_exec, mock_check_allowed):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        # Make the hook file executable
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = False
        mock_stream_exec.return_value = 0

        self.assertFalse(self.hooks.fire(PRE_INIT, "arg1"))
        mock_stream_exec.assert_not_called()
        self.hooks.logger.info.assert_not_called()

    def test_add(self):
        with self.hooks.add(PRE_INIT) as f:
            f.write("echo ok")

        hook_file = self.hooks_path / PRE_INIT.name
        self.assertTrue(hook_file.exists())
        self.assertEqual(hook_file.read_text(), "echo ok")
        self.assertTrue(os.access(str(hook_file), os.X_OK))

    def test_add__existing(self):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")

        with self.assertRaises(HookExistsError):
            with self.hooks.add(PRE_INIT) as f:
                f.write("echo ok")

    def test_approval_status__unapproved_without_a_recorded_checksum(self):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")

        self.assertEqual(ApprovalStatus.UNAPPROVED, self.hooks.approval_status(hook_file))

    def test_approval_status__approved_when_the_checksum_matches(self):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()
        self.context.get_global_config().allow_hook(str(hook_file), checksum)

        self.assertEqual(ApprovalStatus.APPROVED, self.hooks.approval_status(hook_file))

    def test_approval_status__modified_when_a_different_checksum_is_recorded(self):
        hook_file = self.hooks_path / PRE_INIT.name
        hook_file.write_bytes(b"echo ok")
        self.context.get_global_config().allow_hook(str(hook_file), "stale-checksum")

        self.assertEqual(ApprovalStatus.MODIFIED, self.hooks.approval_status(hook_file))

    def test_build_env__exports_the_documented_key_set_for_every_hook(self):
        base = {"TREEFORT_HOOK", "TREEFORT_REPO_NAME", "TREEFORT_REPO_DIR"}
        project = base | {"TREEFORT_DEFAULT_BRANCH"}
        worktree = project | {
            "TREEFORT_WORKTREE_NAME",
            "TREEFORT_WORKTREE_NAME_NORMALIZED",
            "TREEFORT_WORKTREE_DIR",
        }
        expected = {
            PRE_INIT: base,
            POST_INIT: project,
            PRE_CHECKOUT: worktree | {"TREEFORT_BASE_REF"},
            POST_CHECKOUT: worktree | {"TREEFORT_BASE_REF"},
            PRE_CREATE: worktree | {"TREEFORT_BASE_REF"},
            POST_CREATE: worktree | {"TREEFORT_BASE_REF"},
            PRE_REMOVE: worktree,
            POST_REMOVE: worktree,
        }
        config = SimpleNamespace(name="repo", default_branch="main")

        with mock.patch.object(Context, "get_config", return_value=config):
            for hook, keys in expected.items():
                with self.subTest(hook=hook):
                    env = hook.build_env(self.context, "wt", "origin/main", "wt")

                    self.assertEqual(keys, set(env))

    def test_hooks__registry_covers_every_builtin_hook_in_declaration_order(self):
        self.assertEqual(
            [
                "pre_init",
                "post_init",
                "pre_checkout",
                "post_checkout",
                "pre_create",
                "post_create",
                "pre_remove",
                "post_remove",
            ],
            list(HOOKS),
        )

    def test_hooks__types_classify_each_hook(self):
        self.assertIsInstance(PRE_INIT, PreInitHook)
        self.assertIsInstance(POST_INIT, PostInitHook)
        self.assertIsInstance(PRE_CREATE, CreateHook)
        self.assertIsInstance(POST_CREATE, CreateHook)
        self.assertIsInstance(PRE_CHECKOUT, CheckoutHook)
        self.assertIsInstance(POST_CHECKOUT, CheckoutHook)
        for hook in (PRE_REMOVE, POST_REMOVE):
            with self.subTest(hook=hook):
                self.assertIsInstance(hook, WorktreeHook)
                self.assertNotIsInstance(hook, BaseRefHook)

    def test_hooks__only_pre_init_excludes_project_hooks(self):
        self.assertEqual(["pre_init"], [hook.name for hook in HOOKS.values() if hook.skip_project])


class HookRequestTestCase(TestCase):
    """Tests for the inputs a hook turns into its positional arguments."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def request(self, **overrides):
        values = {
            "config": SimpleNamespace(url="https://github.com/octo/repo", default_branch="develop"),
            "project_dir": self.tmp_path,
            "resolve_remote": lambda: "upstream",
            "resolve_worktree": lambda: None,
        }
        values.update(overrides)
        return HookRequest(**values)

    def test_worktree_name__returns_a_named_worktree_without_resolving(self):
        resolve_worktree = Mock(return_value="current")
        request = self.request(named_worktree="named", resolve_worktree=resolve_worktree)

        self.assertEqual("named", request.worktree_name())
        resolve_worktree.assert_not_called()

    def test_worktree_name__resolves_the_current_worktree_when_none_is_named(self):
        request = self.request(resolve_worktree=lambda: "current")

        self.assertEqual("current", request.worktree_name())

    def test_worktree_name__raises_when_no_worktree_can_be_resolved(self):
        request = self.request()

        with self.assertRaisesRegex(WorktreeNotFoundError, "pass a worktree name"):
            request.worktree_name()

    def test_default_remote_name__returns_the_resolved_remote(self):
        request = self.request(resolve_remote=lambda: "upstream")

        self.assertEqual("upstream", request.default_remote_name())

    def test_default_remote_name__falls_back_to_origin(self):
        request = self.request(resolve_remote=lambda: None)

        self.assertEqual("origin", request.default_remote_name())

    def test_default_branch__returns_the_configured_branch(self):
        self.assertEqual("develop", self.request().default_branch)

    def test_default_branch__defaults_to_main_without_a_configured_branch(self):
        request = self.request(config=SimpleNamespace())

        self.assertEqual("main", request.default_branch)


class HooksSyncFromGitTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.home_dir = self.tmp_path / "home"
        self.home_dir.mkdir()
        home_patcher = mock.patch.dict(os.environ, {"HOME": str(self.home_dir)})
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

        self.context = Context()
        self.context.cwd = self.tmp_path
        (self.tmp_path / ".bare").mkdir()
        self.hooks_path = self.tmp_path / ".treefort" / "hooks"

        self.hooks = Hooks(self.context, Mock())
        self.git = Mock()
        self.git.ls_tree_entries.return_value = []
        self.git.read_blob.return_value = b""

    def tearDown(self):
        self.tmp_dir.cleanup()

    def entry(self, path, mode="100644", obj_type="blob", sha="abc123"):
        return GitTreeEntry(mode, obj_type, sha, path)

    def test_sync_from_git__writes_binary_content_and_executable_mode(self):
        payload = b"#!/bin/sh\x00\x00echo ok\xff"
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init", "100755")]
        self.git.read_blob.return_value = payload

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        hook_file = self.hooks_path / "pre_init"
        self.assertEqual(1, synced)
        self.assertEqual(payload, hook_file.read_bytes())
        self.assertEqual(0o755, hook_file.stat().st_mode & 0o777)
        self.git.ls_tree_entries.assert_called_once_with("origin/main", ".treefort/hooks")
        self.git.read_blob.assert_called_once_with("abc123")

    def test_sync_from_git__overwrite_false_skips_existing_file(self):
        hook_file = self.hooks_path / "post_init"
        self.hooks_path.mkdir(parents=True)
        hook_file.write_bytes(b"local hook")
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/post_init")]

        synced = self.hooks.sync_from_git(self.git, "origin/main", overwrite=False)

        self.assertEqual(0, synced)
        self.assertEqual(b"local hook", hook_file.read_bytes())
        self.git.read_blob.assert_not_called()
        self.hooks.logger.warning.assert_called_once()
        self.assertIn("already exists", self.hooks.logger.warning.call_args.args[0])

    def test_sync_from_git__warns_when_syncing_a_global_only_hook(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init")]

        self.hooks.sync_from_git(self.git, "origin/main")

        self.hooks.logger.warning.assert_called_once_with(
            "Hook pre_init is global-only; the synced project copy will never run."
        )

    def test_sync_from_git__does_not_warn_for_a_project_scoped_hook(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/post_init")]

        self.hooks.sync_from_git(self.git, "origin/main")

        self.hooks.logger.warning.assert_not_called()

    def test_sync_from_git__overwrite_true_replaces_existing_file(self):
        hook_file = self.hooks_path / "pre_init"
        self.hooks_path.mkdir(parents=True)
        hook_file.write_bytes(b"local hook")
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init")]
        self.git.read_blob.return_value = b"upstream hook"

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(1, synced)
        self.assertEqual(b"upstream hook", hook_file.read_bytes())

    def test_sync_from_git__skips_unknown_and_nested_hook_names(self):
        self.git.ls_tree_entries.return_value = [
            self.entry(".treefort/hooks/custom_hook"),
            self.entry(".treefort/hooks/nested/pre_init"),
        ]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()
        self.assertFalse(self.hooks_path.exists())

    def test_sync_from_git__skips_symlink_and_non_blob_entries(self):
        self.git.ls_tree_entries.return_value = [
            self.entry(".treefort/hooks/pre_init", "120000"),
            self.entry(".treefort/hooks/post_init", obj_type="tree"),
        ]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__skips_paths_outside_the_hooks_prefix(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/templates/pre_init")]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__skips_a_name_that_starts_with_dots(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/..foo")]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()
        self.assertFalse(self.hooks_path.exists())

    def test_sync_from_git__raises_validation_error_on_parent_traversal(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/../evil")]

        with self.assertRaises(ValidationError):
            self.hooks.sync_from_git(self.git, "origin/main")

        self.assertFalse((self.tmp_path / ".treefort" / "evil").exists())
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__raises_validation_error_when_path_resolves_outside(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/nested/../../evil")]

        with self.assertRaises(ValidationError):
            self.hooks.sync_from_git(self.git, "origin/main")

        self.assertFalse((self.tmp_path / ".treefort" / "evil").exists())
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__leaves_allowed_hooks_untouched(self):
        allowed_hooks_file = self.home_dir / ".treefort" / "allowed_hooks.json"
        self.context.get_global_config()
        before = allowed_hooks_file.read_bytes()

        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init", "100755")]
        self.git.read_blob.return_value = b"#!/bin/sh\necho ok\n"

        self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(before, allowed_hooks_file.read_bytes())
        global_config = self.context.get_global_config()
        self.assertEqual(
            "", global_config.allowed_hooks.get(str(self.hooks_path / "pre_init"), default="")
        )
