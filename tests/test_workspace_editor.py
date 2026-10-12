"""Editor model lifetime and unsaved edits across delayed local API responses."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["reload", "reopen", "save", "shortcuts", "autosave",
                                      "history", "project-isolation", "unopened-opener", "navigation-failure", "navigation-close", "navigation-order", "language-sync", "definition-load-cancel", "tab-close-progress", "tab-hide-draft", "panel-close-language-status"])
def test_editor_delayed_responses(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for editor state coverage")
    subprocess.run([node, "tests/workspace-editor-state.cjs", scenario],
                   cwd=Path(__file__).parents[1], check=True, timeout=10)
