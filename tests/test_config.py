"""Tests for configuration utilities."""

import json
import tempfile
from pathlib import Path
from types import MappingProxyType
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

    def test_last_worktree__defaults_to_none(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        self.assertIsNone(config.last_worktree)

    def test_last_worktree__treats_an_empty_string_as_unset(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(last_worktree="")

        self.assertIsNone(config.last_worktree)

    def test_last_worktree__round_trips_a_nested_name(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(last_worktree="subpath/feature")
        config.save()

        loaded = RepositoryConfig(io)
        loaded.load()
        self.assertEqual("subpath/feature", loaded.last_worktree)

    def test_last_worktree__raises_for_an_integer(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(last_worktree=125)

        with self.assertRaisesRegex(ConfigTypeError, "last_worktree must be a string, got int"):
            _ = config.last_worktree

    def test_last_worktree__raises_for_a_boolean(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(last_worktree=True)

        with self.assertRaisesRegex(ConfigTypeError, "last_worktree must be a string, got bool"):
            _ = config.last_worktree

    def test_template_cow_patterns__defaults_to_empty_list_when_missing(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        self.assertEqual(config.template_cow_patterns, [])

        config.update(templates={"raw": ["*.env"]})
        self.assertEqual(config.template_cow_patterns, [])

    def test_template_raw_patterns__defaults_to_empty_list_when_missing(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        self.assertEqual(config.template_raw_patterns, [])

        config.update(templates={"cow": ["*.db"]})
        self.assertEqual(config.template_raw_patterns, [])

    def test_template_cow_patterns__round_trips_toml_and_returns_fresh_copy(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(templates={"cow": ["*.db", "data/**"]})
        config.save()

        loaded = RepositoryConfig(io)
        loaded.load()

        patterns = loaded.template_cow_patterns
        self.assertEqual(patterns, ["*.db", "data/**"])
        self.assertIs(type(patterns), list)
        self.assertEqual([type(pattern) for pattern in patterns], [str, str])
        self.assertTrue(Path("app.db").match(patterns[0]))

        patterns.append("mutated")
        self.assertEqual(loaded.template_cow_patterns, ["*.db", "data/**"])

    def test_template_raw_patterns__round_trips_toml_and_returns_fresh_copy(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        config.update(templates={"raw": [".env", "certs/*.pem"]})
        config.save()

        loaded = RepositoryConfig(io)
        loaded.load()

        patterns = loaded.template_raw_patterns
        self.assertEqual(patterns, [".env", "certs/*.pem"])
        self.assertIs(type(patterns), list)
        self.assertEqual([type(pattern) for pattern in patterns], [str, str])
        self.assertTrue(Path(".env").match(patterns[0]))

        patterns.append("mutated")
        self.assertEqual(loaded.template_raw_patterns, [".env", "certs/*.pem"])

    def test_template_cow_patterns__accepts_non_dict_mapping(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)
        proxy = MappingProxyType({"cow": ["*.db"]})
        self.assertNotIsInstance(proxy, dict)
        config.update(templates=proxy)

        patterns = config.template_cow_patterns
        self.assertEqual(patterns, ["*.db"])
        self.assertEqual([type(pattern) for pattern in patterns], [str])

        (self.tmp_path / "config.toml").write_text(
            'type = "repository"\n\n'
            "[templates.extra]\n"
            "flag = true\n\n"
            "[other]\n"
            "value = 1\n\n"
            "[templates]\n"
            'cow = ["*.db"]\n',
            encoding="utf-8",
        )
        loaded = RepositoryConfig(io)
        loaded.load()

        self.assertIsNot(type(loaded.get("templates")), dict)
        out_of_order_patterns = loaded.template_cow_patterns
        self.assertEqual(out_of_order_patterns, ["*.db"])
        self.assertEqual([type(pattern) for pattern in out_of_order_patterns], [str])

    def test_template_cow_patterns__raises_for_malformed_toml_loaded_from_disk(self):
        config_file = self.tmp_path / "config.toml"
        io = TomlConfigIO(config_file)

        cases = [
            (
                'type = "repository"\n\n[[templates]]\ncow = ["*.db"]\n',
                r"templates must be a table, got ",
            ),
            (
                'type = "repository"\n\n[templates]\ncow = "*.db"\n',
                r"templates\.cow must be a list of strings, got ",
            ),
            (
                'type = "repository"\n\n[templates]\ncow = ["*.db", 123]\n',
                r"templates\.cow entries must be strings, got ",
            ),
        ]
        for toml_text, error_pattern in cases:
            with self.subTest(toml=toml_text):
                config_file.write_text(toml_text, encoding="utf-8")
                loaded = RepositoryConfig(io)
                loaded.load()
                with self.assertRaisesRegex(ConfigTypeError, error_pattern):
                    _ = loaded.template_cow_patterns

    def test_template_cow_patterns__raises_when_templates_is_not_a_mapping(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [("bad", "str"), ([1], "list")]:
            with self.subTest(templates=invalid_value):
                config.update(templates=invalid_value)
                with self.assertRaisesRegex(
                    ConfigTypeError, rf"templates must be a table, got {type_name}"
                ):
                    _ = config.template_cow_patterns

    def test_template_raw_patterns__raises_when_templates_is_not_a_mapping(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [("bad", "str"), ([1], "list")]:
            with self.subTest(templates=invalid_value):
                config.update(templates=invalid_value)
                with self.assertRaisesRegex(
                    ConfigTypeError, rf"templates must be a table, got {type_name}"
                ):
                    _ = config.template_raw_patterns

    def test_template_cow_patterns__raises_when_value_is_not_a_list(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [
            ("*.db", "str"),
            (None, "NoneType"),
            ({"a": "b"}, "dict"),
        ]:
            with self.subTest(cow=invalid_value):
                config.update(templates={"cow": invalid_value})
                with self.assertRaisesRegex(
                    ConfigTypeError,
                    rf"templates\.cow must be a list of strings, got {type_name}",
                ):
                    _ = config.template_cow_patterns

    def test_template_raw_patterns__raises_when_value_is_not_a_list(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [
            ("*.bin", "str"),
            (None, "NoneType"),
            ({"a": "b"}, "dict"),
        ]:
            with self.subTest(raw=invalid_value):
                config.update(templates={"raw": invalid_value})
                with self.assertRaisesRegex(
                    ConfigTypeError,
                    rf"templates\.raw must be a list of strings, got {type_name}",
                ):
                    _ = config.template_raw_patterns

    def test_template_cow_patterns__raises_when_entries_contain_non_strings(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [
            ([1], "int"),
            (["*.db", 2], "int"),
            (["*.db", None], "NoneType"),
            ([True], "bool"),
        ]:
            with self.subTest(cow=invalid_value):
                config.update(templates={"cow": invalid_value})
                with self.assertRaisesRegex(
                    ConfigTypeError,
                    rf"templates\.cow entries must be strings, got {type_name}",
                ):
                    _ = config.template_cow_patterns

    def test_template_raw_patterns__raises_when_entries_contain_non_strings(self):
        io = TomlConfigIO(self.tmp_path / "config.toml")
        config = RepositoryConfig(io)

        for invalid_value, type_name in [
            ([1], "int"),
            (["*.bin", 2], "int"),
            (["*.bin", None], "NoneType"),
            ([False], "bool"),
        ]:
            with self.subTest(raw=invalid_value):
                config.update(templates={"raw": invalid_value})
                with self.assertRaisesRegex(
                    ConfigTypeError,
                    rf"templates\.raw entries must be strings, got {type_name}",
                ):
                    _ = config.template_raw_patterns


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

    def test_template_cow_patterns__defaults_to_empty_list_and_round_trips_toml(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        config = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        self.assertEqual(config.template_cow_patterns, [])

        config.update(templates={"cow": ["*.sqlite", "cache/**"]})
        config.save()

        loaded = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        loaded.load()

        patterns = loaded.template_cow_patterns
        self.assertEqual(patterns, ["*.sqlite", "cache/**"])
        self.assertIs(type(patterns), list)
        self.assertEqual([type(pattern) for pattern in patterns], [str, str])
        self.assertTrue(Path("dev.sqlite").match(patterns[0]))

        patterns.append("mutated")
        self.assertEqual(loaded.template_cow_patterns, ["*.sqlite", "cache/**"])

    def test_template_raw_patterns__defaults_to_empty_list_and_round_trips_toml(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        config = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        self.assertEqual(config.template_raw_patterns, [])

        (self.tmp_path / "config.toml").write_text(
            'type = "global"\n\n[templates]\nraw = [".env", "secrets/*.key"]\n',
            encoding="utf-8",
        )

        loaded = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))
        loaded.load()

        patterns = loaded.template_raw_patterns
        self.assertEqual(patterns, [".env", "secrets/*.key"])
        self.assertIs(type(patterns), list)
        self.assertEqual([type(pattern) for pattern in patterns], [str, str])
        self.assertTrue(Path(".env").match(patterns[0]))

        patterns.append("mutated")
        self.assertEqual(loaded.template_raw_patterns, [".env", "secrets/*.key"])

    def test_template_cow_patterns__raises_for_invalid_types(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        config = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))

        config.update(templates="bad")
        with self.assertRaisesRegex(ConfigTypeError, r"templates must be a table, got str"):
            _ = config.template_cow_patterns

        config.update(templates={"cow": "*.db"})
        with self.assertRaisesRegex(
            ConfigTypeError, r"templates\.cow must be a list of strings, got str"
        ):
            _ = config.template_cow_patterns

        config.update(templates={"cow": ["*.db", 2]})
        with self.assertRaisesRegex(
            ConfigTypeError, r"templates\.cow entries must be strings, got int"
        ):
            _ = config.template_cow_patterns

    def test_template_raw_patterns__raises_for_invalid_types(self):
        global_io = TomlConfigIO(self.tmp_path / "config.toml")
        allowed_hooks_io = JsonConfigIO(self.tmp_path / "allowed_hooks.json")
        config = GlobalConfig(global_io, AllowedHooksConfig(allowed_hooks_io))

        config.update(templates=[1])
        with self.assertRaisesRegex(ConfigTypeError, r"templates must be a table, got list"):
            _ = config.template_raw_patterns

        config.update(templates={"raw": None})
        with self.assertRaisesRegex(
            ConfigTypeError, r"templates\.raw must be a list of strings, got NoneType"
        ):
            _ = config.template_raw_patterns

        config.update(templates={"raw": [1]})
        with self.assertRaisesRegex(
            ConfigTypeError, r"templates\.raw entries must be strings, got int"
        ):
            _ = config.template_raw_patterns


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
