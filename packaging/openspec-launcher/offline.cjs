"use strict";

// Applied only to the bundled OpenSpec child, never to Sona's model providers.
// Environment flags suppress the upstream check; these hooks additionally
// prevent accidental outbound traffic from the CLI or its dependencies.
const { syncBuiltinESMExports } = require("node:module");
function denied() {
  const error = new Error("Sona 内置 OpenSpec 为离线模式，不允许网络访问；版本随客户端安装包升级。");
  error.code = "SONACODE_OPENSPEC_OFFLINE";
  throw error;
}
require("node:net").Socket.prototype.connect = denied;
require("node:tls").connect = denied;
for (const name of ["node:http", "node:https"]) {
  const transport = require(name);
  transport.request = denied;
  transport.get = denied;
}
require("node:http2").connect = denied;
const dns = require("node:dns");
for (const name of ["lookup", "lookupService", "resolve", "resolve4", "resolve6", "resolveAny",
  "resolveCaa", "resolveCname", "resolveMx", "resolveNaptr", "resolveNs", "resolvePtr",
  "resolveSoa", "resolveSrv", "resolveTxt", "reverse"]) {
  dns[name] = denied;
  if (name in dns.promises) dns.promises[name] = async () => denied();
  if (name.startsWith("resolve") || name === "reverse") {
    if (name in dns.Resolver.prototype) dns.Resolver.prototype[name] = denied;
    if (name in dns.promises.Resolver.prototype) dns.promises.Resolver.prototype[name] = async () => denied();
  }
}
require("node:dgram").Socket.prototype.send = denied;
globalThis.fetch = async () => denied();
syncBuiltinESMExports();
