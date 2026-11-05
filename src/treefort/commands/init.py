import re

from treefort.command import Command
from treefort.config import RepositoryConfig, TomlConfigIO
from treefort.errors import ProjectExistsError, RepositoryPathError
from treefort.git import GitUri
from treefort.hooks import Hook, HookExists
from treefort.templates import TemplateExists

JUST_PATH_RE = re.compile(r"^[\w\-_]+/[\w\-_]+$")


def normalize(repo: str):
    repo = repo.rstrip("/")
    if not repo.startswith("http") and not repo.startswith("git@"):
        if not JUST_PATH_RE.match(repo):
            raise RepositoryPathError("Invalid repository path format. Expected: {owner}/{repo}")
        repo = f"https://github.com/{repo}"

    if not repo.lower().endswith(".git"):
        repo += ".git"

    return repo


class RepositoryTarget:
    __slots__ = ("uri", "_git_uri", "_destination_dir")

    def __init__(self, repo: str, destination_dir: str | None = None):
        self.uri = normalize(repo)
        self._git_uri = GitUri(self.uri)
        self._destination_dir = destination_dir

    def validate(self):
        if not self._git_uri.is_valid:
            raise RepositoryPathError(f"Invalid repository path: {self.path}")

    @property
    def path(self):
        return self._git_uri.path

    @property
    def owner(self):
        return self._git_uri.owner

    @property
    def name(self):
        return self._git_uri.name

    @property
    def destination_dir(self):
        if self._destination_dir is None:
            return self.name
        return self._destination_dir


class InitCommand(Command):
    """Initialize a project for use with treefort"""

    _name = "init"

    def __call__(self, repo: str, *destination_dir: str | None, yes: bool = False):
        """
        Initialize a project for using treefort with it, by cloning the project and configuring
        it to be a bare git repo.

        This command creates a suitable structure for worktree directories within the project
        directory. It accepts various input formats, but it must be a GitHub repository, since this
        uses `gh` to gather additional information about the project.

        Examples:
            treefort init https://github.com/bjester/treefort.git
            treefort init ssh@github.com:bjester/treefort.git
            treefort init bjester/treefort
            treefort init bjester/treefort treefort-second

        :param repo: The URI, or Github 'owner/repo', to clone
        :type repo: str
        :param destination_dir: The destination directory to clone the project into.
        :type destination_dir: str
        :param yes: Perform any action automatically without asking (execute new or modified hooks)
        :type yes: bool
        """
        destination_dir = destination_dir[0] if destination_dir else None
        repo_target = RepositoryTarget(repo, destination_dir=destination_dir)
        repo_target.validate()

        project_dir = self._context.cwd / repo_target.destination_dir

        if project_dir.exists():
            # this would be problematic!
            raise ProjectExistsError(f"Project directory {project_dir} already exists")

        project_dir.mkdir(parents=True, exist_ok=True)

        with self._context.use(project_dir):
            self._runtime.hooks.fire(
                Hook.pre_init,
                repo_target.uri,
                project_dir,
                skip_project=True,
                bypass_allowlist=yes,
            )
            self._runtime.git.clone(repo_target.uri, ".bare")

            with (project_dir / ".git").open("w", encoding="utf-8") as f:
                f.write("gitdir: ./.bare")

            self._context.reset_properties()
            self._runtime.git.config("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
            self._runtime.git.fetch()
            repo_data = self._runtime.gh.repo_status()
            config = RepositoryConfig(TomlConfigIO.from_path(self._context.config_dir, "config"))
            config.update(
                default_branch=repo_data["defaultBranchRef"]["name"],
                owner=repo_data["owner"]["login"],
                name=repo_data["name"],
                url=repo_data["url"],
                is_private=repo_data["isPrivate"],
            )

            self._context.set_config(config)

            self._add_templates(config)
            self._add_hooks(config)

            self._runtime.hooks.fire(
                Hook.post_init,
                repo_target.uri,
                project_dir,
                bypass_allowlist=yes,
            )

    def _add_hooks(self, config):
        """Checks whether the repo has hooks in the default branch and copies them"""
        for hook in Hook:
            # check to see if repo has a hook
            if not any(self._runtime.git.ls_tree(config.default_branch, hook.git_path)):
                continue

            # copy it
            try:
                with self._runtime.hooks.add(hook) as f:
                    for line in self._runtime.git.cat_file(config.default_branch, hook.git_path):
                        f.write(f"{line}\n")
            except HookExists as e:
                self._logger.warning(f"Skipping due to error: {str(e)}")

    def _add_templates(self, config):
        """Checks whether the repo has templates in the default branch and copies them"""
        # check to see if repo has templates
        for template_ls in self._runtime.git.ls_tree(config.default_branch, ".treefort/templates"):
            template_file = template_ls.split("\t")[1].strip('"')
            # copy it
            try:
                with self._runtime.templates.add(template_file) as f:
                    for line in self._runtime.git.cat_file(config.default_branch, template_file):
                        f.write(f"{line}\n")
            except TemplateExists as e:
                self._logger.warning(f"Skipping due to error: {str(e)}")
