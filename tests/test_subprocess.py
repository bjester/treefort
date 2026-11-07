import io
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from unittest.mock import MagicMock, Mock

from treefort.logger import setup_logger
from treefort.subprocess import SubprocessOperator, _log_name


class _SubprocessOperator(SubprocessOperator):
    """Test operator for SubprocessOperator class"""

    command_name = "test_command"


class SubprocessOperatorTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.context = Mock()
        self.context.cwd = self.tmp_path
        logging_patch = mock.patch("treefort.subprocess.logging")
        self.addCleanup(logging_patch.stop)
        logging_mock = logging_patch.start()
        self.logger = Mock()
        self.child_logger = Mock()
        self.command_logger = logging_mock.getLogger.return_value
        self.child_command_logger = self.command_logger.getChild.return_value
        self.logger.getChild.return_value = self.child_logger
        self.operator = _SubprocessOperator(self.context, self.logger)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @mock.patch("treefort.subprocess.subprocess.Popen")
    def test_stream_exec(self, mock_popen):
        process_mock = MagicMock()
        process_mock.stdout = ["line1\n", "line2\n"]
        process_mock.returncode = 0
        process_mock.pid = 123
        process_mock.poll.side_effect = [None, None, 0]
        mock_popen.return_value = process_mock

        command = ["do-something"]
        exit_code = self.operator.stream_exec(command)

        expected_command = ["test_command", "do-something"]
        mock_popen.assert_called_once_with(
            expected_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=self.tmp_path,
        )

        self.assertEqual(exit_code, 0)
        self.logger.getChild.assert_called_once_with("test_subprocess")
        self.child_logger.debug.assert_any_call(
            f"Executing: {shlex.join(expected_command)}", extra=mock.ANY
        )
        self.assertEqual(list(self.operator.last_output), ["line1", "line2"])
        self.assertEqual(self.child_command_logger.debug.call_count, 4)
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[0][0][0],
            "START: test_command do-something",
        )
        self.assertEqual(self.child_command_logger.debug.call_args_list[1][0][0], "line1")
        self.assertEqual(self.child_command_logger.debug.call_args_list[2][0][0], "line2")
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[3][0][0],
            "END: test_command do-something (0)",
        )

    @mock.patch("treefort.subprocess.subprocess.Popen")
    def test_stream_exec__retains_bounded_last_output(self, mock_popen):
        process_mock = MagicMock()
        process_mock.stdout = [f"line-{i}\n" for i in range(25)] + ["\n"]
        process_mock.returncode = 1
        process_mock.pid = 123
        process_mock.poll.return_value = 1
        mock_popen.return_value = process_mock

        exit_code = self.operator.stream_exec(["do-something"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(self.operator.last_output), 20)
        self.assertEqual(self.operator.last_output[0], "line-5")
        self.assertEqual(self.operator.last_output[-1], "line-24")

    @mock.patch("treefort.subprocess.subprocess.Popen")
    def test_stream_exec__timeout_kills_process(self, mock_popen):
        process_mock = MagicMock()
        process_mock.stdout = []
        process_mock.pid = 123
        process_mock.poll.return_value = None
        mock_popen.return_value = process_mock
        self.operator.wait_time = 0

        with self.assertRaises(subprocess.TimeoutExpired):
            self.operator.stream_exec(["do-something"])

        process_mock.kill.assert_called_once_with()
        process_mock.wait.assert_called_once_with()

    @mock.patch("treefort.subprocess.subprocess.Popen")
    def test_stream_exec__stream_info_logs_gutter_lines(self, mock_popen):
        process_mock = MagicMock()
        process_mock.stdout = ["line1\n", "\n", "line2\n"]
        process_mock.returncode = 0
        process_mock.pid = 123
        process_mock.poll.return_value = 0
        mock_popen.return_value = process_mock

        with mock.patch("treefort.ui.is_color_enabled", return_value=False):
            exit_code = self.operator.stream_exec(["do-something"], stream_info=True)

        self.assertEqual(exit_code, 0)
        self.child_logger.info.assert_has_calls([mock.call("  │ line1"), mock.call("  │ line2")])
        self.assertEqual(self.child_logger.info.call_count, 2)
        # the gutter is the only destination: no duplicate colorized debug trace per line
        debug_messages = [call.args[0] for call in self.child_command_logger.debug.call_args_list]
        self.assertNotIn("line1", debug_messages)
        self.assertNotIn("line2", debug_messages)

    @mock.patch("treefort.subprocess.subprocess.Popen")
    def test_stream_exec__stream_info_disabled_logs_no_info(self, mock_popen):
        process_mock = MagicMock()
        process_mock.stdout = ["line1\n"]
        process_mock.returncode = 0
        process_mock.pid = 123
        process_mock.poll.return_value = 0
        mock_popen.return_value = process_mock

        self.operator.stream_exec(["do-something"])

        self.child_logger.info.assert_not_called()

    @mock.patch("treefort.subprocess.subprocess.run")
    def test_run(self, mock_run):
        result_mock = Mock(spec=subprocess.CompletedProcess, stdout="line1\nline2", returncode=0)
        mock_run.return_value = result_mock

        command = ["get-data"]
        with self.operator.run(command) as output:
            self.assertEqual(output, result_mock)

        expected_command = ["test_command", "get-data"]
        mock_run.assert_called_once_with(
            expected_command,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            cwd=self.tmp_path,
        )
        self.assertEqual(self.child_command_logger.debug.call_count, 2)
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[0][0][0],
            "START: test_command get-data",
        )
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[1][0][0],
            "END: test_command get-data (0)",
        )

    @mock.patch("treefort.subprocess.subprocess.run")
    def test_run__records_stdout_then_stderr_into_last_output_on_failure(self, mock_run):
        stdout_lines = "".join(f"stdout-{i}\n" for i in range(25))
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128,
            cmd=["test_command", "get-data"],
            output=stdout_lines,
            stderr="fatal: bad revision\n\n",
        )

        with self.assertRaises(subprocess.CalledProcessError):
            with self.operator.run(["get-data"]):
                pass

        self.assertEqual(len(self.operator.last_output), 20)
        self.assertEqual(self.operator.last_output[0], "stdout-6")
        self.assertEqual(self.operator.last_output[-2], "stdout-24")
        self.assertEqual(self.operator.last_output[-1], "fatal: bad revision")
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[0][0][0],
            "START: test_command get-data",
        )
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[-2][0][0],
            "fatal: bad revision",
        )
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[-1][0][0],
            "END: test_command get-data (128)",
        )

    @mock.patch("treefort.subprocess.subprocess.run")
    def test_iter_output(self, mock_run):
        result_mock = Mock(spec=subprocess.CompletedProcess, stdout="line1\nline2", returncode=0)
        mock_run.return_value = result_mock

        command = ["get-data"]
        output = list(self.operator.iter_output(command))

        expected_command = ["test_command", "get-data"]
        mock_run.assert_called_once_with(
            expected_command,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            cwd=self.tmp_path,
        )

        self.assertEqual(output, ["line1", "line2"])
        self.child_logger.debug.assert_called_once()
        self.child_logger.info.assert_not_called()
        self.assertEqual(self.child_command_logger.debug.call_count, 4)
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[0][0][0], "START: test_command get-data"
        )
        self.assertEqual(self.child_command_logger.debug.call_args_list[1][0][0], "line1")
        self.assertEqual(self.child_command_logger.debug.call_args_list[2][0][0], "line2")
        self.assertEqual(
            self.child_command_logger.debug.call_args_list[3][0][0],
            "END: test_command get-data (0)",
        )


class LogNameTestCase(TestCase):
    def test_basic(self):
        self.assertEqual(_log_name(["git", "status"]), "git")

    def test_script_path(self):
        with tempfile.NamedTemporaryFile() as tmp:
            script_path = Path(tmp.name)
            self.assertEqual(_log_name([str(script_path), "run"]), f"{script_path.name}.run")

    def test_arg_limit(self):
        self.assertEqual(_log_name(["git", "diff", "--", "path/to/file"]), "git")


class StreamInfoConsoleTestCase(TestCase):
    """Exercise gutter streaming through the real logger wiring rather than a mocked logger.

    The command prints an upper-cased payload so the output can be told apart from the command
    line that the logger also records.
    """

    COMMAND = ["bash", "-c", "echo 'hook says hi' | tr a-z A-Z"]
    PAYLOAD = "HOOK SAYS HI"

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

        root = logging.getLogger()
        self._orig_handlers = list(root.handlers)
        self._orig_level = root.level

        context = Mock()
        context.cwd = self.tmp_path
        self.context = context

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in self._orig_handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in self._orig_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(self._orig_level)
        self.tmp_dir.cleanup()

    def build_operator(self, verbose: bool) -> SubprocessOperator:
        with (
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            mock.patch("treefort.logger.sys.stdout", self.stdout),
            mock.patch("treefort.logger.sys.stderr", self.stderr),
        ):
            logger = setup_logger(
                "treefort-wiring", log_dir=self.tmp_path / "logs", verbose=verbose
            )
        return SubprocessOperator(self.context, logger)

    def console_payload_lines(self) -> list[str]:
        return [line for line in self.stdout.getvalue().splitlines() if self.PAYLOAD in line]

    def test_stream_exec__stream_info_prints_gutter_once_without_verbose(self):
        operator = self.build_operator(verbose=False)

        operator.stream_exec(self.COMMAND, stream_info=True)

        self.assertEqual(self.console_payload_lines(), [f"  │ {self.PAYLOAD}"])

    def test_stream_exec__stream_info_prints_gutter_once_when_verbose(self):
        operator = self.build_operator(verbose=True)

        operator.stream_exec(self.COMMAND, stream_info=True)

        self.assertEqual(self.console_payload_lines(), [f"  │ {self.PAYLOAD}"])

    def test_stream_exec__stream_info_records_payload_once_in_log_file(self):
        operator = self.build_operator(verbose=False)

        operator.stream_exec(self.COMMAND, stream_info=True)

        log_text = (self.tmp_path / "logs" / "treefort.log").read_text()
        self.assertEqual(log_text.count(self.PAYLOAD), 1)
        self.assertIn(f"│ {self.PAYLOAD}", log_text)
