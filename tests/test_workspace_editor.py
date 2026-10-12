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


def test_context_menu_has_opaque_surface_and_fallback_colors():
    css = (Path(__file__).parents[1] / 'sona_code/web/static/workspace-editor.css').read_text(encoding='utf-8')
    variables = css.split('.wsp-editor-canvas {\n  --vscode-menu-background:', 1)[1].split('}', 1)[0]
    assert 'var(--panel)' in variables
    assert '--vscode-menu-foreground: var(--text)' in variables
    assert '--vscode-list-hoverBackground: var(--accent-soft)' in variables
    menu = css.split('.wsp-editor .monaco-menu-container {', 1)[1].split('}', 1)[0]
    assert 'background: var(--panel)' in menu and 'box-shadow:' in menu
