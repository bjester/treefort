"""Bash shell integration: the directory-changing wrapper and patched fire tab completion.

Zsh reuses this module's output through ``bashcompinit``; see ``treefort.shells.zsh``.
"""

from fire import completion as fire_completion

from treefort.shells.fire_template import DEFAULT_OPTIONS

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


def wrapper(cmd: str) -> str:
    """The wrapper function that performs requested directory changes in the shell."""
    return BASH_WRAPPER.replace("__CMD__", cmd)


def completion_script(cmd: str, commands: list[tuple[str, ...]], hook_names: str) -> str:
    """Fire's bash completion, with namespaced helpers and dynamic completions patched in."""
    # Fire's _GetMaps adds to the options set it is given, so hand it a mutable copy of
    # the shared frozenset.
    script = fire_completion._BashScript(cmd, commands, set(DEFAULT_OPTIONS))
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

    dynamic_helpers = BASH_DYNAMIC_HELPERS.replace("__HOOK_NAMES__", hook_names)
    script = script.replace(FIRE_BASH_HEADER, f"{FIRE_BASH_HEADER}\n{dynamic_helpers}", 1)

    dynamic_dispatch = "  if _treefort_dynamic_completion; then\n    return 0\n  fi\n"
    patched = f"{dynamic_dispatch}{FIRE_BASH_COMPLETION_LINE}"
    script = script.replace(FIRE_BASH_COMPLETION_LINE, patched, 1)
    return f"{script}\n{BASH_HELPER_OVERRIDES}"
