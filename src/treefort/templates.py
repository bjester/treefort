import os
from contextlib import contextmanager
from logging import Logger
from pathlib import Path
from string import Template

from treefort import ui
from treefort.context import Context
from treefort.operator import ConfigOperator
from treefort.utils import normalize_worktree_name


class TemplateExists(Exception):
    pass


class Templates(ConfigOperator):
    """
    Copies files from /templates directory into a worktree, replacing environment variables in the
    process.
    """

    def __init__(self, context: Context, logger: Logger):
        super().__init__(context, logger)
        self.dir_name = "templates"
        self.replacement_map = {}

        global_config = context.get_global_config()
        for envvar_name in global_config.allowed_envvars:
            self.replacement_map[envvar_name] = os.environ.get(envvar_name, "")

    def copy(self, worktree_name: str):
        """
        Copies files from /templates configuration directory into a worktree, replacing environment
        variables in the process.
        :param worktree_name: The name of the worktree to copy the templates into
        """
        config = self.context.get_config()
        worktree_dir = self.context.project_dir / worktree_name

        self.replacement_map["REPO_NAME"] = config.name
        self.replacement_map["REPO_DIR"] = str(self.context.project_dir)
        self.replacement_map["WORKTREE_NAME"] = worktree_name
        self.replacement_map["WORKTREE_NAME_NORMALIZED"] = normalize_worktree_name(worktree_name)
        self.replacement_map["WORKTREE_DIR"] = str(worktree_dir)

        copied_count = 0
        for templates_dir in self.iter_config_dirs():
            for path in templates_dir.rglob("*"):
                relative_path = path.relative_to(templates_dir)
                if self._copy(worktree_dir, path, relative_path):
                    copied_count += 1

        if copied_count:
            self.logger.info(ui.success(f"Copied {copied_count} template(s)"))

    def _copy(self, worktree_dir: Path, absolute_path: Path, relative_path: Path) -> bool:
        """Copy a single template into the worktree, returning whether a file was written."""
        dest_path = worktree_dir / relative_path

        if absolute_path.is_dir():
            dest_path.mkdir(parents=True, exist_ok=True)
            return False

        self.logger.debug(f"Copying template {relative_path}")
        with absolute_path.open("r", encoding="utf-8") as src:
            with dest_path.open("w", encoding="utf-8") as dest:
                content = src.read()
                dest.write(Template(content).safe_substitute(self.replacement_map))

        dest_path.chmod(absolute_path.stat().st_mode)
        return True

    @contextmanager
    def add(self, relative_path: str):
        template_file = self.context.project_dir / relative_path
        template_file.parent.mkdir(parents=True, exist_ok=True)

        if template_file.exists():
            raise TemplateExists(f"Template {relative_path} already exists.")

        # copy it to config
        with template_file.open("w", encoding="utf-8") as f:
            yield f
