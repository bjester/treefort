import re

from treefort.context import Context
from treefort.errors import RemoteUsageError
from treefort.gh import GithubCLI
from treefort.git import GitCLI, GitRemote
from treefort.hooks import Hooks
from treefort.logger import setup_logger
from treefort.templates import Templates


class Runtime:
    __slots__ = ("name", "verbose", "context", "logger", "hooks", "git", "gh", "templates")

    def __init__(self, name: str, verbose: bool = False):
        """
        :param verbose: Whether to enable logging verbosity
        """
        self.name = name
        self.verbose = verbose
        self.context = Context()

        # run migrations before resolving any configuration directories
        self.context.migrate()

        log_dir = self.context.global_config_dir / "logs"
        logger = setup_logger(
            name,
            log_dir=log_dir,
            verbose=verbose,
        )
        self.logger = logger

        # operators
        self.hooks = Hooks(self.context, self.logger)
        self.git = GitCLI(self.context, self.logger)
        self.gh = GithubCLI(self.context, self.logger)
        self.templates = Templates(self.context, self.logger)

    def get_default_remote(self) -> GitRemote | None:
        return self.get_remote(owner_name=self.context.get_config().owner)

    def get_remote(
        self, name: str | None = None, owner_name: str | None = None
    ) -> GitRemote | None:
        remote_pattern = None
        if owner_name:
            config = self.context.get_config()
            # forks could be renamed, but we're not gonna worry about that for now
            remote_ref = f"{owner_name}/{config.name}"
            remote_pattern = re.compile(rf"(?:^|[:/]){re.escape(remote_ref)}(?:\.git)?/?$")
        elif not name:
            raise RemoteUsageError("Must provide either owner_name or name")

        for remote in self.git.remote():
            if remote.type != "fetch":
                continue
            if (remote_pattern and remote_pattern.search(remote.uri)) or (
                name and name == remote.name
            ):
                return remote

        return None
