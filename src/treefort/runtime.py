import json
import os

import tomlkit.exceptions

from treefort.context import Context
from treefort.errors import (
    AncestorNotFoundError,
    ConfigTypeError,
    ProjectNotFoundError,
    RemoteUsageError,
)
from treefort.gh import GithubCLI
from treefort.git import GitCLI, GitRemote
from treefort.hooks import Hooks
from treefort.logger import setup_logger
from treefort.templates import Templates

CONFIG_LOAD_ERRORS = (
    ConfigTypeError,
    OSError,
    tomlkit.exceptions.TOMLKitError,
    json.JSONDecodeError,
)


class Runtime:
    __slots__ = ("name", "verbose", "context", "logger", "hooks", "git", "gh", "templates")

    def __init__(self, name: str, verbose: bool = False):
        """
        :param verbose: Whether to enable logging verbosity
        """
        self.name = name
        self.context = Context()

        # run migrations before resolving any configuration directories
        self.context.migrate()

        config_warnings: list[str] = []
        env_verbose = os.environ.get("TREEFORT_VERBOSE", "").lower() in ("1", "true", "yes")
        self.verbose = bool(verbose or env_verbose or self._resolve_config_verbose(config_warnings))

        log_dir = self.context.global_config_dir / "logs"
        logger = setup_logger(
            name,
            log_dir=log_dir,
            verbose=self.verbose,
        )
        self.logger = logger
        for warning in config_warnings:
            self.logger.warning(warning)

        # operators
        self.hooks = Hooks(self.context, self.logger)
        self.git = GitCLI(self.context, self.logger)
        self.gh = GithubCLI(self.context, self.logger)
        self.templates = Templates(self.context, self.logger)

    def _resolve_config_verbose(self, warnings: list[str] | None = None) -> bool:
        """Return True if either repository or global configuration enables verbose mode.

        Verbosity is additive across sources: any configuration level set to ``true``
        enables verbose logging, while missing or malformed configuration files fall
        back to ``False`` and record a warning to emit once the logger is initialized.
        """
        try:
            if self.context.get_config().verbose:
                return True
        except (ProjectNotFoundError, AncestorNotFoundError):
            pass
        except CONFIG_LOAD_ERRORS as e:
            if warnings is not None:
                warnings.append(f"Ignoring invalid repository configuration for verbose mode: {e}")

        try:
            return self.context.get_global_config().verbose
        except CONFIG_LOAD_ERRORS as e:
            if warnings is not None:
                warnings.append(f"Ignoring invalid global configuration for verbose mode: {e}")
            return False

    def get_default_remote(self) -> GitRemote | None:
        return self.get_remote(owner_name=self.context.get_config().owner)

    def get_remote(
        self, name: str | None = None, owner_name: str | None = None
    ) -> GitRemote | None:
        repo_name = None
        if owner_name:
            config = self.context.get_config()
            # forks could be renamed, but we're not gonna worry about that for now
            repo_name = config.name
        elif not name:
            raise RemoteUsageError("Must provide either owner_name or name")

        for remote in self.git.remote():
            if remote.type != "fetch":
                continue
            if (owner_name and repo_name and remote.matches(owner_name, repo_name)) or (
                name and name == remote.name
            ):
                return remote

        return None
