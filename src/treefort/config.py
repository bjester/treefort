"""Configuration system supporting both JSON and TOML formats.

This module provides an abstract interface for configuration management, with
concrete reader and writer implementations for both JSON and TOML formats.
"""

import json
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import tomlkit

from treefort.errors import ConfigTypeError


@runtime_checkable
class ConfigProtocol(Protocol):
    """Protocol defining the interface for all config classes.

    Consumers should depend on this protocol rather than concrete implementations,
    allowing them to work with any config format without knowing the details.
    """

    type: str

    def update(self, *args, **kwargs):
        """Update config data with keyword arguments."""
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the config."""
        ...

    def save(self):
        """Save the config to a file."""
        ...

    def load(self):
        """Load config from a file."""
        ...


@runtime_checkable
class ConfigIOProtocol(Protocol):
    """Protocol defining the interface for all config reader/writer classes."""

    ext: str

    def __init__(self, file: Path): ...

    def exists(self) -> bool:
        """Check if the file exists."""
        ...

    def read(self) -> dict[str, Any]:
        """Read raw config from a file."""
        ...

    def write(self, data: dict[str, Any]):
        """Write raw config data to a file."""
        ...

    @classmethod
    def from_path(cls, parent_path: Path, file_name: str) -> "ConfigIOProtocol":
        """Build from path and filename"""
        ...


class ConfigIO(ABC):
    """Abstract base class for all configuration reader and writer classes.

    Provides common functionality and enforces the ConfigIOProtocol interface.
    Subclasses must implement format-specific read() and write() methods.
    """

    ext: str

    def __init__(self, file: Path):
        self._file = file

        if file.suffix != f".{self.ext}":
            raise ConfigTypeError(f"Invalid file extension: {file.suffix}")

    def exists(self) -> bool:
        """Check if the file exists."""
        return self._file.exists()

    @contextmanager
    def _open(self, mode: str = "r"):
        with self._file.open(mode, encoding="utf-8") as f:
            yield f

    @abstractmethod
    def read(self) -> dict[str, Any]:
        """Read raw config from file.

        Must be implemented by subclasses for their specific format.
        """
        pass

    @abstractmethod
    def write(self, data: dict[str, Any]):
        """Save the raw config to file.

        Must be implemented by subclasses for their specific format.
        """
        pass

    @classmethod
    def from_path(cls, parent_path: Path, file_name: str):
        return cls(parent_path / f"{file_name}.{cls.ext}")


class Config(ABC):
    """Abstract base class for all configuration classes.

    Provides common functionality and enforces the ConfigProtocol interface.
    Subclasses must implement format-specific save() and load() methods.
    """

    type: str

    def __init__(self, config_io: ConfigIO):
        self._config_io = config_io
        self._data = {"type": getattr(self, "type", "unknown")}

    def update(self, *args, **kwargs):
        """Update config data with keyword arguments."""
        self._data.update(*args, **kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the config."""
        return self._data.get(key, default)

    def _dump(self) -> dict[str, Any]:
        return self._data

    def _load(self, data: dict[str, Any]):
        self._data.update(data)

    def save(self):
        """Save the config to a file."""
        self._config_io.write(self._dump())

    def load(self):
        """Load config from a file."""
        config_data = self._config_io.read()
        if not config_data:
            return
        if config_data.get("type") != self.type:
            raise ConfigTypeError(f"Invalid config type: {config_data.get('type')}")
        self._load(config_data)


class JsonConfigIO(ConfigIO):
    """Config reader and writer for JSON format"""

    ext: str = "json"

    def read(self) -> dict[str, Any]:
        if not self.exists():
            return {}

        with self._open("r") as fd:
            return json.load(fd)

    def write(self, data: dict[str, Any]):
        self._file.parent.mkdir(parents=True, exist_ok=True)

        with self._open("w") as fd:
            json.dump(data, fd, indent=4)


class TomlConfigIO(ConfigIO):
    """Config reader and writer for TOML format"""

    ext: str = "toml"

    def read(self) -> dict[str, Any]:
        if not self.exists():
            return {}

        with self._open("r") as fd:
            return tomlkit.load(fd)

    def write(self, data: dict[str, Any]):
        self._file.parent.mkdir(parents=True, exist_ok=True)

        with self._open("w") as fd:
            tomlkit.dump(data, fd)


class AllowedHooksConfig(Config):
    """Separate configuration for allowed hooks (stored as allowed_hooks.json).

    This is kept separate because it needs to be written to frequently by the app
    and end users are unlikely to edit it directly.
    """

    type: str = "allowed_hooks"

    def __init__(self, config_io: JsonConfigIO):
        """Only supports JSON"""
        super().__init__(config_io)

    def update(self, *args, **kwargs):
        """Update config data with keyword arguments."""
        hooks = self._data.get("hooks", {})
        hooks.update(*args, **kwargs)
        self._data.update(hooks=hooks)

    def get(self, key: str, default: Any = None):
        """Look up the checksum recorded for an allowed hook file.

        Reads the ``hooks`` mapping from the config data and returns the entry
        stored under ``key``. Returns ``default`` when the hook is not allowed,
        including when no ``hooks`` mapping has been saved yet.

        :param key: Path of the hook file whose checksum is requested.
        :param default: Value returned when ``key`` is absent. Defaults to
            ``None``.
        :returns: The checksum string for the hook, or ``default``.
        :rtype: Any
        """
        return super().get("hooks", default={}).get(key, default)


class SettingsConfig(Config):
    """Base configuration for user and repository settings."""

    @property
    def verbose(self) -> bool:
        """Whether verbose logging is enabled."""
        raw = self._data.get("verbose", False)
        if not isinstance(raw, bool):
            raise ConfigTypeError(f"verbose must be a boolean, got {type(raw).__name__}")
        return raw


class GlobalConfig(SettingsConfig):
    """Global configuration."""

    type: str = "global"

    def __init__(self, config_io: ConfigIO, allowed_hooks: AllowedHooksConfig):
        super().__init__(config_io)
        self.allowed_hooks = allowed_hooks

    @property
    def allowed_envvars(self) -> list[str]:
        """Get the list of allowed environment variables."""
        return self._data.get("allowed_envvars", [])

    def allow_hook(self, path: str, checksum: str):
        """Add a hook to the allowed hooks list."""
        self.allowed_hooks.update({path: checksum})

    def save(self):
        """Save the config to a file."""
        self.allowed_hooks.save()
        super().save()


class RepositoryConfig(SettingsConfig):
    """Respository/project configuration."""

    type: str = "repository"

    @property
    def default_branch(self) -> str:
        return self._data.get("default_branch", "main")

    @property
    def last_worktree(self) -> str | None:
        """The name of the worktree the user last switched away from."""
        return self._data.get("last_worktree")

    @property
    def owner(self) -> str:
        return self._data.get("owner")

    @property
    def name(self) -> str:
        return self._data.get("name")

    @property
    def url(self) -> str:
        return self._data.get("url")

    @property
    def is_private(self) -> bool:
        return self._data.get("is_private", False)


class ConfigManager:
    """Manages config"""

    def __init__(self):
        self._config = None
        self._global_config = None

    def _get_config_io(
        self,
        config_dir: Path,
        file_name: str,
        check_ios: list[type[ConfigIO]] | None = None,
        default_type: type[ConfigIO] = TomlConfigIO,
        migrate: bool = True,
    ) -> ConfigIO:
        """Get the configuration reader/writer object."""
        if check_ios is None:
            check_ios = [TomlConfigIO, JsonConfigIO]

        config_io = None

        for check_type in check_ios:
            _config_io = check_type.from_path(config_dir, file_name)
            if _config_io.exists():
                config_io = _config_io
                break

        if config_io is None:
            config_io = default_type.from_path(config_dir, file_name)

        if migrate and not isinstance(config_io, default_type):
            new_config_io = default_type.from_path(config_dir, file_name)
            new_config_io.write(config_io.read())
            try:
                config_io._file.unlink()
            except OSError:
                pass
            config_io = new_config_io

        return config_io

    def get_config(self, config_dir: Path) -> RepositoryConfig:
        """Get the repository configuration."""
        if self._config is None:
            config = RepositoryConfig(self._get_config_io(config_dir, "config"))
            config.load()
            self._config = config
        return self._config

    def get_global_config(self, config_dir: Path) -> GlobalConfig:
        """Get the global configuration."""
        if self._global_config is None:
            allowed_hooks_io = self._get_config_io(
                config_dir, "allowed_hooks", check_ios=[], default_type=JsonConfigIO, migrate=False
            )
            allowed_hooks = AllowedHooksConfig(allowed_hooks_io)

            global_config = GlobalConfig(self._get_config_io(config_dir, "config"), allowed_hooks)
            global_config.load()

            if not allowed_hooks_io.exists():
                allowed_hooks.update(global_config._data.pop("allowed_hooks", {}))
                allowed_hooks.save()
                global_config.save()
            else:
                allowed_hooks.load()

            self._global_config = global_config

        return self._global_config

    def save_config(self, config: ConfigProtocol):
        """Save a configuration to disk."""
        config.save()
        if isinstance(config, RepositoryConfig):
            self._config = config
        elif isinstance(config, GlobalConfig):
            self._global_config = config
        elif isinstance(config, AllowedHooksConfig):
            if self._global_config is not None:
                self._global_config.allowed_hooks = config
        else:
            raise ValueError(f"Unknown config type: {type(config)}")
