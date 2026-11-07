"""Emit shell integration: a directory-changing wrapper and patched fire tab completion."""

import inspect
import re
from typing import TYPE_CHECKING

from fire import completion as fire_completion

from treefort.command import Command
from treefort.errors import ValidationError
from treefort.hooks import Hook
from treefort.runtime import Runtime

if TYPE_CHECKING:
    from treefort.main import WorktreeCommands

SHELLS = ("bash", "zsh", "fish")
# The first character must be alphanumeric so the generated scripts can always invoke the
# command; re.fullmatch anchors strictly, unlike the "$" in re.match which a trailing
# newline slips past.
CMD_NAME_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9_-]*"
# Mirrors the `-v`/`--verbose` flags that treefort.cli strips before dispatch. New root-wide
# flags must be added here as well, or they disappear from tab completion.
#
# Completion invariant, encoded in both shell snippets: the subcommand is the FIRST non-flag
# token at index >= 1, and --remote is the only flag that consumes the following token. A
# new root-level flag that takes a value invalidates the subcommand detection everywhere.
DEFAULT_OPTIONS = {"--verbose", "-v"}

# The command name is templated into the shell snippets as __CMD__ so the snippets can
# stay literal shell text, without brace escaping.
BASH_WRAPPER = """\
__CMD__() {
    local cd_file exit_code=0
    cd_file="$(mktemp)"
    TREEFORT_CD_FILE="$cd_file" command __CMD__ "$@" || exit_code=$?
    if [[ -s "$cd_file" ]]; then
        cd -- "$(<"$cd_file")" || { rm -f "$cd_file"; return 1; }
    fi
    rm -f "$cd_file"
    return "$exit_code"
}
"""

FISH_WRAPPER = """\
function __CMD__
    set -l cd_file (mktemp)
    set -l exit_code 0
    TREEFORT_CD_FILE=$cd_file command __CMD__ $argv
    or set exit_code $status
    if test -s $cd_file
        cd -- (cat $cd_file)
        or begin
            rm -f $cd_file
            return 1
        end
    end
    rm -f $cd_file
    return $exit_code
end
"""

# Shell-native completion for positional arguments and option values. All git calls silence
# stderr so pressing <Tab> outside a repository stays quiet, and no Python runs on <Tab>.
BASH_DYNAMIC_HELPERS = """\
_treefort_list_branch_refs()
{
    git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/ 2>/dev/null
}

_treefort_list_worktrees()
{
    local dir project="" path="" name is_bare=""
    # `pwd -P` fails when the working directory has been deleted, such as after removing the
    # active worktree; bail out instead of walking an empty path forever.
    dir="$(pwd -P 2>/dev/null)" || return 0
    while [[ -n "$dir" && "$dir" != "/" ]]; do
        if [[ -e "$dir/.bare" ]]; then
            project="$dir"
            break
        fi
        dir="$(dirname -- "$dir")"
    done
    [[ -n "$project" ]] || return 0
    while IFS= read -r line; do
        case "$line" in
            "worktree "*)
                path="${line#worktree }"
                is_bare=""
                ;;
            bare)
                is_bare="1"
                ;;
            "")
                if [[ -z "$is_bare" && "$path" == "$project"/* ]]; then
                    name="${path#"$project"/}"
                    [[ -n "$name" && "$name" != .* ]] && printf '%s\\n' "$name"
                fi
                ;;
        esac
    done < <(git worktree list --porcelain 2>/dev/null)
}

# Worktree names may contain spaces, so they are completed one per line instead of through a
# compgen word list, which would split on whitespace. The first argument holds additional
# space-free completion words, such as the "- ^ @" shortcuts.
_treefort_complete_worktrees()
{
    local candidate
    COMPREPLY=( $(compgen -W "$1" -- "${cur}") )
    while IFS= read -r candidate; do
        if [[ -n "$candidate" && "$candidate" == "$cur"* ]]; then
            COMPREPLY+=("$candidate")
        fi
    done < <(_treefort_list_worktrees)
}

_treefort_dynamic_completion()
{
    if [[ "${cur}" == -* ]]; then
        return 1
    fi
    case "${prev}" in
        --remote)
            COMPREPLY=( $(compgen -W "$(git remote 2>/dev/null)" -- "${cur}") )
            return 0
            ;;
    esac
    local subcommand="" positional index
    positional=0
    for ((index=1; index < COMP_CWORD; ++index)); do
        if [[ "${COMP_WORDS[index]}" == -* ]]; then
            continue
        fi
        # --remote takes a value, so the word following it is not a positional argument
        if [[ "${COMP_WORDS[index-1]}" == "--remote" ]]; then
            continue
        fi
        if [[ -z "$subcommand" ]]; then
            subcommand="${COMP_WORDS[index]}"
        else
            positional=$((positional + 1))
        fi
    done
    case "${subcommand}" in
        checkout)
            if [[ ${positional} -eq 0 ]]; then
                COMPREPLY=( $(compgen -W "$(_treefort_list_branch_refs)" -- "${cur}") )
                return 0
            fi
            ;;
        create)
            if [[ ${positional} -eq 1 ]]; then
                COMPREPLY=( $(compgen -W "@ ^ $(_treefort_list_branch_refs)" -- "${cur}") )
                return 0
            fi
            ;;
        switch|sw)
            if [[ ${positional} -eq 0 ]]; then
                _treefort_complete_worktrees "- ^ @"
                return 0
            fi
            ;;
        remove|rm)
            if [[ ${positional} -eq 0 ]]; then
                _treefort_complete_worktrees "@"
                return 0
            fi
            ;;
        stack|restack)
            if [[ ${positional} -eq 0 ]]; then
                _treefort_complete_worktrees "status rebase sync"
                return 0
            fi
            ;;
        hook)
            if [[ ${positional} -eq 0 ]]; then
                COMPREPLY=( $(compgen -W "__HOOK_NAMES__" -- "${cur}") )
                return 0
            elif [[ ${positional} -eq 1 ]]; then
                _treefort_complete_worktrees ""
                return 0
            fi
            ;;
    esac
    return 1
}
"""

# Fire's completion helpers assume the last non-flag token is a (sub)command and that global
# flags may take a value. For this CLI the subcommand is the first non-flag token and
# -v/--verbose are boolean, so these overrides are appended after fire's definitions (bash
# keeps the last definition) to complete --flags after positional arguments and globals.
BASH_HELPER_OVERRIDES = """\
_treefort_get_lastcommand()
{
    local i
    for ((i=1; i < COMP_CWORD; ++i)); do
        if [[ "${COMP_WORDS[i]}" == -* ]]; then
            continue
        fi
        # --remote takes a value, which is not the subcommand
        if [[ "${COMP_WORDS[i-1]}" == "--remote" ]]; then
            continue
        fi
        echo "${COMP_WORDS[i]}"
        return 0
    done
    echo "${COMP_WORDS[0]}"
}

_treefort_is_prev_global()
{
    return 1
}
"""

FISH_HELPERS = """\
function __treefort_using_command
    set -l tokens (commandline -opc)
    set -l subcommand ""
    set -l previous ""
    for token in $tokens[2..-1]
        if not string match -q -- '-*' $token; and test "$previous" != "--remote"
            set subcommand $token
            break
        end
        set previous $token
    end
    if test "$argv[1]" = "$tokens[1]"
        test -z "$subcommand"
    else
        test "$subcommand" = "$argv[1]"
    end
end

function __treefort_positional_count
    set -l tokens (commandline -opc)
    set -l count 0
    set -l subcommand_seen 0
    set -l previous ""
    for token in $tokens[2..-1]
        if not string match -q -- '-*' $token; and test "$previous" != "--remote"
            if test $subcommand_seen -eq 0
                set subcommand_seen 1
            else
                set count (math "$count + 1")
            end
        end
        set previous $token
    end
    echo $count
end

function __treefort_branch_refs
    git for-each-ref --format='%(refname:short)' refs/heads/ refs/remotes/ 2>/dev/null
end

function __treefort_remotes
    git remote 2>/dev/null
end

function __treefort_worktree_names
    # `pwd -P` fails when the working directory has been deleted; bail out instead of
    # looping forever on an empty path.
    set -l dir (pwd -P 2>/dev/null)
    if test -z "$dir"
        return 0
    end
    set -l project ""
    while test "$dir" != "/"
        if test -e "$dir/.bare"
            set project "$dir"
            break
        end
        set dir (dirname -- "$dir")
    end
    if test -z "$project"
        return 0
    end
    set -l listing (git worktree list --porcelain 2>/dev/null)
    for path in (string replace --filter --regex '^worktree ' '' $listing)
        if not string match -q -- "$project/*" $path
            continue
        end
        # Dot-prefixed names are excluded along with the .bare repository, which the line
        # filter above cannot see because it drops the `bare` attribute lines.
        set -l name (string replace -- "$project/" "" $path)
        if test -n "$name"; and not string match -q -- '.*' $name
            echo $name
        end
    end
end
"""

# The rules key on the subcommand via `__treefort_using_command`, overridden below to mean
# the first non-flag token, so second positionals still complete; fire's own helper only
# matches the last non-flag token. Shortcut words stay off the first position of each -a
# value so fish's option parser can never mistake the value for another flag.
FISH_DYNAMIC_RULES = """\
complete -c __CMD__ -n '__treefort_using_command checkout' -f -a '(__treefort_branch_refs)'
complete -c __CMD__ -n '__treefort_using_command checkout' -l remote -f -a '(__treefort_remotes)'
complete -c __CMD__ \
    -n '__treefort_using_command create; and test (__treefort_positional_count) -ge 1' \
    -f -a '@ ^ (__treefort_branch_refs)'
complete -c __CMD__ \
    -n '__treefort_using_command switch; or __treefort_using_command sw' \
    -f -a '@ ^ - (__treefort_worktree_names)'
complete -c __CMD__ \
    -n '__treefort_using_command remove; or __treefort_using_command rm' \
    -f -a '@ (__treefort_worktree_names)'
complete -c __CMD__ \
    -n '__treefort_using_command stack; or __treefort_using_command restack' \
    -f -a 'status rebase sync (__treefort_worktree_names)'
complete -c __CMD__ \
    -n '__treefort_using_command hook; and test (__treefort_positional_count) -eq 0' \
    -f -a '__HOOK_NAMES__'
complete -c __CMD__ \
    -n '__treefort_using_command hook; and test (__treefort_positional_count) -eq 1' \
    -f -a '(__treefort_worktree_names)'
"""

# Fire's bash completion template defines these helpers in the global shell function
# namespace; prefixing them keeps the integration from colliding with other tools.
FIRE_BASH_HELPERS = (
    "get_lastcommand",
    "filter_options",
    "option_already_entered",
    "is_prev_global",
)

FIRE_BASH_HEADER = "# This script is autogenerated by fire/completion.py.\n"
FIRE_BASH_COMPLETION_LINE = '  COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )'


class ShellInitCommand(Command):
    """Emit shell integration for the treefort command"""

    _name = "shell-init"

    def __init__(self, runtime: Runtime, cli: "WorktreeCommands"):
        super().__init__(runtime)
        self._cli = cli

    def __call__(self, shell: str = "bash", cmd: str = "treefort"):
        """
        Emit shell integration for bash, zsh, or fish.

        The output contains a wrapper function that changes the shell's working directory
        after commands that request a directory change (such as `switch`), plus tab
        completion for subcommands, aliases, flags, git remotes, branches, worktrees, and
        hook names. Evaluate it once in your shell profile:

            eval "$(treefort shell-init bash)"

        The fish wrapper requires fish 3.1 or newer.

        :param shell: The shell to generate integration for: bash, zsh, or fish
        :param cmd: The command name the wrapper and completion register for
        :raises ValidationError: When `shell` or `cmd` is not supported
        """
        if shell not in SHELLS:
            raise ValidationError(f"Unsupported shell: {shell}. Choose one of: {', '.join(SHELLS)}")
        if not re.fullmatch(CMD_NAME_PATTERN, cmd):
            raise ValidationError(f"Invalid command name: {cmd}")

        print(self._wrapper(shell, cmd))
        print(self._completion_script(shell, cmd))

    def _wrapper(self, shell: str, cmd: str) -> str:
        """The wrapper function that performs requested directory changes in the shell."""
        template = FISH_WRAPPER if shell == "fish" else BASH_WRAPPER
        return template.replace("__CMD__", cmd)

    def _completion_script(self, shell: str, cmd: str) -> str:
        """The tab completion script for the given shell."""
        if shell == "fish":
            return self._fish_script(cmd)
        script = self._bash_script(cmd)
        if shell == "zsh":
            # zsh runs bash completion scripts through bashcompinit
            return f"autoload -Uz bashcompinit && bashcompinit\n\n{script}"
        return script

    def _bash_script(self, cmd: str) -> str:
        """Fire's bash completion, with namespaced helpers and dynamic completions patched in."""
        # Fire copies default_options into its own per-command maps; hand it a private set so
        # the module-level constant stays untouched across repeated invocations.
        script = fire_completion._BashScript(cmd, self._completion_commands(), set(DEFAULT_OPTIONS))
        for helper in FIRE_BASH_HELPERS:
            script = script.replace(helper, f"_treefort_{helper}")

        # The helper is called from inside the completion function, far above the override
        # appended at the end of the script; point readers at the redefinition.
        call_site = "  lastcommand=$(_treefort_get_lastcommand)"
        script = script.replace(
            call_site,
            "  # Redefined at the end of this script; see BASH_HELPER_OVERRIDES.\n" + call_site,
            1,
        )

        dynamic_helpers = BASH_DYNAMIC_HELPERS.replace("__HOOK_NAMES__", self._hook_names())
        script = script.replace(FIRE_BASH_HEADER, f"{FIRE_BASH_HEADER}\n{dynamic_helpers}", 1)

        dynamic_dispatch = "  if _treefort_dynamic_completion; then\n    return 0\n  fi\n"
        patched = f"{dynamic_dispatch}{FIRE_BASH_COMPLETION_LINE}"
        script = script.replace(FIRE_BASH_COMPLETION_LINE, patched, 1)
        return f"{script}\n{BASH_HELPER_OVERRIDES}"

    def _fish_script(self, cmd: str) -> str:
        """Fire's fish completion, with namespaced helpers and dynamic completions appended."""
        script = fire_completion._FishScript(cmd, self._completion_commands(), set(DEFAULT_OPTIONS))
        for old, new in (
            # Fire gates every subcommand flag rule on a helper that compares "--" + option
            # against the previous token, which never matches, so drop the guard entirely;
            # the CLI's global flags are boolean and need no value handling.
            ("; and __is_prev_global;", ";"),
            ("__fish_using_command", "__treefort_using_command"),
            ("__option_entered_check", "__treefort_option_entered_check"),
            ("__is_prev_global", "__treefort_is_prev_global"),
            # Fire renders the short -v option as the long --v flag; register it as short.
            (" --v' -l v\n", " -v' -s v\n"),
        ):
            script = script.replace(old, new)

        # Fire's global-flag helper is unreferenced once the guard is stripped above; drop
        # the dead definition instead of shipping its never-matching comparison.
        script = re.sub(
            r"function __treefort_is_prev_global\n.*?\nend\n\n",
            "",
            script,
            count=1,
            flags=re.DOTALL,
        )

        rules = FISH_DYNAMIC_RULES.replace("__CMD__", cmd).replace(
            "__HOOK_NAMES__", self._hook_names()
        )
        return f"{script}{FISH_HELPERS}\n{rules}"

    def _completion_commands(self) -> list[tuple[str, ...]]:
        """Command tuples for fire's completion maps, with aliases and true flags only.

        Parameters without a default must be passed positionally, so their `--flag` form is
        filtered out; keyword parameters stay as `--flag` suggestions. Every tuple is
        duplicated under the command's aliases so aliased invocations complete identically.
        """
        commands: list[tuple[str, ...]] = []
        for command in fire_completion._Commands(self._cli):
            if self._is_positional_flag(command):
                continue
            commands.append(command)
            commands.extend((alias, *command[1:]) for alias in self._aliases_of(command[0]))
        return commands

    def _is_positional_flag(self, command: tuple[str, ...]) -> bool:
        """Whether the tuple's flag names a parameter that has no default value."""
        if len(command) != 2 or not command[1].startswith("-"):
            return False
        member = getattr(self._cli, command[0], None)
        if member is None:
            return False
        try:
            parameters = inspect.signature(member).parameters
        except (TypeError, ValueError):
            return False
        parameter = parameters.get(command[1][2:].replace("-", "_"))
        return parameter is not None and parameter.default is inspect.Parameter.empty

    def _aliases_of(self, subcommand: str) -> list[str]:
        """The aliases that resolve to the given subcommand."""
        return [alias for alias, target in self._cli._alias_map.items() if target == subcommand]

    @staticmethod
    def _hook_names() -> str:
        """Completion words for the first positional argument of the `hook` subcommand."""
        return " ".join(["list", *[hook.name for hook in Hook]])
