import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Annotated

from treefort.command import Arg, Command, command


@command("install")
class InstallCommand(Command):
    def __call__(
        self,
        *,
        alias: Annotated[str | None, Arg(short="-a")] = None,
        gh_ext: Annotated[bool, Arg(short="-g")] = False,
        path_bin: Annotated[bool, Arg(short="-p")] = False,
        force: Annotated[bool, Arg(short="-f")] = False,
    ):
        """Installs treefort as a GitHub CLI extension or to the user's PATH.

        Without specifying options, the install command will ask you what to do.

        :param alias: An alternate name for the extension or path command.
        :param gh_ext: Install as a GitHub CLI extension.
        :param path_bin: Install to the user's PATH.
        :param force: Overwrite existing files.
        """
        target_name = alias or "treefort"
        gh_ext, path_bin = self._ask(target_name, gh_ext, path_bin)

        current_script_path = Path(sys.argv[0]).resolve()
        should_link = False

        # unless the script is a self-contained executable, link it instead of copy
        if current_script_path.suffix == ".py":
            should_link = True
        elif not current_script_path.suffix and str(current_script_path).endswith(
            ".venv/bin/treefort"
        ):
            should_link = True

        target_paths = []

        if gh_ext:
            # gh cli expects that it has `gh-` prefix
            ext_name = target_name
            if not ext_name.startswith("gh-"):
                ext_name = f"gh-{target_name}"
            target_paths.append(
                Path("~/").expanduser()
                / ".local"
                / "share"
                / "gh"
                / "extensions"
                / ext_name
                / ext_name
            )

        if path_bin:
            path_dirs = os.getenv("PATH", "").split(os.pathsep)
            # Common user bin directories
            preferred_install_dirs = [
                Path.home() / ".local" / "bin",
                Path.home() / "bin",
            ]

            install_dir = next((d for d in preferred_install_dirs if str(d) in path_dirs), None)

            if not install_dir:
                self._logger.error("Could not find a suitable user binary directory in your PATH.")
                sys.exit(1)

            target_paths.append(install_dir / target_name)

        self._run(current_script_path, target_paths, should_link, force)

    def _run(
        self,
        current_script_path: Path,
        target_paths: list[Path],
        should_link: bool,
        force: bool,
    ):
        for target_path in target_paths:
            if target_path.exists() or target_path.is_symlink():
                if not force:
                    self._logger.warning(
                        f"Path already exists, use --force to overwrite: {target_path}"
                    )
                    sys.exit(1)
                target_path.unlink(True)

            target_path.parent.mkdir(parents=True, exist_ok=True)

            if should_link:
                target_path.symlink_to(current_script_path)
            else:
                shutil.copy(current_script_path, target_path)
                target_path.chmod(target_path.stat().st_mode | stat.S_IEXEC)
            self._logger.info(f"Installed to {target_path}")

    def _ask(self, target_name: str, gh_ext: bool, path_bin: bool) -> tuple[bool, bool]:
        # ask user what path they would like to use for installation, without these args
        if not gh_ext and not path_bin:
            self._logger.info("Tip: Re-run this with --alias to change the name\n")
            self._logger.info("Which method do you want to use?")
            self._logger.info(
                f"\t1. As a GitHub CLI (gh) extension: gh {target_name.replace('gh-', '')}"
            )
            self._logger.info(f"\t2. In your PATH: {target_name}\n")

            choice = input("Your choice (1, 2): ")
            if choice not in ("1", "2"):
                self._logger.error("Invalid choice")
                sys.exit(1)

            if choice == "1":
                gh_ext = True
            elif choice == "2":
                path_bin = True
        return gh_ext, path_bin
