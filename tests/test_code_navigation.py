"""Browser language protocol and definition interaction state."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('scenario', ['flush', 'debounce', 'stale', 'epoch', 'generation',
                                     'cancel', 'reconnect', 'idle', 'limit', 'unavailable',
                                     'provider', 'click', 'candidates', 'intent', 'settings',
                                     'requests', 'message-limit', 'sync-failure', 'timeout',
                                     'cancel-sync', 'deadline', 'empty-reanalyze', 'boundary-error', 'capacity-status'])
def test_code_navigation(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for navigation state coverage')
    subprocess.run([node, 'tests/code-navigation-state.cjs', scenario],
                   cwd=Path(__file__).parents[1], check=True, timeout=10)
