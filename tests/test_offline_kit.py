"""Offline build payloads cannot silently use stale dependencies or escaped paths."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def kit_module():
    spec = importlib.util.spec_from_file_location("offline_kit", Path(__file__).parents[1] / "scripts/offline-kit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_inputs_exist_in_repository():
    module = kit_module()
    root = Path(__file__).parents[1]
    assert all((root / path).is_file() for path in module.INPUTS)


@pytest.fixture
def payload(tmp_path):
    kit, source = tmp_path / "kit", tmp_path / "source"
    kit.mkdir(); source.mkdir()
    (kit / "asset").write_bytes(b"pinned dependency")
    (source / "package-lock.json").write_bytes(b"lock")
    manifest = {"files": {"asset": hashlib.sha256(b"pinned dependency").hexdigest()},
                "source_inputs": {"package-lock.json": hashlib.sha256(b"lock").hexdigest()}}
    (kit / "manifest.json").write_text(json.dumps(manifest))
    return kit, source, manifest


def test_verified_payload_and_corruption(payload):
    kit, source, _ = payload
    module = kit_module()
    module.verify_kit(kit, source)
    (kit / "asset").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="asset"):
        module.verify_kit(kit, source)


def test_changed_dependency_lock_requires_new_kit(payload):
    kit, source, _ = payload
    (source / "package-lock.json").write_bytes(b"new lock")
    with pytest.raises(RuntimeError, match="package-lock"):
        kit_module().verify_kit(kit, source)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "a\\escape"])
def test_manifest_paths_must_be_relative(payload, path):
    kit, source, manifest = payload
    manifest["files"] = {path: "0" * 64}
    (kit / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="path"):
        kit_module().verify_kit(kit, source)
