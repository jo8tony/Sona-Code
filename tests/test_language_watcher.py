import asyncio
import os
import subprocess

import pytest

from sona_code.language_services.watcher import ProjectWatcher


@pytest.mark.asyncio
async def test_source_changes_and_saved_notifications_are_deduplicated(tmp_path):
    source = tmp_path / 'a.py'; source.write_text('a')
    batches = []
    async def emit(events):
        batches.extend(events)
    watcher = ProjectWatcher(tmp_path, 'python', emit)
    await watcher.start()
    source.write_text('changed')
    await watcher.scan()
    assert batches == [{'uri': source.as_uri(), 'type': 2}]
    batches.clear()
    source.write_text('saved'); await watcher.saved('a.py'); await watcher.scan()
    assert batches == [{'uri': source.as_uri(), 'type': 2}]
    batches.clear()
    new = tmp_path / 'b.py'; new.write_text('b'); source.unlink()
    await watcher.scan()
    assert sorted((e['uri'], e['type']) for e in batches) == sorted([(source.as_uri(), 3), (new.as_uri(), 1)])
    await watcher.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction watcher')
@pytest.mark.asyncio
async def test_watcher_does_not_cross_junction(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'secret.py').write_text('x')
    link = root / 'link'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    watcher = ProjectWatcher(root, 'python', lambda events: asyncio.sleep(0))
    try:
        await watcher.start()
        assert watcher.snapshot == {}
    finally:
        await watcher.close()
        link.rmdir()


@pytest.mark.asyncio
async def test_watcher_excludes_dependencies_links_and_enforces_limit(tmp_path):
    for folder in ('node_modules', '.git', '.venv', 'target', 'build', 'dist'):
        path = tmp_path / folder; path.mkdir(); (path / 'a.py').write_text('ignored')
    for name in ('a.py', 'b.py', 'c.py'):
        (tmp_path / name).write_text('tracked')
    (tmp_path / 'wrong.java').write_text('ignored')
    watcher = ProjectWatcher(tmp_path, 'python', lambda events: asyncio.sleep(0), limit=2)
    await watcher.start()
    assert len(watcher.snapshot) == 2 and watcher.limited
    assert all('/' not in path for path in watcher.snapshot)
    await watcher.close()
