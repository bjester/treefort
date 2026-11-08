from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

from treefort.context import Context
from treefort.runtime import Runtime

_CommandType = TypeVar("_CommandType", bound=type)


class Completion(str, Enum):
    """Shell completion providers for positional and flag arguments."""

    NONE = "none"
    WORKTREE = "worktree"
    WORKTREE_SWITCH = "worktree_switch"  # "- ^ @" + active worktrees
    WORKTREE_CURRENT = "worktree_current"  # "@" + active worktrees
    BRANCH_REF = "branch_ref"  # local and remote git branch refs
    BASE_REF = "base_ref"  # "@ ^" + local and remote git branch refs
    REMOTE = "remote"  # git remote names
    HOOK = "hook"  # "list" + Hook enum member names

    def __str__(self) -> str:
        """Return the provider name, so formatting never depends on the Enum mixin."""
        return self.value


@dataclass(frozen=True)
class Arg:
    """Inline parameter metadata used inside typing.Annotated[T, Arg(...)]."""

    short: str | None = None
    complete: Completion = Completion.NONE


def command(
    name: str, *, aliases: tuple[str, ...] | list[str] = ()
) -> Callable[[_CommandType], _CommandType]:
    """Class decorator registering a Command's CLI name and aliases."""

    def decorator(cls: _CommandType) -> _CommandType:
        cls._name = name
        cls._aliases = list(aliases)
        return cls

    return decorator


class BaseCommand:
    def __init__(self):
        pass


class Command(BaseCommand):
    _name: str
    _aliases: list[str] = []

    def __init__(self, runtime: Runtime):
        super().__init__()
        self._runtime = runtime

    @property
    def _context(self) -> Context:
        """Convenience access to the context"""
        return self._runtime.context

    @property
    def _logger(self):
        """Convenience access to the logger"""
        return self._runtime.logger

    @property
    def _ui_enabled(self) -> bool:
        """Whether transient UI such as spinners may render.

        Verbose mode already streams every execution trace, so a spinner would only duplicate it.
        """
        return not getattr(self._runtime, "verbose", False)
