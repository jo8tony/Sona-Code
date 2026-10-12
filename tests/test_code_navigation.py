"""Browser language protocol and definition interaction state."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('scenario', ['flush', 'debounce', 'stale', 'epoch', 'generation',
                                     'cancel', 'reconnect', 'idle', 'limit', 'unavailable',
                                     'provider', 'click', 'candidates', 'intent', 'settings',
                                     'requests', 'message-limit', 'sync-failure', 'timeout',
                                     'cancel-sync', 'deadline', 'empty-reanalyze', 'boundary-error', 'capacity-status',
                                     'implementation-provider', 'implementation-candidates', 'implementation-intent',
                                     'implementation-flush', 'implementation-stale', 'implementation-epoch',
                                     'implementation-generation', 'implementation-cancel', 'implementation-limit',
                                     'implementation-requests', 'implementation-timeout', 'implementation-cancel-sync',
                                     'implementation-deadline', 'implementation-unsupported', 'implementation-mixed-requests',
                                     'auto-declaration', 'auto-use', 'auto-empty', 'auto-stale', 'auto-reference-stale',
                                     'auto-unsupported', 'auto-modifiers',
                                     *['references-' + name for name in ('provider', 'candidates', 'intent', 'flush', 'stale',
                                        'epoch', 'generation', 'cancel', 'limit', 'requests', 'timeout', 'cancel-sync',
                                        'deadline', 'unsupported', 'mixed-requests')]])
def test_code_navigation(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for navigation state coverage')
    subprocess.run([node, 'tests/code-navigation-state.cjs', scenario],
                   cwd=Path(__file__).parents[1], check=True, timeout=10)
