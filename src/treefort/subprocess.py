import logging
import queue
import random
import shlex
import subprocess
import threading
import time
from collections import deque
from collections.abc import Generator, Iterator, Sequence
from contextlib import ContextDecorator, contextmanager
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from treefort import ui
from treefort.operator import RuntimeOperator

# Command traces are DEBUG-only and carry no level glyph, so a color never competes with the
# semantic rendering of a warning (▲) or error (✗) on stderr. Any palette color is therefore
# usable to tell one command's output apart from another's in verbose mode.
COMMAND_COLORS = list(ui.COLORS)
MAX_OUTPUT_TAIL = 20


def _log_name(command: list[str]) -> str:
    """
    Returns a prefix string for visibility, via logging, into the command being executed
    :param command: The command list to be executed
    :return: A string for prefixing log messages
    """
    command_prefix = command[0]
    command_script_path = Path(command_prefix)
    if command_script_path.exists():
        command_prefix = (
            f"{command_script_path.name}.{command[1]}"
            if len(command) > 1
            else command_script_path.name
        )

    return command_prefix


def _output_thread(process: subprocess.Popen) -> tuple[queue.Queue[str], threading.Thread]:
    """
    Creates a thread that queues the process output to be read by the main thread
    :param process: The process to read output from
    :return: A tuple of the output queue and reader thread
    """
    output_queue: queue.Queue[str] = queue.Queue()

    def enqueue_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            output_queue.put(line)

    reader_thread = threading.Thread(target=enqueue_output, daemon=True)
    reader_thread.start()
    return output_queue, reader_thread


class RandomColorState(threading.local):
    depth: int = 0
    color: str | None = None


class RandomColorDecorator(ContextDecorator):
    """Maintains the same color across decorated calls within the same context"""

    def __init__(self):
        self._state = RandomColorState()

    @property
    def color(self) -> str | None:
        return self._state.color

    def __enter__(self):
        self._state.depth += 1
        if self._state.color is None:
            self._state.color = random.choice(COMMAND_COLORS)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._state.depth -= 1
        if self._state.depth == 0:
            self._state.color = None


random_color = RandomColorDecorator()


class SubprocessOperator(RuntimeOperator):
    wait_time: int = 60
    """The number of seconds to wait for a process to finish"""
    command_name: str | None = None
    """The name of the command being executed, prepended to the command list"""
    last_output: Sequence[str] = ()
    """Bounded tail buffer of recent non-empty output lines from the last command"""

    def _record_output_line(self, line: str) -> str:
        stripped = line.rstrip()
        if stripped:
            self.last_output.append(stripped)
        return stripped

    def _format_output_tail(self) -> str:
        return f"\n{chr(10).join(self.last_output)}" if self.last_output else ""

    def _get_command_logger(self, command: list[str]) -> logging.Logger:
        """Get a dedicated logger for logging command output"""
        if self.command_name:
            return logging.getLogger(self.command_name).getChild(_log_name(command))
        return logging.getLogger(_log_name(command))

    def stream_exec(
        self,
        command: list[str],
        cwd: str | Path | None = None,
        stream_info: bool = False,
    ) -> int:
        """
        Executes a command in a subprocess and streams its output to stdout.
        :param command: The command to execute as a list of strings
        :param cwd: The working directory to execute the command in
        :param stream_info: Whether to mirror output lines to the console at INFO level, indented
            in a gutter so they read as details beneath a step line
        :return: The exit code of the process
        """
        logger = self._get_command_logger(command)
        self.last_output = deque(maxlen=MAX_OUTPUT_TAIL)

        if self.command_name:
            command = [self.command_name, *command]

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd or self.context.cwd,
        )
        output_queue, reader_thread = _output_thread(process)
        deadline = time.monotonic() + self.wait_time

        try:
            color = COMMAND_COLORS[process.pid % len(COMMAND_COLORS)]
            self.logger.debug(f"Executing: {shlex.join(command)}", extra={"color": color})
            logger.debug(f"START: {shlex.join(command)}", extra={"color": color})

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and process.poll() is None:
                    raise subprocess.TimeoutExpired(command, self.wait_time)

                try:
                    line = output_queue.get(timeout=max(0.01, min(0.1, remaining)))
                except queue.Empty:
                    # Drain any buffered tail lines before exiting once process is done.
                    if (
                        process.poll() is not None
                        and not reader_thread.is_alive()
                        and output_queue.empty()
                    ):
                        break
                    continue

                stripped = self._record_output_line(line)
                # A line has exactly one destination: the console gutter when the caller wants
                # live output, otherwise the colorized debug trace.
                if not stream_info:
                    logger.debug(
                        stripped,
                        extra={"color": color},
                    )
                elif stripped:
                    self.logger.info(ui.gutter(stripped))

            process.wait()
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            if process.stdout and hasattr(process.stdout, "close"):
                process.stdout.close()
            reader_thread.join(timeout=0.5)
            logger.debug(
                f"END: {shlex.join(command)} ({process.returncode})", extra={"color": color}
            )

        return process.returncode

    @contextmanager
    def run(
        self, command: list[str], cwd: str | Path | None = None
    ) -> Generator[CompletedProcess, Any, None]:
        """
        Executes a command in a subprocess and returns the completed process
        :param command: The command to execute as a list of strings
        :param cwd: The working directory to execute the command in
        """
        logger = self._get_command_logger(command)
        # Only failure paths populate last_output for run() so successful queries do not
        # split stdout twice when consumed via iter_output().
        self.last_output = deque(maxlen=MAX_OUTPUT_TAIL)
        if self.command_name:
            command = [self.command_name, *command]

        with random_color:
            self.logger.debug(
                f"Executing: {shlex.join(command)}", extra={"color": random_color.color}
            )
            logger.debug(f"START: {shlex.join(command)}", extra={"color": random_color.color})
            process = None
            returncode = None

            try:
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=self.wait_time,
                    cwd=cwd or self.context.cwd,
                )
                returncode = process.returncode
                yield process
            except subprocess.CalledProcessError as e:
                returncode = e.returncode
                for stream_output in (e.stdout, e.stderr):
                    if not stream_output:
                        continue
                    for line in stream_output.splitlines():
                        if stripped := self._record_output_line(line):
                            logger.debug(
                                stripped,
                                extra={"color": random_color.color},
                            )
                raise
            finally:
                logger.debug(
                    f"END: {shlex.join(command)} ({returncode})",
                    extra={"color": random_color.color},
                )

    def iter_output(self, command: list[str], cwd: str | Path | None = None) -> Iterator[str]:
        """
        Executes a command in a subprocess and iterates its output after completion
        :param command: The command to execute as a list of strings
        :param cwd: The working directory to execute the command in
        """
        with random_color:
            logger = self._get_command_logger(command)

            with self.run(command, cwd=cwd) as process:
                for line in process.stdout.splitlines():
                    logger.debug(
                        line.rstrip(),
                        extra={"color": random_color.color},
                    )
                    yield line
