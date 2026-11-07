# AGENTS instructions

## Introduction

This document governs the project `treefort`. The project is a CLI utility that assists software developers in their use of git worktrees. The project uses `python-fire` to provide the CLI and is structured into classes which represent the commands that can be invoked.

## Structure
- The source code is located in `src/treefort/`
- The CLI entry point function is `main` in `src/treefort/cli.py`
- The primary CLI command class is located in `src/treefort/main.py`
- The sub-command classes are located in `src/treefort/commands/`
- The tests are located in `tests/`

## Development environment

### Tooling
* Package management: uv - installs and manages python dependencies, and builds the project.
* Linter & formatter: ruff - enforces style rules and code formatting (`uv run ruff check .`, `uv run ruff format --check .`).
* Pre-commit: prek - executes linters, formatters, and other checks prior to commit (`uv run prek run --all-files`).

### Details
- Use `uv venv` to create a virtual environment into `.venv/` which can otherwise be ignored.
- Use `uv sync --dev` to install python packages.
- Use `uv run pytest` (or `uv run pytest -q`) to run the test suite.
- Use `make build` to build the project, generating wheel, `.pex`, and standalone binary files in `dist/`.
- The `treefort` command can be tested within the `playground/` directory.
- Find CI plans in the `.github/workflows` folder.

### Verification Checklist
Before submitting changes, ensure the following checks pass:
- `uv run pytest -q` - all unit tests must pass.
- `uv run prek run --all-files` (or `uv run ruff check .` and `uv run ruff format --check .`) - all static analysis and formatting checks must pass.
- `git diff --check` - verify no whitespace issues or conflict markers remain.
- `bash -n scripts/e2e_test.sh` - verify bash script syntax when modifying shell scripts.

### Test Architecture & Conventions
- Organize unit tests by production class: one test case class per production class, named `<ProductionClass>TestCase` inheriting from `unittest.TestCase`.
- Method naming: `test_<method_name>__<behavior>` (use two underscores between method name and behavior) for class unit tests, and `test_<behavior>` for function tests.
- Isolate runtime side effects: when testing isolated helper methods or components, avoid constructing a full `Runtime` (which triggers logging and migration); construct via `Runtime.__new__(Runtime)` or mock boundaries instead.
- Organize tests into three tiers: `tests/` (mocked unit tests), `tests/integration/` (offline tests extending `LocalRepoIntegrationTestCase`, which provides an isolated `HOME` and a local bare `origin.git`), and `scripts/e2e_test.sh` (live GitHub MVP journeys, run with `./scripts/e2e_test.sh [test_name...]`).
- Never let tests touch the developer's `~/.treefort` or `~/.gitconfig`: isolate `HOME` and the Git identity/GPG environment when a test needs real `git`.

### Subprocess & CLI Safety
- Always pass the `--` argument separator before user-controlled or branch arguments in Git CLI invocations (e.g. `git branch -d -- <branch>`) to prevent option injection.
- Convert nonzero CLI subprocess results into `CommandError` with captured diagnostic output rather than swallowing errors or returning empty results.
- Worktree names should support nested relative paths (e.g. `subpath/feature`).

## Style Guide
- See `.gemini/styleguide.md` for overall style guidance
- See `.aiassistant/rules/tests.md` for unit test styles

## AI Assistance & Contributions
- Include clear pull request summaries with sections for `## Summary`, `## Testing` (or `## Validation`), and `## AI disclosure`.
- For commits generated with AI assistance, attribute contributors using the `Co-Authored-By:` Git trailer.
