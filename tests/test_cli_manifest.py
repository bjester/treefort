import builtins
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import treefort
from treefort.cli_codegen import build_manifest, write_generated_module
from treefort.cli_manifest import (
    PACKAGE_DIR,
    CLIManifest,
    CommandSpec,
    ManifestUnavailable,
    ParamSpec,
    _can_rebuild,
    is_packaged,
    load_manifest,
    max_source_mtime_ns,
)

SAMPLE_PARAM = ParamSpec(
    name="worktree_name",
    cli_name="WORKTREE_NAME",
    kind="positional",
    value_type="str",
    nullable=False,
    required=True,
    has_default=False,
    default=None,
    choices=(),
    short=None,
    completion="worktree",
    description="The name of the worktree",
)
SAMPLE_COMMAND = CommandSpec(
    name="remove",
    aliases=("rm",),
    summary="Remove a worktree",
    description="",
    params=(SAMPLE_PARAM,),
    help_text="NAME\n    treefort remove - Remove a worktree\n",
)
SAMPLE_MANIFEST = CLIManifest(
    prog_name="treefort",
    summary="CLI tool for managing Git worktrees",
    description="",
    commands={"remove": SAMPLE_COMMAND},
    alias_map={"rm": "remove"},
    root_help_text="NAME\n    treefort - CLI tool for managing Git worktrees\n",
    shell_completions={"bash": "complete -F _treefort_complete treefort\n"},
)


@contextmanager
def without_generated_attribute():
    """Hide any ``treefort._cli_generated`` attribute an earlier import left behind."""
    stale = treefort.__dict__.pop("_cli_generated", None)
    try:
        yield
    finally:
        if stale is not None:
            treefort._cli_generated = stale


@contextmanager
def missing_generated_module():
    """Make ``from treefort import _cli_generated`` raise ImportError."""
    with without_generated_attribute(), patch.dict(sys.modules, {"treefort._cli_generated": None}):
        yield


@contextmanager
def failing_generated_import(failure: ImportError):
    """Raise ``failure`` for the generated module import and delegate every other import."""
    real_import = builtins.__import__

    def import_(name, module_globals=None, module_locals=None, fromlist=(), level=0):
        if name == "treefort" and "_cli_generated" in (fromlist or ()):
            raise failure
        return real_import(name, module_globals, module_locals, fromlist, level)

    with patch("builtins.__import__", side_effect=import_):
        yield


class MaxSourceMtimeNsTestCase(TestCase):
    def _write(self, path: Path, mtime_ns: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        os.utime(path, ns=(mtime_ns, mtime_ns))

    def test_returns_the_newest_source_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._write(directory / "older.py", 1_000)
            self._write(directory / "nested" / "newer.py", 2_000)

            self.assertEqual(2_000, max_source_mtime_ns(directory))

    def test_ignores_the_generated_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._write(directory / "source.py", 1_000)
            self._write(directory / "_cli_generated.py", 9_000)

            self.assertEqual(1_000, max_source_mtime_ns(directory))

    def test_skips_sources_that_cannot_be_stated(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._write(directory / "source.py", 1_000)
            (directory / "dangling.py").symlink_to(directory / "missing.py")

            self.assertEqual(1_000, max_source_mtime_ns(directory))

    def test_returns_zero_when_there_are_no_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(0, max_source_mtime_ns(Path(tmp)))


class IsPackagedTestCase(TestCase):
    @patch.object(sys, "frozen", True, create=True)
    def test_returns_true_when_frozen(self):
        self.assertTrue(is_packaged())

    @patch("treefort.cli_manifest.__file__", "/opt/py/lib/site-packages/treefort/cli_manifest.py")
    def test_returns_true_inside_site_packages(self):
        self.assertTrue(is_packaged())

    @patch("treefort.cli_manifest.__file__", "/usr/lib/py/dist-packages/treefort/cli_manifest.py")
    def test_returns_true_inside_dist_packages(self):
        self.assertTrue(is_packaged())

    @patch("treefort.cli_manifest.__file__", "/opt/dist/treefort.pex/treefort/cli_manifest.py")
    def test_returns_true_inside_a_pex_archive(self):
        self.assertTrue(is_packaged())

    @patch("treefort.cli_manifest.__file__", "/checkout/src/treefort/cli_manifest.py")
    @patch.object(sys, "path", ["/checkout/src", "/opt/pex/treefort.pex"])
    def test_returns_true_when_a_pex_is_on_the_path(self):
        self.assertTrue(is_packaged())

    @patch("treefort.cli_manifest.__file__", "/checkout/src/treefort/cli_manifest.py")
    @patch.object(sys, "path", ["/checkout/src"])
    def test_returns_false_in_a_source_checkout(self):
        self.assertFalse(is_packaged())


class LoadManifestTestCase(TestCase):
    @staticmethod
    def generated(manifest=SAMPLE_MANIFEST, mtime_ns=0):
        return SimpleNamespace(MANIFEST=manifest, _SOURCE_MTIME_NS=mtime_ns)

    def test_returns_generated_manifest_without_scanning_when_packaged(self):
        with (
            patch.object(treefort, "_cli_generated", self.generated(), create=True),
            patch("treefort.cli_manifest.is_packaged", return_value=True),
            patch("treefort.cli_manifest.max_source_mtime_ns") as mtime_mock,
        ):
            manifest = load_manifest()

        self.assertIs(SAMPLE_MANIFEST, manifest)
        mtime_mock.assert_not_called()

    def test_returns_generated_manifest_when_sources_are_not_newer(self):
        with (
            patch.object(treefort, "_cli_generated", self.generated(mtime_ns=2_000), create=True),
            patch("treefort.cli_manifest.is_packaged", return_value=False),
            patch("treefort.cli_manifest.max_source_mtime_ns", return_value=2_000),
            patch("treefort.cli_manifest._build_manifest") as build_mock,
        ):
            manifest = load_manifest()

        self.assertIs(SAMPLE_MANIFEST, manifest)
        build_mock.assert_not_called()

    def test_rebuilds_when_sources_are_newer(self):
        rebuilt = SimpleNamespace(name="rebuilt")
        with (
            patch.object(treefort, "_cli_generated", self.generated(mtime_ns=1_000), create=True),
            patch("treefort.cli_manifest.is_packaged", return_value=False),
            patch("treefort.cli_manifest.max_source_mtime_ns", return_value=2_000),
            patch("treefort.cli_manifest._build_manifest", return_value=rebuilt) as build_mock,
        ):
            manifest = load_manifest()

        self.assertIs(rebuilt, manifest)
        build_mock.assert_called_once_with()

    def test_returns_generated_manifest_when_it_cannot_be_rebuilt(self):
        with (
            patch.object(treefort, "_cli_generated", self.generated(mtime_ns=1_000), create=True),
            patch("treefort.cli_manifest.is_packaged", return_value=False),
            patch("treefort.cli_manifest.max_source_mtime_ns", return_value=2_000),
            patch("treefort.cli_manifest._can_rebuild", return_value=False),
            patch("treefort.cli_manifest._build_manifest") as build_mock,
        ):
            manifest = load_manifest()

        self.assertIs(SAMPLE_MANIFEST, manifest)
        build_mock.assert_not_called()

    def test_can_rebuild__is_false_when_the_generator_is_absent(self):
        with patch("treefort.cli_manifest.importlib.util.find_spec", return_value=None):
            self.assertFalse(_can_rebuild())

    def test_can_rebuild__is_true_when_the_generator_is_available(self):
        with patch("treefort.cli_manifest.importlib.util.find_spec", return_value=object()):
            self.assertTrue(_can_rebuild())

    def test_raises_a_clear_error_when_the_manifest_is_missing_and_cannot_be_rebuilt(self):
        with (
            missing_generated_module(),
            patch("treefort.cli_manifest._can_rebuild", return_value=False),
        ):
            with self.assertRaisesRegex(ManifestUnavailable, "missing its generated CLI manifest"):
                load_manifest()

    def test_rebuilds_when_the_generated_module_is_missing(self):
        rebuilt = SimpleNamespace(name="rebuilt")
        with (
            missing_generated_module(),
            patch("treefort.cli_manifest._build_manifest", return_value=rebuilt) as build_mock,
        ):
            manifest = load_manifest()

        self.assertIs(rebuilt, manifest)
        build_mock.assert_called_once_with()

    def test_rebuilds_when_the_generated_module_was_never_written(self):
        # An absent file surfaces as ImportError(name="treefort"), not ModuleNotFoundError
        rebuilt = SimpleNamespace(name="rebuilt")
        absent = ImportError("cannot import name '_cli_generated' from 'treefort'", name="treefort")
        with (
            without_generated_attribute(),
            failing_generated_import(absent),
            patch("treefort.cli_manifest._build_manifest", return_value=rebuilt) as build_mock,
        ):
            manifest = load_manifest()

        self.assertIs(rebuilt, manifest)
        build_mock.assert_called_once_with()

    def test_propagates_a_broken_generated_module(self):
        failure = ModuleNotFoundError("No module named 'broken'", name="broken")
        with patch("treefort.cli_manifest._build_manifest") as build_mock:
            with failing_generated_import(failure):
                with self.assertRaises(ModuleNotFoundError):
                    load_manifest()

        build_mock.assert_not_called()

    def test_does_not_expose_the_codegen_module(self):
        # Importing or importing from the generator at module scope would ship it in artifacts
        self.assertFalse(hasattr(treefort.cli_manifest, "cli_codegen"))
        self.assertFalse(hasattr(treefort.cli_manifest, "build_manifest"))


class PackagedLayoutTestCase(TestCase):
    """Reproduces an installed artifact that excludes the generator, as wheels and PEX do."""

    def test_load_manifest__serves_the_generated_manifest_without_the_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lib"
            package = root / "treefort"
            shutil.copytree(
                PACKAGE_DIR,
                package,
                ignore=shutil.ignore_patterns("cli_codegen.py", "_cli_generated.py", "__pycache__"),
            )
            generated = package / "_cli_generated.py"
            write_generated_module(generated, build_manifest())
            # Mark the manifest stale so only the "cannot rebuild" rule can save it
            stale = re.sub(
                r"_SOURCE_MTIME_NS = \d+",
                "_SOURCE_MTIME_NS = 1",
                generated.read_text(encoding="utf-8"),
            )
            generated.write_text(stale, encoding="utf-8")

            script = (
                "import sys\n"
                f"sys.path.insert(0, {str(root)!r})\n"
                "import treefort\n"
                f"assert treefort.__file__.startswith({str(root)!r}), treefort.__file__\n"
                "from treefort.cli_manifest import load_manifest\n"
                "print(sorted(load_manifest().commands))\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            self.assertEqual("", result.stderr, result.stderr)
            self.assertEqual(
                "['aliases', 'checkout', 'create', 'init', 'install',"
                " 'prune', 'remove', 'version']",
                result.stdout.strip(),
            )
