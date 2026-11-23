# treefort

[![Python tests](https://github.com/bjester/treefort/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/bjester/treefort/actions/workflows/pytest.yml)
[![Build](https://github.com/bjester/treefort/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/bjester/treefort/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/treefort.svg?color=blue)](https://pypi.org/project/treefort/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org)

A CLI tool that helps you manage Git worktrees. Built as a GitHub CLI (`gh`) extension, but works standalone.

**Dependencies:** `git`, [`gh`](https://cli.github.com/)

---

## Table of Contents

- [Quick Start](#quick-start)
- [Capabilities](#capabilities)
- [How It Works](#how-it-works)
  - [Directory Structure](#directory-structure)
  - [Hooks](#hooks)
  - [Templates](#templates)
  - [Logging and Verbosity](#logging-and-verbosity)
- [Commands](#commands)
  - [init](#commands-init)
  - [create](#commands-create)
  - [checkout](#commands-checkout)
  - [switch](#commands-switch)
  - [remove](#commands-remove)
  - [prune](#commands-prune)
  - [sync](#commands-sync)
  - [hook](#commands-hook)
  - [install](#commands-install)
  - [shell-init](#commands-shell-init)
  - [version](#commands-version)
- [Installation](#installation)
- [AI Disclosure](#ai-disclosure)
- [License](#license)

---

## Quick Start

```bash
pip install treefort
```

Or with uv:

```bash
uvx treefort
```

Or download the PEX file from the [releases page](https://github.com/bjester/treefort/releases).

See [recipes](https://github.com/bjester/treefort-recipes) for hooks and templates.

---

## Capabilities

| Feature | Description |
|---------|-------------|
| **Bare repository initialization** | Clean separation of git metadata from worktrees |
| **Lifecycle hooks** | Custom scripts at key points with checksum validation |
| **Global + project config** | Hooks and templates at both levels |
| **PR worktrees** | Create worktrees directly from GitHub PRs |
| **Project bootstrapping** | Auto-copy hooks/templates from repo on init |
| **Worktree templates** | Pre-configured files copied to new worktrees |
| **Environment variables** | Template variables with allowlist support |

---

## How It Works

### Directory Structure

After initializing a repository (e.g., `treefort`):

```
treefort/
  .bare/                 # Bare git repository
  .git                   # Points to .bare/
  .treefort/
    hooks/             # Project-level hooks
    templates/         # Project-level templates
    config.toml        # Project configuration
  my-new-worktree/       # Created worktrees
    README.md
    ...
```

### Hooks

Hooks are executable scripts that run at specific lifecycle points. They can be configured at:
- **Global level**: `~/.treefort/hooks/` (or parent directories outside the project)
- **Project level**: `.treefort/hooks/` (copied from repo on init if present)

Execution order: global hooks first, then project hooks.

| Hook | Executes At | Description |
|------|-------------|-------------|
| `pre_init` | Global only | Before initializing a repository for worktrees |
| `post_init` | Global + Project | After initializing a repository for worktrees |
| `pre_checkout` | Global + Project | Before checking out a PR or existing branch as a worktree |
| `post_checkout` | Global + Project | After checking out a PR or existing branch as a worktree |
| `pre_create` | Global + Project | Before creating a new worktree (branch) |
| `post_create` | Global + Project | After creating a new worktree (branch) |
| `pre_remove` | Global + Project | Before removing a worktree |
| `post_remove` | Global + Project | After removing a worktree |

Hooks must be executable and allowed (via checksum validation) to run.
Treefort stores approved hook checksums in `~/.treefort/allowed_hooks.json`.

**Environment variables:** every hook also receives named `TREEFORT_*` variables, so a script
does not have to count positional arguments. A variable that does not apply to a hook is unset
rather than empty.

| Variable | Description | Hooks |
|----------|-------------|-------|
| `TREEFORT_HOOK` | Name of the running hook | All |
| `TREEFORT_REPO_NAME` | Name of the git repository | All |
| `TREEFORT_REPO_DIR` | Absolute path of the repo / project directory | All |
| `TREEFORT_DEFAULT_BRANCH` | Default branch from the repository configuration | `post_init` and worktree hooks |
| `TREEFORT_WORKTREE_NAME` | Name of the worktree | `create`, `checkout`, `remove` |
| `TREEFORT_WORKTREE_NAME_NORMALIZED` | Worktree name with non-alphanumeric chars replaced by dashes | `create`, `checkout`, `remove` |
| `TREEFORT_WORKTREE_DIR` | Absolute path of the worktree directory | `create`, `checkout`, `remove` |
| `TREEFORT_BASE_REF` | Ref the worktree came from | `create`, `checkout` |

`pre_init` runs before the clone exists, so it receives only the first three variables.
Positional arguments are unchanged: `$1`, `$2`, and `$3` still carry the same values.

Hooks do not inherit the full caller environment. Each hook receives the `TREEFORT_*` variables
above plus these standard variables:

`HOME`, `HTTP_PROXY`, `HTTPS_PROXY`, `LANG`, `LC_ALL`, `LC_CTYPE`, `LOGNAME`, `NO_PROXY`, `PATH`,
`SHELL`, `TERM`, `TMPDIR`, `USER`, `XDG_CONFIG_HOME`, `http_proxy`, `https_proxy`, `no_proxy`.

Proxy variables are passed in both cases because curl and git accept only the lowercase
`http_proxy`. A `TREEFORT_*` value left in the caller's environment never reaches a hook, so a
hook that runs `treefort` cannot pass its own context to the next one. Any other variable a hook
needs must be set by the hook itself.

<details>
<summary><strong>Example: post_create hook</strong></summary>

A hook for this project might look something like:

```bash
#!/usr/bin/env bash

WORKTREE_NAME="$TREEFORT_WORKTREE_NAME"                       # Same as $1
BASE_REF="$TREEFORT_BASE_REF"                                # Same as $2, format: remote/branch
WORKTREE_NAME_NORMALIZED="$TREEFORT_WORKTREE_NAME_NORMALIZED" # Same as $3

pushd "$WORKTREE_NAME"

uv venv
uv sync --group dev
prek install -f

popd
```

</details>

### Templates

Files in `.treefort/templates/` are copied to new worktrees before post-hooks execute.

**Tip:** Add template files to `.gitignore`.

Templates support environment variable substitution with `${ENVVAR_NAME}` syntax. Add allowed
variable names to `allowed_envvars` in `~/.treefort/config.toml`.

**Provided variables:**

| Variable | Description |
|----------|-------------|
| `REPO_NAME` | Name of the git repository |
| `REPO_DIR` | Absolute path of the repo / project directory |
| `WORKTREE_NAME` | Name of the new worktree |
| `WORKTREE_NAME_NORMALIZED` | Worktree name with non-alphanumeric chars replaced by dashes |
| `WORKTREE_DIR` | Absolute path of the worktree directory |
| `DEFAULT_BRANCH` | Default branch name from the repository configuration |

Every provided variable is also available with a `TREEFORT_` prefix, for example
`$TREEFORT_WORKTREE_DIR`, matching the names a hook sees in its environment. Names you add to
`allowed_envvars` are not mirrored.

<details>
<summary><strong>Example: JetBrains project name template</strong></summary>

Set a unique project name per worktree in JetBrains IDEs:

**File:** `.treefort/templates/.idea/.name`

```
${REPO_NAME}.${WORKTREE_NAME_NORMALIZED}
```

**Result in worktree:** `.idea/.name` contains `treefort.feat-my-branch`

</details>

### Logging and Verbosity

Treefort writes diagnostic logs to `~/.treefort/logs/treefort.log` on every run. Warnings and errors are written to `stderr` so `stdout` stays clean for scripts and pipes.

To stream debug logs and full error tracebacks to the terminal, enable verbose mode with any of these options:

- Pass `-v` or `--verbose` on the command line.
- Set the `TREEFORT_VERBOSE=1` environment variable (`1`, `true`, or `yes`).
- Set `verbose = true` in `.treefort/config.toml` for a single repository or in `~/.treefort/config.toml` for all repositories.

Verbosity is additive across sources. If any of these sources enables verbose mode, verbose output is active.

---

## Commands

Run `treefort` without any arguments for usage information.

<details>
<summary><strong><a id="commands-init">init</a></strong> — Initialize a repository for worktrees</summary>

**Spec:** `init <repository_uri> [optional_clone_dir] [--yes] [--no-hooks]`

Initializes a repository for use with worktrees. Similar to `git clone` — specify a directory name as the second argument, or it defaults to the repository name.

- `--yes`: Automatically execute new or modified hooks without prompting
- `--no-hooks`: Skip every hook without prompting or running anything

</details>

<details>
<summary><strong><a id="commands-create">create</a></strong> — Create a new worktree</summary>

**Spec:** `create <worktree_name> [base_ref] [--yes] [--no-hooks]`

Creates a new worktree. Defaults to the default branch of the GitHub repository. Optionally specify a base reference.

- `--yes`: Automatically execute new or modified hooks without prompting
- `--no-hooks`: Skip every hook without prompting or running anything

</details>

<details>
<summary><strong><a id="commands-checkout">checkout</a></strong> — Checkout a PR or branch as a worktree</summary>

**Spec:** `checkout [--remote=<name>] [--yes] [--no-hooks] <branch_name|pr_number|pr_url>`

Quickly create a worktree for a PR or existing branch. Works with fork PRs regardless of remote configuration.

- `--yes`: Automatically execute new or modified hooks without prompting
- `--no-hooks`: Skip every hook without prompting or running anything

</details>

<details>
<summary><strong><a id="commands-switch">switch</a></strong> — Switch to another worktree</summary>

**Spec:** `switch <worktree_name|branch_name|shortcut>`

**Aliases:** `sw`

Switches to another worktree in the current project. Accepts a project-relative worktree name
(including nested names such as `subpath/feature`), the name of a branch checked out in a
worktree, or one of these shortcuts:

- `-`: The previous worktree
- `^`: The worktree on the default branch, or the project root when there is none
- `@` or `.`: The current worktree root, or the project root when you are already in it

With shell integration installed, your shell changes directory. Without it, `switch` prints the
target path so you can use `cd "$(treefort switch <target>)"`.

</details>

<details>
<summary><strong><a id="commands-remove">remove</a></strong> — Remove a worktree</summary>

**Spec:** `remove [--force] [--yes] [--delete-branch] [--no-hooks] <worktree_name>`

**Aliases:** `rm`

Removes a worktree. Git refuses to delete worktrees with unmerged commits unless `--force` is used.

- `--delete-branch`: Delete the local Git branch associated with the worktree
- `--yes`: Automatically execute new or modified hooks without prompting
- `--no-hooks`: Skip every hook without prompting or running anything

</details>

<details>
<summary><strong><a id="commands-prune">prune</a></strong> — Remove worktrees for merged pull requests</summary>

**Spec:** `prune [--force] [--yes] [--no-hooks]`

Finds worktrees whose local branch head matches a merged GitHub pull request. Unless you use
`--yes`, treefort prompts before it removes each worktree and local branch.

- `--force`: Bypass Git safety checks when treefort removes the worktree
- `--yes`: Skip removal prompts and execute new or modified hooks without prompting
- `--no-hooks`: Skip every hook without prompting or running anything

</details>

<details>
<summary><strong><a id="commands-sync">sync</a></strong> — Synchronize project hooks and templates</summary>

**Spec:** `sync [ref] [--no-fetch]`

Copies `.treefort/hooks/` and `.treefort/templates/` from the repository Git tree into the
project's `.treefort/` directory. Use it to collect hooks and templates that your team added or
changed after you ran `init`.

Files are copied byte for byte, so binary templates stay intact. Executable bits recorded in Git
are preserved on both hooks and templates. Existing files are replaced. Files that exist only on
your machine are kept.

The command reads `<default_remote>/<default_branch>` unless you pass a `ref`. It fetches first,
from the remote the `ref` names when it names one, and from the default remote otherwise.

- `--no-fetch`: Skip the fetch and read the ref that is already in your local repository

Synced hooks are not approved automatically. The next time a synced hook runs, treefort prompts
you to approve its new checksum.

</details>

<details>
<summary><strong><a id="commands-hook">hook</a></strong> — List or run a lifecycle hook</summary>

**Spec:** `hook [hook_name] [worktree_name] [--base-ref=<ref>] [--yes]`

Without a hook name, lists every global and project hook with its scope, whether it is
executable, and whether its checksum is approved. Works outside a project, where only global
hooks exist. Prints `No hooks configured.` when no hook is installed. A project-level `pre_init`
is reported as `global-only`, because it is listed but can never run.

Naming a hook runs it with the same arguments and `TREEFORT_*` environment the lifecycle command
passes, so you can iterate on a hook without creating or removing a worktree. `pre_init` runs
global hooks only, matching `init`. The worktree name defaults to the worktree you are in.

- `--base-ref=<ref>`: Override the ref passed as the hook's second argument. Defaults to
  `<remote>/<branch>` for `checkout` hooks and `<remote>/<default_branch>` for `create` hooks.
  A worktree checked out from a fork PR needs `--base-ref <fork-remote>/<branch>` to match what
  `checkout` passed, because nothing records which remote the checkout used
- `--yes`: Run new or modified hooks without asking for confirmation

</details>

<details>
<summary><strong><a id="commands-install">install</a></strong> — Install treefort</summary>

**Spec:** `install [--alias=<name>] [--gh-ext] [--path-bin] [--shell] [--force]`

Installs treefort as a GitHub CLI extension or to your PATH. Without options, prompts for installation method.

- `--gh-ext`: Install as `gh` extension
- `--path-bin`: Install to `~/.local/bin` or `~/bin`
- `--shell`: Add the shell integration and tab completion hook to your shell startup file
- `--alias`: Custom name (e.g., `wktr`)
- `--force`: Overwrite existing installation

`--shell` detects `bash`, `zsh`, or `fish` from `$SHELL`, reuses a `treefort` binary already on
your `PATH`, and installs one when it is missing. It cannot be combined with `--gh-ext` alone.

</details>

<details>
<summary><strong><a id="commands-shell-init">shell-init</a></strong> — Emit the shell integration script</summary>

**Spec:** `shell-init [bash|zsh|fish]`

Prints the shell wrapper that applies directory changes, followed by the tab-completion script
for the given shell. The shell is detected from `$SHELL` when omitted, and the command name is
taken from the invoked executable, so an `--alias` install registers under its alias.

Prefer `treefort install --shell` to register the hook automatically.

</details>

<details>
<summary><strong><a id="commands-version">version</a></strong> — Show version</summary>

**Spec:** `version`

Outputs the installed version of treefort.

</details>

---

## Installation

Tested on Linux. Requires Python 3.10+ for PEX.

### As a GitHub CLI extension (`gh`)

1. Download the [latest release](https://github.com/bjester/treefort/releases) binary.
2. Make executable: `chmod +x treefort`
3. Install: `./treefort install --gh-ext`
4. Test: `gh treefort`

### Standalone

1. Download the [latest release](https://github.com/bjester/treefort/releases) binary or PEX.
2. Make executable: `chmod +x treefort*`
3. Install: `./treefort install --path-bin` or `./treefort.pex install --path-bin`
4. Test: `treefort version`

### Aliasing

```bash
./treefort install --alias=wktr --path-bin
wktr version
```

### Shell integration

`treefort switch` cannot change your shell's directory by itself, because a child process
cannot change its parent shell. Register the wrapper and tab completion in your startup file:

```bash
./treefort install --path-bin --shell
```

`--shell` detects your shell from `$SHELL` and appends one hook line to `~/.bashrc`,
`~/.bash_profile` (macOS, when that is the only bash startup file present),
`${ZDOTDIR:-~}/.zshrc`, or `${XDG_CONFIG_HOME:-~/.config}/fish/config.fish`. Running it again is
safe. To configure a shell by hand, add the hook line `eval "$(treefort shell-init <shell>)"` to
your startup file, or `treefort shell-init fish | source` for fish.

### From source

```bash
git clone https://github.com/bjester/treefort.git
cd treefort
uv sync --group dev
source .venv/bin/activate
make dist/treefort
./dist/treefort install --path-bin
```

---

## AI Disclosure

LLMs were used in the development of this project, mostly for brainstorming and bootstrapping code, particularly tests. The contribution proportion is roughly 80 / 20, human and AI code respectively. This may change over time as I try out agents!

---

## License

[MIT](LICENSE) :: Copyright 2026 Blaine Jester
