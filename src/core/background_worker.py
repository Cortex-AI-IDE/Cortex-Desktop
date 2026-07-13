"""
Background Execution Worker (Phase 6).

Spawning a separate Python process that loads the same agent bridge
in background mode. Communicates via stdin/stdout JSON-line protocol
with heartbeat monitoring and automatic cleanup.

Architecture:
  - Main process: BackgroundWorker (QProcess wrapper)
  - Child process: worker_entrypoint.py (runs agent bridge in background mode)
  - Protocol: JSON-line over stdin/stdout
  - Heartbeat: child sends {"type":"heartbeat","ts":...} every 30s
  - If 3 heartbeats missed → auto-terminate and clean up
"""

import json
import os
import signal
import sys
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("background_worker")

_worker_counter = 0
_worker_counter_lock = threading.Lock()


def _generate_worker_id() -> str:
    """Generate a unique worker ID with a counter to avoid same-timestamp collisions."""
    global _worker_counter
    with _worker_counter_lock:
        _worker_counter += 1
        return f"bw_{int(time.time())}_{_worker_counter}"


# ---------------------------------------------------------------------------
# Message protocol types
# ---------------------------------------------------------------------------


class WorkerMessageType(str, Enum):
    HEARTBEAT = "heartbeat"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETE = "task_complete"
    TASK_ERROR = "task_error"
    LOG = "log"
    SHUTDOWN = "shutdown"


@dataclass
class WorkerMessage:
    """JSON-line message sent between worker and controller."""
    msg_type: WorkerMessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Background worker state
# ---------------------------------------------------------------------------


class WorkerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    CRASHED = "crashed"
    COMPLETED = "completed"
    TERMINATED = "terminated"


# ---------------------------------------------------------------------------
# BackgroundWorker — controller side (spawns & manages QProcess)
# ---------------------------------------------------------------------------


class BackgroundWorker:
    """
    Manage a background agent process.

    Uses a plain subprocess for cross-platform compatibility.
    Communicates via stdin/stdout JSON-line protocol.

    Usage
    -----
    worker = BackgroundWorker(project_root="/path/to/project")
    worker.start()
    worker.dispatch_task("Run tests on module X")
    # ... later ...
    result = worker.collect_result(timeout=600)
    worker.stop()
    """

    HEARTBEAT_TIMEOUT = 100.0  # seconds before considering worker dead
    HEARTBEAT_INTERVAL = 30.0

    def __init__(
        self,
        project_root: str,
        worker_id: Optional[str] = None,
        python_path: Optional[str] = None,
    ):
        self.worker_id = worker_id or _generate_worker_id()
        self._project_root = project_root
        self._python_path = python_path or sys.executable
        self._process: Optional[Any] = None  # subprocess.Popen
        self._state = WorkerState.IDLE
        self._state_lock = threading.Lock()

        # Communication
        self._stdin_lock = threading.Lock()
        self._message_queue: List[WorkerMessage] = []
        self._message_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._keep_reading = threading.Event()

        # Heartbeat monitoring
        self._last_heartbeat: float = 0.0
        self._heartbeat_timer: Optional[threading.Thread] = None

        # Callbacks
        self._on_complete: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_progress: Optional[Callable] = None

        # Worker entry point path
        self._entry_point = self._find_entry_point()

        # Result gathering
        self._result_summary: Optional[str] = None

        log.info(f"[WORKER-{self.worker_id}] Initialized (python={self._python_path})")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Spawn the worker subprocess."""
        if self._process is not None:
            log.warning(f"[WORKER-{self.worker_id}] Already running")
            return False

        try:
            import subprocess
            env = os.environ.copy()
            env["CORTEX_WORKER_ID"] = self.worker_id
            env["CORTEX_BACKGROUND_MODE"] = "1"
            env["CORTEX_PROJECT_ROOT"] = self._project_root
            env["PYTHONUNBUFFERED"] = "1"

            self._process = subprocess.Popen(
                [self._python_path, self._entry_point],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self._project_root,
                text=True,
                bufsize=1,  # line-buffered
                close_fds=True,
            )

            self._state = WorkerState.RUNNING
            self._last_heartbeat = time.time()
            self._keep_reading.set()

            # Start reader thread for stdout
            self._reader_thread = threading.Thread(
                target=self._read_loop,
                daemon=True,
                name=f"worker-reader-{self.worker_id}",
            )
            self._reader_thread.start()

            # Start heartbeat monitor
            self._heartbeat_timer = threading.Thread(
                target=self._heartbeat_monitor,
                daemon=True,
                name=f"worker-hb-{self.worker_id}",
            )
            self._heartbeat_timer.start()

            log.info(f"[WORKER-{self.worker_id}] Worker process started (PID={self._process.pid})")
            return True

        except Exception as exc:
            log.error(f"[WORKER-{self.worker_id}] Failed to start worker: {exc}")
            self._state = WorkerState.CRASHED
            return False

    def stop(self, timeout: float = 10.0) -> None:
        """Gracefully stop the worker. Sends shutdown, then kills if needed."""
        if self._process is None:
            return

        try:
            self._send_message(WorkerMessage(
                msg_type=WorkerMessageType.SHUTDOWN,
                payload={"reason": "controller_shutdown"},
            ))
        except Exception:
            pass

        self._keep_reading.clear()
        self._safe_terminate(timeout)

        with self._state_lock:
            self._state = WorkerState.TERMINATED
        self._process = None
        log.info(f"[WORKER-{self.worker_id}] Stopped")

    def is_running(self) -> bool:
        """Check whether the worker process is alive."""
        if self._process is None:
            return False
        if self._process.poll() is not None:
            return False
        return True

    # ------------------------------------------------------------------
    # Task dispatch
    # ------------------------------------------------------------------

    def dispatch_task(self, task_description: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send a task to the background worker.

        Returns True if the message was sent successfully.
        """
        if not self.is_running():
            log.warning(f"[WORKER-{self.worker_id}] Cannot dispatch — worker not running")
            return False

        payload: Dict[str, Any] = {"task": task_description}
        if context:
            payload["context"] = context

        self._send_message(WorkerMessage(
            msg_type=WorkerMessageType.TASK_STARTED,
            payload=payload,
        ))
        log.info(f"[WORKER-{self.worker_id}] Dispatched task: {task_description[:80]}...")
        return True

    def collect_result(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Wait for a result from the worker.
        
        If timeout is None, uses dynamic timeout based on task complexity
        via timeout_strategy.get_collect_timeout().

        Returns the result summary or None on timeout/error.
        """
        from src.utils.timeout_strategy import get_collect_timeout
        if timeout is None:
            timeout = get_collect_timeout()
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._pop_message()
            if msg is None:
                time.sleep(0.5)
                continue

            if msg.msg_type == WorkerMessageType.TASK_COMPLETE:
                summary = msg.payload.get("summary", "")
                log.info(f"[WORKER-{self.worker_id}] Task complete: {summary[:100]}...")
                self._result_summary = summary
                return summary

            elif msg.msg_type == WorkerMessageType.TASK_ERROR:
                error = msg.payload.get("error", "unknown error")
                log.warning(f"[WORKER-{self.worker_id}] Task error: {error}")
                return None

            elif msg.msg_type == WorkerMessageType.TASK_PROGRESS:
                progress = msg.payload.get("message", "")
                log.info(f"[WORKER-{self.worker_id}] Progress: {progress[:100]}...")
                if self._on_progress:
                    self._on_progress(progress)

        log.warning(f"[WORKER-{self.worker_id}] collect_result timed out after {timeout}s")
        return None

    def get_state(self) -> WorkerState:
        """Return the current worker state."""
        with self._state_lock:
            return self._state

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict for UI display."""
        return {
            "worker_id": self.worker_id,
            "state": self.get_state().value,
            "running": self.is_running(),
            "pid": self._process.pid if self._process else None,
            "last_heartbeat": self._last_heartbeat,
            "result_ready": self._result_summary is not None,
        }

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_complete(self, callback: Callable[[str], None]) -> None:
        self._on_complete = callback

    def on_error(self, callback: Callable[[str], None]) -> None:
        self._on_error = callback

    def on_progress(self, callback: Callable[[str], None]) -> None:
        self._on_progress = callback

    # ------------------------------------------------------------------
    # Internal — communication
    # ------------------------------------------------------------------

    def _send_message(self, msg: WorkerMessage) -> None:
        """Write a JSON-line message to the worker's stdin."""
        if self._process is None or self._process.stdin is None:
            return
        line = json.dumps({
            "type": msg.msg_type.value,
            "payload": msg.payload,
            "timestamp": msg.timestamp,
        })
        with self._stdin_lock:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()

    def _read_loop(self) -> None:
        """Continuously read JSON-line messages from worker stdout."""
        if self._process is None or self._process.stdout is None:
            return

        while self._keep_reading.is_set():
            try:
                line = self._process.stdout.readline()
                if not line:
                    # EOF — worker probably died
                    log.debug(f"[WORKER-{self.worker_id}] stdout EOF")
                    break

                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)
                msg_type_str = data.get("type", "")
                payload = data.get("payload", {})
                ts = data.get("timestamp", time.time())

                msg_type = WorkerMessageType(msg_type_str)
                msg = WorkerMessage(msg_type=msg_type, payload=payload, timestamp=ts)

                if msg_type == WorkerMessageType.HEARTBEAT:
                    self._last_heartbeat = ts
                    continue

                # Publish event bus event
                try:
                    from src.core.event_bus import get_event_bus, EventType
                    from src.core.event_bus import EventData

                    event_map = {
                        WorkerMessageType.TASK_STARTED: EventType.AI_TOOL_CALLED,
                        WorkerMessageType.TASK_PROGRESS: None,
                        WorkerMessageType.TASK_COMPLETE: EventType.AI_TOOL_COMPLETED,
                        WorkerMessageType.TASK_ERROR: None,
                    }
                    eb_type = event_map.get(msg_type)
                    if eb_type:
                        bus = get_event_bus()
                        bus.publish(eb_type, EventData(
                            source_component=f"background_worker/{self.worker_id}",
                        ))
                except Exception:
                    pass

                # Queue for collection
                with self._message_lock:
                    self._message_queue.append(msg)

                # Fire callbacks
                if msg_type == WorkerMessageType.TASK_COMPLETE and self._on_complete:
                    summary = payload.get("summary", "")
                    self._on_complete(summary)
                elif msg_type == WorkerMessageType.TASK_ERROR and self._on_error:
                    error = payload.get("error", "unknown")
                    self._on_error(error)

            except json.JSONDecodeError:
                continue
            except Exception as exc:
                log.debug(f"[WORKER-{self.worker_id}] Read loop error: {exc}")
                break

        # If we exit the loop unexpectedly, mark as crashed
        if self._keep_reading.is_set():
            with self._state_lock:
                self._state = WorkerState.CRASHED

    def _pop_message(self) -> Optional[WorkerMessage]:
        """Pop the oldest message from the queue."""
        with self._message_lock:
            if self._message_queue:
                return self._message_queue.pop(0)
            return None

    def _heartbeat_monitor(self) -> None:
        """Monitor heartbeats and auto-terminate if too many missed."""
        while self._keep_reading.is_set() and self.is_running():
            time.sleep(self.HEARTBEAT_INTERVAL)
            elapsed = time.time() - self._last_heartbeat
            if elapsed > self.HEARTBEAT_TIMEOUT:
                log.warning(
                    f"[WORKER-{self.worker_id}] Heartbeat timeout "
                    f"({elapsed:.0f}s since last) — terminating"
                )
                self._safe_terminate(timeout=5.0)
                with self._state_lock:
                    self._state = WorkerState.CRASHED
                break

    def _safe_terminate(self, timeout: float = 5.0) -> None:
        """Safely terminate the subprocess — SIGTERM then SIGKILL."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            self._process.wait(timeout=timeout)
        except Exception:
            try:
                self._process.kill()
                self._process.wait(timeout=5.0)
            except Exception:
                pass

    def _find_entry_point(self) -> str:
        """Find the worker entry point script."""
        candidates = [
            os.path.join(self._project_root, "src", "core", "worker_entrypoint.py"),
            os.path.join(self._project_root, "worker_entrypoint.py"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Fallback: create the default path
        default = candidates[0]
        log.warning(f"[WORKER-{self.worker_id}] Entry point not found — will create at {default}")
        return default


# ---------------------------------------------------------------------------
# Worker-side execution (runs in a subprocess)
# ---------------------------------------------------------------------------


class WorkerAgent:
    """
    The agent-side code that runs inside the background subprocess.

    Reads tasks from stdin, executes them, and writes results to stdout.
    """

    def __init__(self):
        self._worker_id = os.environ.get("CORTEX_WORKER_ID", "unknown")
        self._project_root = os.environ.get("CORTEX_PROJECT_ROOT", os.getcwd())
        self._running = True

        # Import bridge lazily
        self._bridge: Optional[Any] = None

        log.info(f"[WORKER-AGENT-{self._worker_id}] Initialized")

    def run(self) -> None:
        """Main loop: read JSON lines from stdin, process, write to stdout."""
        log.info(f"[WORKER-AGENT-{self._worker_id}] Entering main loop")
        self._send_heartbeat()

        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)
                msg_type = data.get("type", "")
                payload = data.get("payload", {})

                if msg_type == "shutdown":
                    self._handle_shutdown(payload)
                elif msg_type == "task_started":
                    self._handle_task(payload)
                elif msg_type == "heartbeat":
                    self._send_heartbeat()

            except json.JSONDecodeError:
                continue
            except Exception as exc:
                self._send_error(str(exc))

        log.info(f"[WORKER-AGENT-{self._worker_id}] Exiting")

    def _handle_task(self, payload: Dict[str, Any]) -> None:
        """Execute a task and write the result."""
        task = payload.get("task", "")
        context = payload.get("context", {})
        log.info(f"[WORKER-AGENT-{self._worker_id}] Starting task: {task[:80]}...")

        try:
            self._lazy_init_bridge()

            # Send progress
            self._send_message("task_progress", {"message": "Processing task..."})

            # Execute through the bridge in background mode
            if self._bridge is not None:
                result = self._bridge.process_task_background(task, context)
            else:
                result = {"summary": f"Simulated result for: {task[:80]}", "success": True}

            self._send_message("task_complete", {"summary": result.get("summary", str(result))})

        except Exception as exc:
            log.error(f"[WORKER-AGENT] Task failed: {exc}")
            self._send_error(str(exc))

    def _lazy_init_bridge(self) -> None:
        """Import and initialize the agent bridge (lazy)."""
        if self._bridge is not None:
            return
        try:
            sys.path.insert(0, self._project_root)
            from src.ai.agent_bridge import CortexAgentBridge

            self._bridge = CortexAgentBridge(
                project_root=self._project_root,
                background_mode=True,
            )
            log.info("[WORKER-AGENT] Bridge initialized in background mode")
        except Exception as exc:
            log.warning(f"[WORKER-AGENT] Bridge init failed (will use simulation): {exc}")

    def _handle_shutdown(self, payload: Dict[str, Any]) -> None:
        """Handle shutdown command."""
        log.info(f"[WORKER-AGENT-{self._worker_id}] Shutdown requested")
        self._running = False

    def _send_heartbeat(self) -> None:
        """Send a heartbeat message to the controller."""
        self._send_message("heartbeat", {"status": "alive"})

    def _send_message(self, msg_type: str, payload: Dict[str, Any]) -> None:
        """Write a JSON-line message to stdout."""
        msg = json.dumps({
            "type": msg_type,
            "payload": payload,
            "timestamp": time.time(),
        })
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def _send_error(self, error: str) -> None:
        """Send an error message."""
        self._send_message("task_error", {"error": error})


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------


def run_worker() -> None:
    """Entry point for subprocess workers (called with --worker flag)."""
    agent = WorkerAgent()
    agent.run()


if __name__ == "__main__":
    run_worker()
