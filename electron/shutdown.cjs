"use strict";

const http = require("node:http");
const { spawn } = require("node:child_process");

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

function requestShutdown(origin, token, timeoutMs) {
  return new Promise(resolve => {
    // Use a wall-clock deadline: a streaming response must not extend exit forever.
    const request = http.request(`${origin}/__recorder/api/shutdown`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Length": "0" },
    }, response => {
      response.resume();
      response.once("end", () => resolve(response.statusCode === 200));
      response.once("error", () => resolve(false));
      response.once("close", () => resolve(false));
    });
    const timer = setTimeout(() => { request.destroy(); resolve(false); }, timeoutMs);
    request.once("close", () => clearTimeout(timer));
    request.once("error", () => resolve(false));
    request.end();
  });
}

function killTree(pid) {
  return new Promise((resolve, reject) => {
    const killer = spawn("taskkill", ["/F", "/T", "/PID", String(pid)], {
      windowsHide: true, stdio: "ignore",
    });
    const timer = setTimeout(() => { killer.kill(); reject(new Error("进程清理超时")); }, 5000);
    killer.once("error", error => { clearTimeout(timer); reject(error); });
    killer.once("exit", code => { clearTimeout(timer); resolve(code); });
  });
}

async function shutdownOwnedChild(child, origin, token, options = {}) {
  if (!child?.pid) return;
  const exited = () => child.exitCode !== null || child.signalCode !== null;
  if (exited()) return;
  const request = options.request || requestShutdown;
  const force = options.force || killTree;
  const waitMs = options.waitMs ?? 5000;
  await request(origin, token, options.requestMs ?? 20000);
  const deadline = Date.now() + waitMs;
  while (!exited() && Date.now() < deadline) await sleep(50);
  if (exited()) return;
  // Address the ChildProcess we spawned, never a listener found by port/name.
  await force(child.pid);
  const forceDeadline = Date.now() + waitMs;
  while (!exited() && Date.now() < forceDeadline) await sleep(50);
  if (!exited()) throw new Error("后台服务尚未退出，请重试退出");
}

module.exports = { shutdownOwnedChild, requestShutdown };
