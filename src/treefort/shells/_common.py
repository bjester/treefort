"""Data shared by the shell completion synthesizers.

Kept out of the package ``__init__`` so the per-shell modules can import it without a cycle.
"""

from dataclasses import dataclass
from typing import Literal

from treefort.cli_manifest import CommandSpec, ParamSpec
from treefort.command import Completion
from treefort.hooks import Hook

CMD_PLACEHOLDER = "__CMD__"
GLOBAL_FLAGS = ("-h", "--help", "-v", "--verbose")

BRANCH_REF_COMMAND = (
    "git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/ 2>/dev/null"
)
REMOTE_COMMAND = "git remote 2>/dev/null"

# Worktree paths are reported relative to the project directory, which is the parent of the
# repository's common git directory. The bare repository itself is not a worktree name.
WORKTREE_AWK = (
    r"""/^worktree /{ p = $0; sub(/^worktree /, "", p); """
    r"""if (root == "") { print p; next } """
    r"""if (p == root || p == root "/.bare") next; """
    r"""if (index(p, root "/") == 1) { p = substr(p, length(root) + 2) } print p }"""
)
WORKTREE_COMMAND = (
    "git worktree list --porcelain 2>/dev/null"
    " | awk -v root="
    '"$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"'
    ' 2>/dev/null)"'
    f" '{WORKTREE_AWK}'"
)

# Helper bodies are valid in both bash and fish
HELPER_BODIES = {
    "branch_refs": BRANCH_REF_COMMAND,
    "remotes": REMOTE_COMMAND,
    "worktrees": WORKTREE_COMMAND,
}

# Extra words each provider offers in addition to its dynamic candidates
PROVIDER_PREFIXES = {
    Completion.BASE_REF.value: ("@", "^"),
    Completion.WORKTREE_SWITCH.value: ("-", "^", "@"),
    Completion.WORKTREE_CURRENT.value: ("@",),
}

# The helper function each provider's candidates come from, without the shell's prefix
PROVIDER_HELPERS = {
    Completion.BRANCH_REF.value: "branch_refs",
    Completion.BASE_REF.value: "branch_refs",
    Completion.REMOTE.value: "remotes",
    Completion.WORKTREE.value: "worktrees",
    Completion.WORKTREE_SWITCH.value: "worktrees",
    Completion.WORKTREE_CURRENT.value: "worktrees",
}


@dataclass(frozen=True)
class CandidateSource:
    """Where the completion candidates for one parameter come from."""

    kind: Literal["words", "provider"]
    words: tuple[str, ...] = ()
    provider: str = ""


def candidate_source(param: ParamSpec) -> CandidateSource | None:
    """Return the completion candidates for a parameter, if it has any.

    Static choices win over a dynamic provider, and the hook provider expands to its enum
    members here so the generated script carries no Python.
    """
    if param.choices:
        return CandidateSource(kind="words", words=param.choices)

    provider = param.completion
    if provider == Completion.NONE.value:
        return None
    if provider == Completion.HOOK.value:
        return CandidateSource(kind="words", words=("list",) + tuple(hook.name for hook in Hook))
    return CandidateSource(kind="provider", provider=provider)


def used_providers(commands: dict[str, CommandSpec]) -> set[str]:
    """Return the dynamic completion providers the given commands rely on."""
    providers = set()
    for spec in commands.values():
        for param in spec.params:
            source = candidate_source(param)
            if source is not None and source.kind == "provider":
                providers.add(source.provider)
    return providers


def helper_names(commands: dict[str, CommandSpec]) -> list[str]:
    """Return the sorted helper names the given commands need."""
    return sorted({PROVIDER_HELPERS[provider] for provider in used_providers(commands)})


def sorted_commands(commands: dict[str, CommandSpec]) -> list[CommandSpec]:
    """Return the commands ordered by canonical name, for deterministic generated output."""
    return [commands[name] for name in sorted(commands)]


def command_words(commands: dict[str, CommandSpec], alias_map: dict[str, str]) -> list[str]:
    """Return every word that selects a command, canonical names first, then aliases."""
    return sorted(commands) + sorted(alias_map)


def positional_params(spec: CommandSpec) -> list[ParamSpec]:
    """Return a command's positional parameters in declaration order."""
    return [param for param in spec.params if param.kind != "flag"]


def flag_params(spec: CommandSpec) -> list[ParamSpec]:
    """Return a command's flag parameters in declaration order."""
    return [param for param in spec.params if param.kind == "flag"]


def value_flag_spellings(spec: CommandSpec) -> list[str]:
    """Return the spellings of every flag that consumes a separate value token."""
    spellings = []
    for param in flag_params(spec):
        if param.value_type == "bool":
            continue
        spellings.append(f"--{param.name.replace('_', '-')}")
        if param.short is not None:
            spellings.append(param.short)
    return spellings


def flag_spellings(param: ParamSpec) -> list[str]:
    """Return every spelling of a flag, including the negation of a default-true boolean."""
    kebab = param.name.replace("_", "-")
    spellings = [f"--{kebab}"]
    if param.short is not None:
        spellings.append(param.short)
    if param.value_type == "bool" and param.default is True:
        spellings.append(f"--no-{kebab}")
    return spellings


def all_flag_spellings(spec: CommandSpec) -> list[str]:
    """Return every flag spelling a command accepts, before the global flags."""
    spellings = []
    for param in flag_params(spec):
        spellings.extend(flag_spellings(param))
    return spellings
