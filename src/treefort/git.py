import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib import parse

from treefort.errors import CommandError, WorktreeNameError
from treefort.subprocess import SubprocessOperator

TYPE_RE = re.compile(r"\((.*)\)")
GIT_EXT_RE = re.compile(r"\.git$", re.IGNORECASE)


class GitUri:
    """Value object for parsing and matching GitHub repository URIs."""

    __slots__ = ("uri",)

    def __init__(self, uri: str):
        self.uri = uri

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitUri):
            return NotImplemented
        return self.uri == other.uri

    def __hash__(self) -> int:
        return hash(self.uri)

    def __repr__(self) -> str:
        return f"GitUri(uri={self.uri!r})"

    @property
    def path(self) -> str:
        if "://" in self.uri:
            raw_path = parse.urlparse(self.uri).path.strip("/")
        elif ":" in self.uri:
            raw_path = self.uri.split(":", 1)[1].strip("/")
        else:
            raw_path = self.uri.strip("/")
        return re.sub(GIT_EXT_RE, "", raw_path)

    @property
    def owner(self) -> str:
        return self.path.split("/", 1)[0]

    @property
    def name(self) -> str:
        parts = self.path.split("/")
        return parts[1] if len(parts) > 1 else ""

    @property
    def is_valid(self) -> bool:
        parts = self.path.split("/")
        return len(parts) == 2 and bool(parts[0]) and bool(parts[1])

    def matches(self, owner: str, name: str) -> bool:
        return (
            self.is_valid
            and self.owner.lower() == owner.lower()
            and self.name.lower() == name.lower()
        )


class GitRemote:
    __slots__ = ("name", "uri", "type")

    def __init__(self, name: str, uri: str, remote_type: str):
        self.name = name
        self.uri = uri
        self.type = remote_type

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitRemote):
            return NotImplemented
        return (self.name, self.uri, self.type) == (other.name, other.uri, other.type)

    def __hash__(self) -> int:
        return hash((self.name, self.uri, self.type))

    def __repr__(self) -> str:
        return f"GitRemote(name={self.name!r}, uri={self.uri!r}, type={self.type!r})"

    @property
    def git_uri(self) -> GitUri:
        return GitUri(self.uri)

    @property
    def owner(self) -> str:
        return self.git_uri.owner

    @property
    def repo_name(self) -> str:
        return self.git_uri.name

    def matches(self, owner: str, name: str) -> bool:
        return self.git_uri.matches(owner, name)


class GitCLI(SubprocessOperator):
    command_name = "git"

    def _format_command_error(self, return_status: int, command: list[str]) -> str:
        detail = f"\n{chr(10).join(self.last_output)}" if self.last_output else ""
        return (
            f"Command failed, with exit status {return_status}: "
            f"{self.command_name} {' '.join(command)}{detail}"
        )

    def stream_exec(self, command: list[str], cwd: str | Path | None = None) -> int:
        return_status = super().stream_exec(command, cwd=cwd)
        if return_status != 0:
            raise CommandError(self._format_command_error(return_status, command))
        return return_status

    def clone(self, src: str, destination_dir: str):
        self.stream_exec(["clone", "--bare", src, destination_dir])

    def config(self, config_option: str, config_value: str):
        self.stream_exec(["config", config_option, config_value])

    def ls_tree(self, branch_name: str, file_path: str) -> Iterator[str]:
        yield from self.iter_output(["ls-tree", "-r", branch_name, "--", file_path])

    def cat_file(self, branch_name: str, file_path: str) -> Iterator[str]:
        yield from self.iter_output(["cat-file", "-p", f"{branch_name}:{file_path}"])

    def fetch(self, remote: str = "origin", refspec: str | None = None):
        args = ["fetch", remote]
        if refspec is not None:
            args.append(refspec)
        self.stream_exec(args)

    def remote(self) -> list[GitRemote]:
        remotes = []
        for line in self.iter_output(["remote", "-v"]):
            name, uri_ref = line.split("\t")
            uri, remote_type = uri_ref.split(" ", 1)
            remotes.append(GitRemote(name, uri, re.sub(TYPE_RE, r"\1", remote_type)))
        return remotes

    def add_worktree(self, name: str, base_ref: str):
        """Create a new worktree branch off base_ref"""
        # Use -- to separate flags from positional arguments to prevent argument injection
        self.stream_exec(["worktree", "add", "-b", name, "--", name, base_ref])

    def open_worktree(self, name: str):
        """Create a new worktree from an existing branch"""
        if ".." in name or name.startswith("/"):
            raise WorktreeNameError("Worktree name cannot contain '..' or start with '/'")
        # `git worktree add <path>` creates a new branch from HEAD. To use an
        # existing branch, the command is `git worktree add <path> <branch>`.
        # We pass `name` for both to check out the existing branch in a path with the same name.
        self.stream_exec(["worktree", "add", "--", name, name])

    def remove_worktree(self, name: str, force: bool = False):
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.extend(["--", name])
        self.stream_exec(args)

    def list_worktrees(self) -> list[dict]:
        """List all worktrees and their associated branches"""
        worktrees = []
        current_worktree = None
        for line in self.iter_output(["worktree", "list", "--porcelain"]):
            if line.startswith("worktree "):
                current_worktree = {"path": line[9:], "branch": None, "is_bare": False}
                worktrees.append(current_worktree)
            elif line.startswith("branch ") and current_worktree:
                current_worktree["branch"] = line[7:]
            elif line == "bare" and current_worktree:
                current_worktree["is_bare"] = True
        return worktrees

    def delete_branch(self, name: str, force: bool = False):
        """Delete a local branch"""
        args = ["branch", "-d" if not force else "-D", "--", name]
        self.stream_exec(args)

    def delete_branch_if_head(self, name: str, expected_head: str):
        """Delete a local branch only if it still points to the expected commit."""
        self.stream_exec(["update-ref", "-d", f"refs/heads/{name}", expected_head])
        self._remove_branch_config(name)

    def _remove_branch_config(self, name: str):
        config_pattern = rf"^branch\.{re.escape(name)}\."
        try:
            with self.run(["config", "--get-regexp", config_pattern]):
                pass
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                return
            raise CommandError(
                self._format_command_error(e.returncode, ["config", "--get-regexp", config_pattern])
            ) from e

        self.stream_exec(["config", "--remove-section", f"branch.{name}"])

    def get_branch_head(self, branch_name: str) -> str:
        """Get the HEAD commit SHA of a given branch"""
        try:
            with self.run(["rev-parse", branch_name]) as p:
                return p.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise CommandError(
                self._format_command_error(e.returncode, ["rev-parse", branch_name])
            ) from e
