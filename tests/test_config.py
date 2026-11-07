"""Tests for configuration utilities."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

import tomlkit

from treefort.config import (
    AllowedHooksConfig,
    ConfigManager,
    ConfigProtocol,
    GlobalConfig,
    JsonConfigIO,
    RepositoryConfig,
    TomlConfigIO,
)
from treefort.errors import ConfigTypeError


class ConfigProtocolTestCase(TestCase):
    def test_save__is_declared(self):
        self.assertTrue(hasattr(ConfigProtocol, "save"))

    def test_load__is_declared(self):
        self.assertTrue(hasattr(ConfigProtocol, "load"))


class JsonConfigIOTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_init__raises_for_invalid_extension(self):
        with self.assertRaises(ConfigTypeError):
            JsonConfigIO(self.tmp_path / "config.toml")

    def test_read__missing_file_returns_empty_dict(self):
        io = JsonConfigIO(self.tmp_path / "config.json")
        self.assertEqual(io.read(), {})

    def test_write__round_trips_data(self):
        io = JsonConfigIO(self.tmp_path / "config.json")
        io.write({"type": "repository", "owner": "octo"})

        self.assertEqual(io.read(), {"type": "repository", "owner": "octo"})


class TomlConfigIOTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_read__missing_file_returns_empty_dict(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        self.assertEqual(io.read(), {})

    def test_write__round_trips_data(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        io.write({"type": "repository", "owner": "octo"})

        self.assertEqual(io.read(), {"type": "repository", "owner": "octo"})


class RepositoryConfigTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_load__raises_for_invalid_type(self):
        io = JsonConfigIO(self.tmp_path / "config.json")
        io.write({"type": "global"})
        config = RepositoryConfig(io)

        with self.assertRaises(ConfigTypeError):
            config.load()

    def test_save__round_trips_values(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(owner="octo", name="repo")
        config.save()

        loaded = RepositoryConfig(io)
        loaded.load()
        self.assertEqual(loaded.get("owner"), "octo")
        self.assertEqual(loaded.get("name"), "repo")

    def test_verbose__defaults_to_false_and_reads_boolean(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        self.assertFalse(config.verbose)

        config.update(verbose=True)
        config.save()

        loaded = RepositoryConfig(io)
        loaded.load()
        self.assertTrue(loaded.verbose)

        loaded.update(verbose="no")
        with self.assertRaisesRegex(ConfigTypeError, "verbose must be a boolean, got str"):
            _ = loaded.verbose


class GlobalConfigTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save__persists_global_and_allowed_hooks_config(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        allowed_hooks = AllowedHooksConfig(allowed_hooks_io)
        config = GlobalConfig(global_io, allowed_hooks)
        config.update(allowed_envvars=["TOKEN"])
        config.allow_hook("/tmp/hook.sh", "abc123")
        config.save()

        loaded_global = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        loaded_global.load()
        loaded_global.allowed_hooks.load()

        self.assertEqual(loaded_global.allowed_envvars, ["TOKEN"])
        self.assertEqual(loaded_global.allowed_hooks.get("/tmp/hook.sh"), "abc123")

    def test_verbose__defaults_to_false_and_reads_boolean(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        config = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        self.assertFalse(config.verbose)

        config.update(verbose=True)
        config.save()

        loaded = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        loaded.load()
        self.assertTrue(loaded.verbose)

        loaded.update(verbose=[1])
        with self.assertRaisesRegex(ConfigTypeError, "verbose must be a boolean, got list"):
            _ = loaded.verbose


class ConfigManagerTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.config_dir = self.tmp_path / ".treefort"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_get_config__missing_config_does_not_raise(self):
        config = ConfigManager().get_config(self.config_dir)
        self.assertEqual(config.default_branch, "main")
        self.assertIsNone(config.owner)

    def test_get_config__failed_load_does_not_cache_partial_config(self):
        (self.config_dir / "config.toml").write_text('type = "global"\n', encoding="utf-8")
        manager = ConfigManager()

        with self.assertRaises(ConfigTypeError):
            manager.get_config(self.config_dir)
        with self.assertRaises(ConfigTypeError):
            manager.get_config(self.config_dir)

    def test_get_global_config__failed_load_does_not_cache_partial_config(self):
        (self.config_dir / "config.toml").write_text('type = "repository"\n', encoding="utf-8")
        manager = ConfigManager()

        with self.assertRaises(ConfigTypeError):
            manager.get_global_config(self.config_dir)
        with self.assertRaises(ConfigTypeError):
            manager.get_global_config(self.config_dir)

    def test_get_global_config__missing_config_does_not_raise(self):
        config = ConfigManager().get_global_config(self.config_dir)
        self.assertEqual(config.allowed_envvars, [])
        self.assertTrue((self.config_dir / "allowed_hooks.json").exists())

    def test_get_config__migrates_json_to_toml(self):
        (self.config_dir / "config.json").write_text(
            json.dumps(
                {
                    "type": "repository",
                    "default_branch": "main",
                    "owner": "octo",
                    "name": "repo",
                }
            ),
            encoding="utf-8",
        )

        config = ConfigManager().get_config(self.config_dir)

        self.assertEqual(config.owner, "octo")
        self.assertTrue((self.config_dir / "config.toml").exists())

    def test_get_global_config__loads_allowed_hooks_file(self):
        (self.config_dir / "config.toml").write_text(
            tomlkit.dumps({"type": "global"}),
            encoding="utf-8",
        )
        (self.config_dir / "allowed_hooks.json").write_text(
            json.dumps(
                {
                    "type": "allowed_hooks",
                    "hooks": {
                        "/tmp/hook.sh": "abc123",
                    },
                }
            ),
            encoding="utf-8",
        )

        config = ConfigManager().get_global_config(self.config_dir)

        self.assertEqual(config.allowed_hooks.get("/tmp/hook.sh"), "abc123")

    def test_get_global_config__migrates_embedded_allowed_hooks(self):
        (self.config_dir / "config.toml").write_text(
            tomlkit.dumps(
                {
                    "type": "global",
                    "allowed_hooks": {"/tmp/hook.sh": "abc123"},
                }
            ),
            encoding="utf-8",
        )

        config = ConfigManager().get_global_config(self.config_dir)

        self.assertEqual(config.allowed_hooks.get("/tmp/hook.sh"), "abc123")
        self.assertTrue((self.config_dir / "allowed_hooks.json").exists())

    def test_save_config__allowed_hooks_without_cached_global_config_saves(self):
        manager = ConfigManager()
        allowed_hooks = AllowedHooksConfig(JsonConfigIO(self.config_dir / "allowed_hooks.json"))
        allowed_hooks.update({"/tmp/hook.sh": "abc123"})

        manager.save_config(allowed_hooks)

        document = json.loads((self.config_dir / "allowed_hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            document,
            {"type": "allowed_hooks", "hooks": {"/tmp/hook.sh": "abc123"}},
        )

    def test_save_config__allowed_hooks_updates_cached_global_config(self):
        manager = ConfigManager()
        global_config = manager.get_global_config(self.config_dir)

        allowed_hooks = AllowedHooksConfig(JsonConfigIO(self.config_dir / "allowed_hooks.json"))
        manager.save_config(allowed_hooks)

        self.assertIs(global_config.allowed_hooks, allowed_hooks)

    def test_save_config__repository_updates_cached_instance(self):
        manager = ConfigManager()
        repo = RepositoryConfig(TomlConfigIO(self.tmp_path / "config.toml"))
        repo.update(owner="octo")

        manager.save_config(repo)

        cached = manager.get_config(self.tmp_path)
        self.assertIs(cached, repo)

    def test_save_config__unknown_type_raises(self):
        class UnknownConfig:
            def save(self):
                return None

        with self.assertRaises(ValueError):
            ConfigManager().save_config(UnknownConfig())
