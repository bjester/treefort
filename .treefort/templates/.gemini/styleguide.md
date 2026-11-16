# treefort project style guide

# Introduction
This style guide outlines the coding conventions for the project `treefort`. The project is a CLI utility that assists software developers in their use of git worktrees. The project uses a purpose-built, zero-dependency CLI engine, and is structured into classes which represent the commands that can be invoked. Commands declare their CLI contract declaratively with `@command` and `typing.Annotated[T, Arg(...)]`, and a build-time generator renders the manifest, help text, and shell completions.

# Key Principles
* **Readability:** Code should be broken into understandable units and align with the Single Responsibility Principle (SRP).
* **Maintainability:** Code should uphold the "Don't Repeat Yourself" (DRY) principle.
* **Consistency:** Adhering to a consistent style across all projects improves collaboration and reduces errors.
* **Performance:** While readability is paramount, code should be efficient.

# Deviations from PEP 8

## Line Length
* **Maximum line length:** 100 characters.

## Indentation
* **Use 4 spaces per indentation level.** (PEP 8 recommendation)

## Imports
* **Multiple imports per line:** Do not import multiple modules on a single line (e.g., use separate import statements).
* **Absolute imports:** Always use absolute imports for clarity.
* **Import sorting:** Follow standard grouping (standard library, third-party, first-party) managed by Ruff.

## Naming Conventions
* **Variables:** Use lowercase with underscores (snake_case): `user_name`, `total_count`
* **Constants:**  Use uppercase with underscores: `MAX_VALUE`, `DATABASE_NAME`
* **Functions:** Use lowercase with underscores (snake_case): `calculate_total()`, `process_data()`
* **Classes:** Use CapWords (CamelCase): `UserManager`, `PaymentProcessor`
* **Modules:** Use lowercase with underscores (snake_case): `user_utils`, `payment_gateway`

## Path Handling
* **Use `pathlib.Path`:** Prefer `pathlib.Path` over `os.path` for all filesystem path operations (enforced by Ruff `PTH`).

## Subprocess & CLI Safety
* **Argument separator:** Always pass the `--` argument separator before user-controlled inputs or branch names in Git CLI commands (e.g. `git branch -d -- <branch>`) to prevent option injection.
* **Subprocess errors:** Convert nonzero CLI subprocess results into `CommandError` with captured diagnostic output rather than swallowing errors or returning empty results.

## Docstrings
* **Use triple double quotes (`"""Docstring goes here."""`) for all docstrings.**
* **For complex functions/classes:** Include detailed descriptions of parameters, return values,
  attributes, and exceptions.
* **Use Sphinx style docstrings:** This helps with automated documentation generation.
    ```python
    def my_function(param1, param2):
        """Single-line summary.

        More detailed description, if necessary.

        :param param1: The first parameter.
        :type param1: int
        :param param2: The second parameter.
        :type param2: str
        :return: The return value.  True for success, False otherwise.
        :rtype: bool
        :raises ValueError: If `param2` is invalid.
        """
        # function body here
        pass
    ```

## Type Hints
* **Use type hints:** Type hints improve code readability and help catch errors early.
* **Modern syntax (Python >=3.10):** Use modern union syntax (`type | None` instead of `Optional[type]`) and built-in generics (`list[str]`, `dict[str, Any]` instead of `typing.List`, `typing.Dict`).
* **Follow PEP 484:** Use standard type annotations across all function signatures.

## Comments
* **Write clear and concise comments:** Explain the "why" behind the code, not just the "what".
* **Comment sparingly:** Well-written code should be self-documenting where possible.
* **Use complete sentences:** Start comments with a capital letter and use proper punctuation.
