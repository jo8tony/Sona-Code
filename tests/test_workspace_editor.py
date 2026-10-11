"""Editor model lifetime and unsaved edits across delayed local API responses."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["reload", "reopen", "save", "shortcuts", "autosave",
                                      "history", "navigation-failure", "navigation-close", "navigation-order"])
def test_editor_delayed_responses(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for editor state coverage")
    subprocess.run([node, "tests/workspace-editor-state.cjs", scenario],
                   cwd=Path(__file__).parents[1], check=True, timeout=10)
