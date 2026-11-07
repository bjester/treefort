"""Fish shell integration: the directory-changing wrapper and patched fire tab completion."""

import re

from fire import completion as fire_completion

from treefort.shells.fire_template import DEFAULT_OPTIONS

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


def wrapper(cmd: str) -> str:
    """The wrapper function that performs requested directory changes in the shell."""
    return FISH_WRAPPER.replace("__CMD__", cmd)


def completion_script(cmd: str, commands: list[tuple[str, ...]], hook_names: str) -> str:
    """Fire's fish completion, with namespaced helpers and dynamic completions appended."""
    # Fire's _GetMaps adds to the options set it is given, so hand it a mutable copy of
    # the shared frozenset.
    script = fire_completion._FishScript(cmd, commands, set(DEFAULT_OPTIONS))
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

    rules = FISH_DYNAMIC_RULES.replace("__CMD__", cmd).replace("__HOOK_NAMES__", hook_names)
    return f"{script}{FISH_HELPERS}\n{rules}"
