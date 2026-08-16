"""
ShellCommand - TypeScript to Python conversion.
Wraps child processes for shell command execution with timeout, backgrounding, and cleanup.

Source: utils/ShellCommand.ts
"""

import asyncio
import os
import signal
import time
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field

# ============================================================
# IMPORTS - With defensive fallbacks
# ============================================================

try:
    from .task.TaskOutput import TaskOutput
except ImportError:
    class TaskOutput:
        """Stub - convert TaskOutput.ts first"""
        def __init__(self, task_id: str, onProgress=None, stdoutToFile=True):
            self.task_id = task_id
            self.path = f"/tmp/task_{task_id}.txt"
            self.stdoutToFile = stdoutToFile
            self.outputFileRedundant = False
            self.outputFileSize = 0

        async def getStdout(self) -> str:
            return ""

        def getStderr(self) -> str:
            return ""

        def deleteOutputFile(self):
            pass

        def spillToDisk(self):
            pass

        def clear(self):
            pass

try:
    from .task.diskOutput import MAX_TASK_OUTPUT_BYTES, MAX_TASK_OUTPUT_BYTES_DISPLAY
except ImportError:
    MAX_TASK_OUTPUT_BYTES = 800 * 1024 * 1024  # 800MB
    MAX_TASK_OUTPUT_BYTES_DISPLAY = "800MB"

try:
    from .Task import generateTaskId
except ImportError:
    def generateTaskId(prefix: str = "task") -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

try:
    from .format import formatDuration
except ImportError:
    def formatDuration(seconds: int) -> str:
        """Format duration in human-readable form"""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ============================================================
# CONSTANTS
# ============================================================

SIGKILL_CODE = 137  # Process killed by SIGKILL
SIGTERM_CODE = 143  # Process terminated by SIGTERM

# Background tasks write stdout/stderr directly to file,
# so a stuck append loop can fill the disk. Poll file size and kill when exceeded.
SIZE_WATCHDOG_INTERVAL_MS = 5_000  # 5 seconds


# ============================================================
# TYPES
# ============================================================

@dataclass
class ExecResult:
    """Result of shell command execution"""
    stdout: str
    stderr: str
    code: int
    interrupted: bool
    backgroundTaskId: Optional[str] = None
    backgroundedByUser: Optional[bool] = None
    assistantAutoBackgrounded: Optional[bool] = None
    outputFilePath: Optional[str] = None
    outputFileSize: Optional[int] = None
    outputTaskId: Optional[str] = None
    preSpawnError: Optional[str] = None


class ShellCommand:
    """Interface for shell command execution"""
    def background(self, backgroundTaskId: str) -> bool:
        raise NotImplementedError

    async def get_result(self) -> ExecResult:
        raise NotImplementedError

    def kill(self) -> None:
        raise NotImplementedError

    @property
    def status(self) -> str:
        """One of: 'running', 'backgrounded', 'completed', 'killed'"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """Cleans up stream resources to prevent memory leaks"""
        raise NotImplementedError

    @property
    def taskOutput(self) -> TaskOutput:
        raise NotImplementedError


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def prependStderr(prefix: str, stderr: str) -> str:
    """Prepend prefix to stderr if it exists"""
    return f"{prefix} {stderr}" if stderr else prefix


# ============================================================
# STREAM WRAPPER - Pipes child process output to TaskOutput
# ============================================================

class StreamWrapper:
    """
    Thin pipe from a child process stream into TaskOutput.
    Used in pipe mode (hooks) for stdout and stderr.
    """
    def __init__(self, stream: asyncio.StreamReader, taskOutput: TaskOutput, isStderr: bool):
        self.stream = stream
        self.taskOutput = taskOutput
        self.isStderr = isStderr
        self.isCleanedUp = False
        self._task = None

    async def dataHandler(self):
        """Read data from stream and write to TaskOutput"""
        try:
            while True:
                data = await self.stream.readline()
                if not data:
                    break
                text = data.decode('utf-8', errors='replace')
                if self.isStderr:
                    self.taskOutput.writeStderr(text)
                else:
                    self.taskOutput.writeStdout(text)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def start(self):
        """Start reading from stream"""
        if self.stream and not self.isCleanedUp:
            self._task = asyncio.create_task(self.dataHandler())

    def cleanup(self):
        """Clean up stream resources"""
        if self.isCleanedUp:
            return
        self.isCleanedUp = True
        if self._task:
            self._task.cancel()
            self._task = None
        # Release references for GC
        self.stream = None
        self.taskOutput = None


# ============================================================
# SHELL COMMAND IMPLEMENTATION
# ============================================================

class ShellCommandImpl(ShellCommand):
    """
    Implementation of ShellCommand that wraps a child process.

    For bash commands: both stdout and stderr go to a file fd via
    stdio[1] and stdio[2] — no JS involvement. Progress is extracted
    by polling the file tail.
    For hooks: pipe mode with StreamWrappers for real-time detection.
    """

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        abortSignal: asyncio.Event,
        timeout: int,
        taskOutput: TaskOutput,
        shouldAutoBackground: bool = False,
        maxOutputBytes: int = MAX_TASK_OUTPUT_BYTES,
    ):
        self._process = process
        self._abortSignal = abortSignal
        self._timeout = timeout
        self._shouldAutoBackground = shouldAutoBackground
        self._maxOutputBytes = maxOutputBytes
        self.taskOutput = taskOutput

        # State
        self._status = 'running'
        self._backgroundTaskId: Optional[str] = None
        self._killedForSize = False
        self._onTimeoutCallback: Optional[Callable] = None
        self._timeoutTask: Optional[asyncio.Task] = None
        self._sizeWatchdogTask: Optional[asyncio.Task] = None
        self._resultFuture: asyncio.Future = asyncio.get_event_loop().create_future()
        self._exitCodeFuture: asyncio.Future = asyncio.get_event_loop().create_future()

        # In file mode (bash commands), both stdout and stderr go to the
        # output file fd — process.stdout/.stderr are both None.
        # In pipe mode (hooks), wrap streams to funnel data into TaskOutput.
        self._stdoutWrapper: Optional[StreamWrapper] = None
        self._stderrWrapper: Optional[StreamWrapper] = None

        if process.stdout:
            self._stdoutWrapper = StreamWrapper(process.stdout, taskOutput, False)
            self._stdoutWrapper.start()

        if process.stderr:
            self._stderrWrapper = StreamWrapper(process.stderr, taskOutput, True)
            self._stderrWrapper.start()

        # Setup abort handler
        self._boundAbortHandler = self._abortHandler

        # Start timeout
        self._timeoutTask = asyncio.create_task(self._handleTimeout())

        # Start waiting for process to exit
        asyncio.create_task(self._waitForExit())

    @property
    def status(self) -> str:
        return self._status

    async def get_result(self) -> ExecResult:
        return await self._resultFuture

    def _abortHandler(self):
        """Handle abort signal"""
        # On 'interrupt' (user submitted a new message), don't kill — let the
        # caller background the process so the model can see partial output.
        if self._abortSignal.reason == 'interrupt':
            return
        self.kill()

    async def _handleTimeout(self):
        """Handle command timeout"""
        try:
            await asyncio.sleep(self._timeout)
            if self._status in ('running', 'backgrounded'):
                if self._shouldAutoBackground and self._onTimeoutCallback:
                    self._onTimeoutCallback(self.background)
                else:
                    self._doKill(SIGTERM_CODE)
        except asyncio.CancelledError:
            pass

    async def _waitForExit(self):
        """Wait for process to exit and handle result"""
        try:
            returncode = await self._process.wait()
            self._resolveExitCode(returncode if returncode is not None else 1)
        except Exception:
            self._resolveExitCode(1)

    def _resolveExitCode(self, code: int):
        """Resolve exit code and handle completion"""
        if not self._exitCodeFuture.done():
            self._exitCodeFuture.set_result(code)

    async def _handleExit(self, code: int):
        """Handle process exit"""
        # Cancel timeout
        if self._timeoutTask:
            self._timeoutTask.cancel()

        # Update status
        if self._status in ('running', 'backgrounded'):
            self._status = 'completed'

        # Build result
        stdout = await self.taskOutput.getStdout()
        result = ExecResult(
            code=code,
            stdout=stdout,
            stderr=self.taskOutput.getStderr(),
            interrupted=(code == SIGKILL_CODE),
            backgroundTaskId=self._backgroundTaskId,
        )

        # Handle file output
        if self.taskOutput.stdoutToFile and not self._backgroundTaskId:
            if self.taskOutput.outputFileRedundant:
                # Small file — full content is in result.stdout, delete the file
                self.taskOutput.deleteOutputFile()
            else:
                # Large file — tell the caller where the full output lives
                result.outputFilePath = self.taskOutput.path
                result.outputFileSize = self.taskOutput.outputFileSize
                result.outputTaskId = self.taskOutput.task_id

        # Add error messages
        if self._killedForSize:
            result.stderr = prependStderr(
                f'Background command killed: output file exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY}',
                result.stderr,
            )
        elif code == SIGTERM_CODE:
            result.stderr = prependStderr(
                f'Command timed out after {formatDuration(self._timeout)}',
                result.stderr,
            )

        # Resolve result
        if not self._resultFuture.done():
            self._resultFuture.set_result(result)

    def _doKill(self, code: Optional[int] = None):
        """Kill the process"""
        self._status = 'killed'
        if self._process.returncode is None:
            try:
                # Kill entire process tree
                self._killProcessTree(self._process.pid)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

        self._resolveExitCode(code if code is not None else SIGKILL_CODE)

    def _killProcessTree(self, pid: int):
        """Kill process tree (cross-platform)"""
        try:
            if os.name == 'nt':
                # Windows
                os.system(f'taskkill /F /T /PID {pid}')
            else:
                # Unix
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass

    def kill(self) -> None:
        """Kill the process"""
        self._doKill()

    def background(self, taskId: str) -> bool:
        """Background the running command"""
        if self._status == 'running':
            self._backgroundTaskId = taskId
            self._status = 'backgrounded'

            # Cancel timeout (no longer needed for backgrounded tasks)
            if self._timeoutTask:
                self._timeoutTask.cancel()
                self._timeoutTask = None

            if self.taskOutput.stdoutToFile:
                # File mode: child writes directly to the fd with no JS involvement.
                # The foreground timeout is gone, so watch file size to prevent
                # a stuck append loop from filling the disk.
                self._startSizeWatchdog()
            else:
                # Pipe mode: spill the in-memory buffer so readers can find it on disk.
                self.taskOutput.spillToDisk()

            return True
        return False

    def _startSizeWatchdog(self):
        """Start watching output file size to prevent disk fill"""
        async def watchdog():
            while self._status == 'backgrounded':
                try:
                    await asyncio.sleep(SIZE_WATCHDOG_INTERVAL_MS / 1000)
                    if os.path.exists(self.taskOutput.path):
                        size = os.path.getsize(self.taskOutput.path)
                        if size > self._maxOutputBytes and self._sizeWatchdogTask is not None:
                            self._killedForSize = True
                            self._clearSizeWatchdog()
                            self._doKill(SIGKILL_CODE)
                            break
                except Exception:
                    pass

        self._sizeWatchdogTask = asyncio.create_task(watchdog())

    def _clearSizeWatchdog(self):
        """Clear the size watchdog"""
        if self._sizeWatchdogTask:
            self._sizeWatchdogTask.cancel()
            self._sizeWatchdogTask = None

    def cleanup(self) -> None:
        """Clean up resources"""
        self._stdoutWrapper.cleanup() if self._stdoutWrapper else None
        self._stderrWrapper.cleanup() if self._stderrWrapper else None
        self.taskOutput.clear()

        # Cancel watchdog
        self._clearSizeWatchdog()

        # Cancel timeout
        if self._timeoutTask:
            self._timeoutTask.cancel()
            self._timeoutTask = None

        # Release references for GC
        self._process = None
        self._onTimeoutCallback = None


# ============================================================
# FACTORY FUNCTIONS
# ============================================================

def wrapSpawn(
    process: asyncio.subprocess.Process,
    abortSignal: asyncio.Event,
    timeout: int,
    taskOutput: TaskOutput,
    shouldAutoBackground: bool = False,
    maxOutputBytes: int = MAX_TASK_OUTPUT_BYTES,
) -> ShellCommand:
    """
    Wraps a child process to enable flexible handling of shell command execution.
    """
    return ShellCommandImpl(
        process,
        abortSignal,
        timeout,
        taskOutput,
        shouldAutoBackground,
        maxOutputBytes,
    )


class AbortedShellCommand(ShellCommand):
    """Static ShellCommand implementation for commands that were aborted before execution."""

    def __init__(
        self,
        backgroundTaskId: Optional[str] = None,
        stderr: Optional[str] = None,
        code: Optional[int] = None,
    ):
        self._status = 'killed'
        self.taskOutput = TaskOutput(generateTaskId('local_bash'), None)
        self._result = ExecResult(
            code=code if code is not None else 145,
            stdout='',
            stderr=stderr or 'Command aborted before execution',
            interrupted=True,
            backgroundTaskId=backgroundTaskId,
        )
        self._resultFuture = asyncio.get_event_loop().create_future()
        self._resultFuture.set_result(self._result)

    @property
    def status(self) -> str:
        return self._status

    async def get_result(self) -> ExecResult:
        return self._result

    def background(self, taskId: str) -> bool:
        return False

    def kill(self) -> None:
        pass

    def cleanup(self) -> None:
        pass


def createAbortedCommand(
    backgroundTaskId: Optional[str] = None,
    opts: Optional[Dict[str, Any]] = None,
) -> ShellCommand:
    """Create an aborted command"""
    if opts is None:
        opts = {}
    return AbortedShellCommand(
        backgroundTaskId=backgroundTaskId,
        stderr=opts.get('stderr'),
        code=opts.get('code'),
    )


def createFailedCommand(preSpawnError: str) -> ShellCommand:
    """Create a failed command"""
    taskOutput = TaskOutput(generateTaskId('local_bash'), None)
    result = ExecResult(
        code=1,
        stdout='',
        stderr=preSpawnError,
        interrupted=False,
        preSpawnError=preSpawnError,
    )

    class FailedCommand(ShellCommand):
        def __init__(self):
            self._status = 'completed'
            self.taskOutput = taskOutput
            self._result = result

        @property
        def status(self) -> str:
            return self._status

        async def get_result(self) -> ExecResult:
            return self._result

        def background(self, taskId: str) -> bool:
            return False

        def kill(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    return FailedCommand()
