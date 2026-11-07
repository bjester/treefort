#!/usr/bin/env bash
#
# End-to-end tests for the core treefort user journeys.
#
# This tier drives the real CLI against https://github.com/bjester/treefort-recipes.git, so it
# needs network access and an authenticated `gh` CLI. Offline coverage of multi-worktree Git state
# transitions belongs in the Python integration tier (`tests/integration/`), and isolated class
# behaviour belongs in the unit tier (`tests/`).
#
# Usage:
#   ./scripts/e2e_test.sh                 # run every test
#   ./scripts/e2e_test.sh create_mvp      # run only the named tests
#   ./scripts/e2e_test.sh create_mvp checkout_mvp
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAYGROUND_DIR="$REPO_DIR/playground"
RECIPES_REPO="https://github.com/bjester/treefort-recipes.git"
DEFAULT_BRANCH="main"

# worktrees created by the tests
DEFAULT_WORKTREE="integ-test-default"
BRANCH_WORKTREE="integ-test-branch"
REMOTE_BRANCH_WORKTREE="integ-test-remote-branch"
PR_WORKTREE="for-integration"
MERGED_PR_WORKTREE="project-rename"

PROJECT_DIR=""

# --- assertion helpers -------------------------------------------------------------------------

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_dir_exists() {
    [ -d "$1" ] || fail "Directory '$1' does not exist"
}

assert_dir_not_exists() {
    [ ! -d "$1" ] || fail "Directory '$1' still exists"
}

assert_file_exists() {
    [ -f "$1" ] || fail "File '$1' does not exist"
}

assert_output_contains() {
    local expected="$1"
    shift
    local output
    output="$("$@" 2>&1)" || fail "Command '$*' failed while expecting output to contain '$expected'"
    case "$output" in
        *"$expected"*) ;;
        *) fail "Output of '$*' does not contain '$expected' (got: $output)" ;;
    esac
}

assert_fails() {
    if "$@" >/dev/null 2>&1; then
        fail "Command '$*' should have failed"
    fi
}

# Verifies a worktree's branch and base. Runs in a subshell so that a failed check never leaves the
# caller in a different working directory.
assert_worktree() {
    local worktree_dir="$1"
    local expected_branch="$2"
    local expected_base="$3"

    (
        cd "$worktree_dir"
        assert_file_exists "AGENTS.md"

        local current_branch
        current_branch="$(git branch --show-current)"
        [ "$current_branch" = "$expected_branch" ] ||
            fail "Branch mismatch in '$worktree_dir' (expected '$expected_branch', got '$current_branch')"

        local merge_base current_head
        merge_base="$(git merge-base HEAD "$expected_base")"
        current_head="$(git rev-parse HEAD)"
        [ "$merge_base" = "$current_head" ] ||
            fail "'$worktree_dir' is not based off '$expected_base'"
    )
}

# --- cli wrapper -------------------------------------------------------------------------------

treefort() {
    uv run treefort "$@"
}

# --- tests -------------------------------------------------------------------------------------

setup_playground() {
    echo "[SETUP] Initializing a project from $RECIPES_REPO"

    rm -rf "$PLAYGROUND_DIR"
    mkdir -p "$PLAYGROUND_DIR"
    (cd "$PLAYGROUND_DIR" && treefort init "$RECIPES_REPO")

    PROJECT_DIR="$PLAYGROUND_DIR/treefort-recipes"
    assert_dir_exists "$PROJECT_DIR"
    assert_dir_exists "$PROJECT_DIR/.bare"
    assert_dir_exists "$PROJECT_DIR/.treefort"
    assert_file_exists "$PROJECT_DIR/.git"
    echo "PASS: project initialized"
}

test_create_mvp() {
    echo "[TEST] create worktrees from the default branch, a branch, and a remote branch"

    treefort create "$DEFAULT_WORKTREE"
    assert_worktree "$DEFAULT_WORKTREE" "$DEFAULT_WORKTREE" "$DEFAULT_BRANCH"

    treefort create "$BRANCH_WORKTREE" "$DEFAULT_BRANCH"
    assert_worktree "$BRANCH_WORKTREE" "$BRANCH_WORKTREE" "$DEFAULT_BRANCH"

    treefort create "$REMOTE_BRANCH_WORKTREE" "origin/$DEFAULT_BRANCH"
    assert_worktree "$REMOTE_BRANCH_WORKTREE" "$REMOTE_BRANCH_WORKTREE" "$DEFAULT_BRANCH"

    assert_fails treefort create integ-test-invalid nonexistent/branch
    echo "PASS: create"
}

test_checkout_mvp() {
    echo "[TEST] checkout pull request #2 as a worktree"

    treefort checkout 2

    assert_dir_exists "$PR_WORKTREE"
    assert_file_exists "$PR_WORKTREE/README.md"
    assert_file_exists "$PR_WORKTREE/AGENTS.md"

    local first_line
    first_line="$(head -n 1 "$PR_WORKTREE/README.md")"
    case "$first_line" in
        *"INTEGRATION TEST"*) ;;
        *) fail "README.md does not start with 'INTEGRATION TEST' (got: $first_line)" ;;
    esac
    echo "PASS: checkout"
}

test_remove_and_prune_mvp() {
    echo "[TEST] remove the worktrees created by this suite"

    local worktrees=("$DEFAULT_WORKTREE" "$BRANCH_WORKTREE" "$REMOTE_BRANCH_WORKTREE" "$PR_WORKTREE")
    local worktree any_existing=false
    for worktree in "${worktrees[@]}"; do
        if [ -d "$worktree" ]; then
            any_existing=true
            break
        fi
    done

    # ensure `treefort rm` is exercised even when remove_and_prune_mvp is run standalone
    if [ "$any_existing" = false ]; then
        treefort create "$DEFAULT_WORKTREE"
    fi

    local cleaned_first=false
    for worktree in "${worktrees[@]}"; do
        if [ -d "$worktree" ]; then
            if [ "$cleaned_first" = false ]; then
                # exercise the default non-force removal path after clearing untracked templates
                git -C "$worktree" clean -fdq
                treefort rm "$worktree"
                cleaned_first=true
            else
                treefort rm "$worktree" --force
            fi
        fi
        assert_dir_not_exists "$worktree"
    done
    assert_fails treefort rm integ-test-missing
    echo "PASS: remove"

    echo "[TEST] prune the worktree for merged pull request #3"

    treefort checkout 3
    assert_dir_exists "$MERGED_PR_WORKTREE"
    git show-ref --verify --quiet "refs/heads/$MERGED_PR_WORKTREE" ||
        fail "Local branch 'refs/heads/$MERGED_PR_WORKTREE' does not exist before prune"

    # drop the untracked template files that `treefort checkout` copies in, otherwise git refuses
    # to remove the worktree without --force
    git -C "$MERGED_PR_WORKTREE" clean -fdq

    assert_output_contains "Successfully pruned '$MERGED_PR_WORKTREE'" treefort prune --yes

    assert_dir_not_exists "$MERGED_PR_WORKTREE"
    if git show-ref --verify --quiet "refs/heads/$MERGED_PR_WORKTREE"; then
        fail "Local branch 'refs/heads/$MERGED_PR_WORKTREE' still exists after prune"
    fi
    echo "PASS: prune"
}

# --- runner ------------------------------------------------------------------------------------

TESTS=(
    create_mvp
    checkout_mvp
    remove_and_prune_mvp
)

cleanup() {
    cd "$REPO_DIR"
    rm -rf "$PLAYGROUND_DIR"
}

run_test() {
    local name="$1"
    local test_function="test_${name}"
    # each test runs in a subshell so the working directory always starts at the project
    (cd "$PROJECT_DIR" && "$test_function")
}

main() {
    local selected=()
    local name
    if [ "$#" -gt 0 ]; then
        for name in "$@"; do
            selected+=("${name#test_}")
        done
    else
        selected=("${TESTS[@]}")
    fi

    # validate the selection before the expensive project setup
    for name in "${selected[@]}"; do
        declare -F "test_${name}" >/dev/null || fail "Unknown test '$name'"
    done

    trap cleanup EXIT

    echo "=== treefort E2E test suite ==="
    setup_playground

    for name in "${selected[@]}"; do
        run_test "$name"
    done

    echo "=== All E2E tests passed ==="
}

main "$@"
