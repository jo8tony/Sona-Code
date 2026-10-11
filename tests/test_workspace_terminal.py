"""Project shell lifecycle, stale creation, and terminal transport coverage."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_project_terminal_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for project terminal coverage")
    subprocess.run([node, "tests/workspace-terminal-state.cjs"],
                   cwd=Path(__file__).absolute().parents[1], check=True, timeout=10)
