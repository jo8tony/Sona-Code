"""Network denial, launcher overrides and an update with no network attempts."""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "packaging/openspec-launcher/offline.cjs"


def test_offline_policy_blocks_commonjs_esm_fetch_dns_and_sockets(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to verify the distributed offline policy")
    requests = []
    class Listener(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Listener)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    probe = tmp_path / "probe.mjs"
    probe.write_text('''
import { request } from 'node:http';
import { connect } from 'node:net';
import dns from 'node:dns';
import https from 'node:https';
import tls from 'node:tls';
import http2 from 'node:http2';
import dgram from 'node:dgram';
const url = process.argv[2];
const socket = dgram.createSocket('udp4');
const attempts = [() => request(url), () => fetch(url), () => connect(new URL(url).port, '127.0.0.1'),
  () => dns.lookup('example.invalid', () => {}), () => dns.promises.lookup('example.invalid'),
  () => new dns.Resolver().resolve4('example.invalid', () => {}),
  () => new dns.promises.Resolver().resolve4('example.invalid'),
  () => https.get(url.replace('http:', 'https:')), () => tls.connect({host:'127.0.0.1',port:443}),
  () => http2.connect(url), () => socket.send('test', 9, '127.0.0.1')];
const errors = [];
for (const attempt of attempts) { try { await attempt(); errors.push('allowed'); } catch(error) { errors.push(error.code); } }
socket.close();
console.log(JSON.stringify(errors));
''', encoding="utf-8")
    try:
        result = subprocess.run([node, "--require", str(POLICY), str(probe), f"http://127.0.0.1:{server.server_port}/check"],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == ["SONACODE_OPENSPEC_OFFLINE"] * 11
        assert not requests
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def native_bundle():
    root = os.environ.get("OPENSPEC_TEST_BUNDLE")
    if not root:
        pytest.skip("set OPENSPEC_TEST_BUNDLE to test the bundled runtime")
    return Path(root)


def test_native_launcher_forces_offline_flags_and_never_falls_back(native_bundle, tmp_path):
    root = tmp_path / "中文 offline runtime"
    suffix = ".exe" if os.name == "nt" else ""
    for relative in (f"bin/openspec{suffix}", f"runtime/node{suffix}", "runtime/offline.cjs", "defaults/openspec-config.json"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(native_bundle / relative, target)
    cli = root / "package/node_modules/@fission-ai/openspec/bin/openspec.js"
    cli.parent.mkdir(parents=True)
    cli.write_text('''
const {OPENSPEC_TELEMETRY,OPENSPEC_NO_UPDATE_CHECK,DO_NOT_TRACK,NODE_OPTIONS}=process.env;
fetch('http://127.0.0.1:9').catch(error => console.log(JSON.stringify({
  OPENSPEC_TELEMETRY,OPENSPEC_NO_UPDATE_CHECK,DO_NOT_TRACK,NODE_OPTIONS,code:error.code})));
''', encoding="utf-8")
    env = {**os.environ, "OPENSPEC_TELEMETRY": "1", "OPENSPEC_NO_UPDATE_CHECK": "", "DO_NOT_TRACK": "0",
           "NODE_OPTIONS": "--invalid-option", "SONACODE_OPENSPEC_CONFIG_HOME": str(tmp_path / "config")}
    executable = str(root / f"bin/openspec{suffix}")
    result = subprocess.run([executable], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"OPENSPEC_TELEMETRY": "0", "OPENSPEC_NO_UPDATE_CHECK": "1",
        "DO_NOT_TRACK": "1", "code": "SONACODE_OPENSPEC_OFFLINE"}
    (root / f"runtime/node{suffix}").unlink()
    missing = subprocess.run([executable, "--version"], env=env, capture_output=True, timeout=10)
    assert missing.returncode == 127


def test_official_update_with_offline_flags_makes_zero_network_attempts(native_bundle, tmp_path):
    project = tmp_path / "project"
    shutil.copytree(native_bundle / "templates", project)
    probe = tmp_path / "audit.cjs"
    audit = tmp_path / "audit.json"
    probe.write_text('''
const fs = require('node:fs');
const {syncBuiltinESMExports} = require('node:module');
let count = 0;
function spy(object, name) { const original = object[name]; object[name] = function(...args) { count++; return original.apply(this,args); }; }
spy(globalThis,'fetch');
for(const name of ['node:http','node:https']) { spy(require(name),'request'); spy(require(name),'get'); }
spy(require('node:net').Socket.prototype,'connect');
spy(require('node:dns'),'lookup');
syncBuiltinESMExports();
process.on('exit', () => fs.writeFileSync(process.env.OPENSPEC_TEST_AUDIT, JSON.stringify({count})));
''', encoding="utf-8")
    config = tmp_path / "config/openspec/config.json"
    config.parent.mkdir(parents=True)
    config.write_bytes((native_bundle / "defaults/openspec-config.json").read_bytes())
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "OPENSPEC_TELEMETRY": "0", "OPENSPEC_NO_UPDATE_CHECK": "1", "DO_NOT_TRACK": "1",
           "CI": "false", "NODE_ENV": "production", "OPENSPEC_TEST_AUDIT": str(audit),
           "XDG_CONFIG_HOME": str(config.parent.parent), "XDG_DATA_HOME": str(tmp_path / "data"),
           "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home / ".codex")}
    env.pop("NODE_OPTIONS", None)
    node = native_bundle / "runtime" / ("node.exe" if os.name == "nt" else "node")
    cli = native_bundle / "package/node_modules/@fission-ai/openspec/bin/openspec.js"
    result = subprocess.run([str(node), "--require", str(native_bundle / "runtime/offline.cjs"), "--require", str(probe),
        str(cli), "update"], cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(audit.read_text()) == {"count": 0}
