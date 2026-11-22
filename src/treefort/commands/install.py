import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Annotated

from treefort.command import Arg, Command, command
from treefort.errors import ValidationError
from treefort.shells import detect_shell, init_hook_line, is_valid_command_name, shell_rc_path

HOOK_MARKER = "# treefort shell integration"


def _user_search_path() -> str:
    """Return the ``PATH`` entries that can hold a persistent user installation.

    Relative entries and the active virtual environment's ``bin`` directory are dropped, so
    ``uv run treefort install --shell`` does not mistake the ephemeral ``.venv`` binary for an
    installed one.

    :return: The filtered search path
    """
    virtual_env = os.environ.get("VIRTUAL_ENV")
    venv_bin = (Path(virtual_env) / "bin").resolve() if virtual_env else None
    entries = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not Path(entry).is_absolute():
            continue
        if venv_bin is not None and Path(entry).resolve() == venv_bin:
            continue
        entries.append(entry)
    return os.pathsep.join(entries)


@command("install")
class InstallCommand(Command):
    def __call__(
        self,
        *,
        alias: Annotated[str | None, Arg(short="-a")] = None,
        gh_ext: Annotated[bool, Arg(short="-g")] = False,
        path_bin: Annotated[bool, Arg(short="-p")] = False,
        shell: Annotated[bool, Arg(short="-s")] = False,
        force: Annotated[bool, Arg(short="-f")] = False,
    ):
        """Installs treefort as a GitHub CLI extension or to the user's PATH.

        Without specifying options, the install command will ask you what to do.

        :param alias: An alternate name for the extension or path command.
        :param gh_ext: Install as a GitHub CLI extension.
        :param path_bin: Install to the user's PATH.
        :param shell: Configure shell integration and tab completion in the active shell
            startup file.
        :param force: Overwrite existing files.
        """
        target_name = alias or "treefort"
        gh_ext, path_bin, detected_shell = self._resolve_install_modes(
            target_name, gh_ext, path_bin, shell
        )

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
            target_paths.append(self._path_bin_target(target_name))

        # A shell integration install only needs the binary to be present, so an existing one
        # must not fail the command before the hook is written
        self._run(
            current_script_path,
            target_paths,
            should_link,
            force,
            keep_existing=detected_shell is not None,
        )

        if detected_shell is not None:
            self._install_shell_hook(detected_shell, target_name)

    def _resolve_install_modes(
        self, target_name: str, gh_ext: bool, path_bin: bool, shell: bool
    ) -> tuple[bool, bool, str | None]:
        """Validate the requested install modes and resolve the shell to configure.

        :param target_name: The installed command name
        :param gh_ext: Whether a GitHub CLI extension was requested
        :param path_bin: Whether a ``PATH`` installation was requested
        :param shell: Whether shell integration was requested
        :return: The resolved ``gh_ext``, ``path_bin``, and detected shell (or ``None``)
        :raises ValidationError: When the alias, mode combination, or shell is invalid
        """
        if not is_valid_command_name(target_name):
            raise ValidationError(f"Invalid alias name: {target_name!r}")

        if not shell:
            gh_ext, path_bin = self._ask(target_name, gh_ext, path_bin)
            return gh_ext, path_bin, None

        if gh_ext and not path_bin:
            raise ValidationError(
                "Shell integration (--shell) requires a PATH installation. "
                "Pass --path-bin as well or omit --gh-ext."
            )

        detected_shell = detect_shell()
        if detected_shell is None:
            raise ValidationError(
                "Could not detect a supported shell from $SHELL (supported: bash, zsh, fish)."
            )

        # Reuse a binary already on the user PATH, and install one when it is missing
        if not path_bin and not gh_ext:
            path_bin = shutil.which(target_name, path=_user_search_path()) is None
        return gh_ext, path_bin, detected_shell

    def _path_bin_target(self, target_name: str) -> Path:
        """Return the user binary directory path to install ``target_name`` into.

        :param target_name: The installed command name
        :return: The installation target path
        """
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

        return install_dir / target_name

    def _run(
        self,
        current_script_path: Path,
        target_paths: list[Path],
        should_link: bool,
        force: bool,
        keep_existing: bool = False,
    ):
        """Install the running script at every target path.

        :param current_script_path: The script to install
        :param target_paths: The destinations to install it at
        :param should_link: Whether to symlink instead of copy
        :param force: Whether to overwrite an existing target
        :param keep_existing: Whether an existing target is acceptable instead of an error,
            so a shell integration install still reaches the hook
        """
        for target_path in target_paths:
            if target_path.exists() or target_path.is_symlink():
                if not force:
                    if keep_existing:
                        self._logger.info(
                            f"Keeping existing {target_path}, use --force to overwrite"
                        )
                        continue
                    self._logger.warning(
                        f"Path already exists, use --force to overwrite: {target_path}"
                    )
                    sys.exit(1)
                if not target_path.is_symlink() and target_path.resolve() == current_script_path:
                    # Re-running from the installed binary must not unlink the running executable
                    self._logger.info(f"Installed to {target_path}")
                    continue
                target_path.unlink(True)

            target_path.parent.mkdir(parents=True, exist_ok=True)

            if should_link:
                target_path.symlink_to(current_script_path)
            else:
                shutil.copy(current_script_path, target_path)
                target_path.chmod(target_path.stat().st_mode | stat.S_IEXEC)
            self._logger.info(f"Installed to {target_path}")

    def _install_shell_hook(self, detected_shell: str, target_name: str) -> None:
        """Append the shell integration hook to the active shell's startup file, once.

        Appending in place rather than rewriting keeps startup files that dotfile managers
        symlink into a repository as symlinks.

        :param detected_shell: The shell to configure
        :param target_name: The installed command name the hook must invoke
        """
        rc_path = shell_rc_path(detected_shell)
        hook_line = init_hook_line(detected_shell, target_name)
        existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
        if hook_line in {line.strip() for line in existing.splitlines()}:
            self._logger.info(f"Shell integration already configured in {rc_path}")
            return

        rc_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        with rc_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{prefix}{HOOK_MARKER}\n{hook_line}\n")
        self._logger.info(f"Configured {detected_shell} shell integration in {rc_path}")

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
