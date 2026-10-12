"""Position history state machine, independent of Monaco and language services."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('scenario', ['chain', 'limit', 'deleted', 'busy', 'failed', 'clear', 'snapshot'])
def test_code_history(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for history state coverage')
    subprocess.run([node, 'tests/code-history-state.cjs', scenario],
                   cwd=Path(__file__).parents[1], check=True, timeout=10)
