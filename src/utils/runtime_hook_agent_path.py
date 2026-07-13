"""
PyInstaller Runtime Hook — Add src/agent/src to sys.path.

Many agent submodules use bare `from utils.X import Y` imports that assume
`src/agent/src/` is on sys.path. In frozen builds, only sys._MEIPASS is on
the path. This hook adds the agent src directory so those imports resolve.
"""
import sys
import os

if getattr(sys, 'frozen', False):
    # Frozen build: add _MEIPASS/src/agent/src to sys.path
    agent_src = os.path.join(sys._MEIPASS, 'src', 'agent', 'src')
    if os.path.isdir(agent_src) and agent_src not in sys.path:
        sys.path.insert(0, agent_src)
