"""
Worker entry point for background execution (Phase 6).

This script is spawned as a subprocess by BackgroundWorker and loads
the agent bridge in background mode.

Usage:
    python -m src.core.worker_entrypoint
"""

import os
import sys

# Ensure the project root is on sys.path
_project_root = os.environ.get("CORTEX_PROJECT_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.core.background_worker import run_worker

if __name__ == "__main__":
    run_worker()
