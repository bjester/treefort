"""Snapshot fire's completion templates so upstream drift shows up as a reviewable diff.

`treefort shell-init` patches fire's completion scripts by matching exact template text. A
fire release that rewords those templates would otherwise degrade tab completion silently,
because users install `fire>=0.7.1` themselves while CI only tests the locked version. These
snapshots pin fire's raw output for a fixed command set: a dependabot bump that changes the
templates fails here, and the diff says what the patches must adapt to.

Fire emits set-ordered words, case arms, and completion rules, so the comparison sorts
those and drops fire's incidental trailing spaces; everything else is compared verbatim.

Regenerate with `TREEFORT_UPDATE_COMPLETION_SNAPSHOTS=1 uv run pytest tests/shells`.
"""

import os
import re
from pathlib import Path
from unittest import TestCase

from fire.completion import _BashScript, _FishScript

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE_ENV_VAR = "TREEFORT_UPDATE_COMPLETION_SNAPSHOTS"

# A fixed command set and option set, deliberately independent of treefort's own CLI: the
# snapshots must change only when fire's template changes, not when treefort gains a
# subcommand or flag.
SNAPSHOT_OPTIONS = {"--verbose", "-v"}
SNAPSHOT_COMMANDS = (
    ("demo",),
    ("demo", "--verbose"),
    ("demo", "-v"),
    ("demo", "switch"),
    ("demo", "sw"),
    ("demo", "checkout"),
    ("demo", "checkout", "--remote"),
    ("demo", "checkout", "--yes"),
)

# Only the two options lines above the case block are set-ordered; fire sorts every other
# assignment itself, and the ones inside filter_options() must not be reordered.
BASH_OPTIONS_LINE = re.compile(r'^(  (?:opts|GLOBAL_OPTIONS)=")([^"]*)(" ?)$')
FISH_GLOBAL_OPTIONS_LINE = re.compile(r"^(\s*set global_options )(.*)$")
CASE_ARMS_SEPARATOR = "    ;;\n"


def strip_trailing_whitespace(script: str) -> str:
    """Drop fire's incidental trailing spaces, which none of the patches depend on."""
    return "\n".join(line.rstrip() for line in script.splitlines()) + "\n"


def sorted_words(words: str) -> str:
    """Sort completion words, keeping variable references such as ${GLOBAL_OPTIONS} last."""
    parts = words.split()
    literal = sorted(part for part in parts if not part.startswith("${"))
    variables = [part for part in parts if part.startswith("${")]
    return " ".join([*literal, *variables])


def sort_bash_case_arms(script: str) -> str:
    """Sort the `case "${lastcommand}"` arms, which fire emits in set order."""
    header = '  case "${lastcommand}" in\n'
    start = script.index(header)
    end = script.index("  esac\n", start)
    region = script[start + len(header) : end]
    # The blank line right after `in` belongs to the template, not to the first arm, and the
    # blank line before each later arm belongs to the separator.
    leading_match = re.match(r"(?:[ \t]*\n)+", region)
    if leading_match is None:
        raise ValueError("no blank line after the case header")
    leading = leading_match.group()
    arms = [arm.lstrip("\n") for arm in region[len(leading) :].split(CASE_ARMS_SEPARATOR)]
    arms = [arm for arm in arms if arm.strip()]
    arms.sort(key=lambda arm: arm.strip().splitlines()[0])
    rebuilt = f"{CASE_ARMS_SEPARATOR}\n".join(arms) + CASE_ARMS_SEPARATOR
    return f"{script[:start]}{header}{leading}{rebuilt}{script[end:]}"


def canonical_bash(script: str) -> str:
    """Normalize the set-ordered parts of fire's bash template."""
    case_header = '  case "${lastcommand}" in\n'
    head, rest = script.split(case_header, 1)
    lines = []
    for line in head.splitlines():
        match = BASH_OPTIONS_LINE.match(line)
        if match is not None:
            prefix, words, suffix = match.groups()
            line = f"{prefix}{sorted_words(words)}{suffix}"
        lines.append(line)
    return strip_trailing_whitespace(sort_bash_case_arms("\n".join([*lines, case_header + rest])))


def canonical_fish(script: str) -> str:
    """Normalize the set-ordered parts of fire's fish template."""
    lines: list[str] = []
    rules: list[str] = []
    for line in script.splitlines():
        match = FISH_GLOBAL_OPTIONS_LINE.match(line)
        if match is not None:
            prefix, words = match.groups()
            line = f"{prefix}{' '.join(sorted(words.split()))}"
        if line.startswith("complete -c "):
            rules.append(line)
        else:
            lines.append(line)
    return strip_trailing_whitespace("\n".join([*lines, *sorted(rules)]))


class CompletionSnapshotTestCase(TestCase):
    # The whole point is a readable diff when fire changes; unittest truncates it by default.
    maxDiff = None

    def test_bash_template__matches_snapshot(self):
        self.assert_snapshot("bash", self.canonicalize(canonical_bash, _BashScript, "bash"))

    def test_fish_template__matches_snapshot(self):
        self.assert_snapshot("fish", self.canonicalize(canonical_fish, _FishScript, "fish"))

    def canonicalize(self, normalizer, template, shell: str) -> str:
        """Fire's template for the snapshot command set, normalized for set ordering."""
        script = template("demo", SNAPSHOT_COMMANDS, set(SNAPSHOT_OPTIONS))
        try:
            return normalizer(script)
        except ValueError as e:
            # A structural change can defeat the normalizer itself; say so plainly instead of
            # failing inside the normalization helpers.
            self.fail(f"fire's {shell} completion template no longer has the expected shape: {e}")

    def test_canonical_bash__normalizes_set_order_but_not_template_text(self):
        # Synthetic rather than fire's own output: fire's set ordering varies per process, so a
        # literal assertion on its generated text would itself be flaky.
        script = (
            '  opts="--verbose -v"\n'
            '  GLOBAL_OPTIONS="--verbose -v"\n'
            "\n"
            '  case "${lastcommand}" in\n'
            "\n"
            "    demo)\n"
            '      opts="--verbose demo ${GLOBAL_OPTIONS}"\n'
            "      opts=$(filter_options $opts)\n"
            "    ;;\n"
            "  esac\n"
            "\n"
            "filter_options()\n"
            "{\n"
            '  opts="$opts $opt"\n'
            "}\n"
        )

        # The two options lines above the case block are set-ordered and must normalize alike.
        self.assertEqual(
            canonical_bash(script),
            canonical_bash(script.replace('opts="--verbose -v"', 'opts="-v --verbose"')),
        )
        # fire sorts this line itself; reordering it is a real template change and must show.
        self.assertNotEqual(
            canonical_bash(script),
            canonical_bash(script.replace('opts="$opts $opt"', 'opts="$opt $opts"')),
        )

    def test_sort_bash_case_arms__orders_arms_and_keeps_their_bodies(self):
        script = (
            '  case "${lastcommand}" in\n'
            "\n"
            "    zulu)\n"
            '      opts="z"\n'
            "    ;;\n"
            "\n"
            "    alpha)\n"
            '      opts="a"\n'
            "    ;;\n"
            "  esac\n"
        )

        self.assertEqual(
            sort_bash_case_arms(script),
            '  case "${lastcommand}" in\n'
            "\n"
            "    alpha)\n"
            '      opts="a"\n'
            "    ;;\n"
            "\n"
            "    zulu)\n"
            '      opts="z"\n'
            "    ;;\n"
            "  esac\n",
        )

    def test_canonical_fish__sorts_set_order_but_not_template_text(self):
        script = (
            "function __is_prev_global\n"
            '    set global_options "--verbose" "-v"\n'
            "end\n"
            "\n"
            "complete -c demo -n '__fish_using_command demo' -f -a switch\n"
            "complete -c demo -n '__fish_using_command demo' -f -a checkout\n"
        )
        shuffled = (
            "function __is_prev_global\n"
            '    set global_options "-v" "--verbose"\n'
            "end\n"
            "\n"
            "complete -c demo -n '__fish_using_command demo' -f -a checkout\n"
            "complete -c demo -n '__fish_using_command demo' -f -a switch\n"
        )

        self.assertEqual(canonical_fish(script), canonical_fish(shuffled))
        self.assertNotEqual(
            canonical_fish(script), canonical_fish(script.replace("-a switch", "-a swap"))
        )

    def assert_snapshot(self, shell: str, content: str):
        path = SNAPSHOT_DIR / f"fire-completion-{shell}.txt"
        if os.environ.get(UPDATE_ENV_VAR) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.skipTest(f"regenerated {path.name}")
        self.assertTrue(path.is_file(), f"missing snapshot {path}")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            content,
            msg=(
                f"fire's {shell} completion template changed. Check whether the patches in "
                "treefort/shells still apply to the diff above, then regenerate with "
                f"{UPDATE_ENV_VAR}=1 uv run pytest tests/shells"
            ),
        )
