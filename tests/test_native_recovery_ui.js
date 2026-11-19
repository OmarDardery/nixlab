"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../admin/static/incus-secrets.js"), "utf8");
const controls = {
  "native-recovery-export-passphrase": {value: "offline-recovery-test-passphrase"},
  "native-recovery-export-confirm": {value: "offline-recovery-test-passphrase"},
  "native-recovery-export-admin": {value: "admin-test-password"},
  "native-recovery-backup": {checked: true},
  "native-recovery-kit-file": {files: [{
    size: 65, arrayBuffer: async () => Buffer.from('{"format":"nixlab-recovery-kit","version":1}')
  }]},
  "native-recovery-import-passphrase": {value: "offline-recovery-test-passphrase"},
  "native-recovery-import-admin": {value: "admin-test-password"},
  "native-recovery-import-ack": {checked: false},
  "native-reindex-password": {value: "admin-test-password"},
  "native-reindex-ack": {checked: true},
};
const calls = [], downloads = [];
const encrypted = Buffer.from('{"format":"nixlab-recovery-kit","version":1}');
const ctx = vm.createContext({
  document: {getElementById: id => controls[id] || null},
  Uint8Array, TextEncoder, atob, btoa, console,
});
vm.runInContext(source, ctx);
const evalJS = expression => vm.runInContext(expression, ctx);
ctx.state = {page: "projects", principal: {role: "admin"}};
ctx.card = (title, body) => title + body;
ctx.esc = value => String(value).replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
ctx.shell = () => {};
ctx.download = (name, data, type) => downloads.push({name, data: Buffer.from(data), type});
ctx.api = async (route, body) => {
  calls.push({route, body});
  if (route === "project-secrets/recovery-export")
    return {encrypted: encrypted.toString("base64"), verified: false,
            requiresOfflineUnlockVerification: true, recipient: "age1" + "q".repeat(58)};
  if (route === "project-secrets/recovery-verify")
    return {valid: true, recipient: "age1" + "q".repeat(58)};
  if (route === "project-secrets/reindex")
    return {indexed: true, revision: "a".repeat(64)};
  if (route === "project-secrets")
    return ctx.inventory;
  throw Error("Unexpected recovery endpoint: " + route);
};
async function main() {
  ctx.inventory = {
    identity: {healthy: true, configured: true, recipient: "age1" + "q".repeat(58)},
    projects: [{project: "example", secretCount: 1, keys: ["TOKEN"], revision: "a".repeat(64), indexed: true}],
  };
  evalJS("nativeSecretsView.data = inventory; nativeSecretsView.loaded = true; nativeSecretsView.selected = 'example'");
  assert.match(evalJS("nativeSecretsPanel()"), /Download encrypted recovery kit/);
  await evalJS("nativeRecoveryExport()");
  assert.equal(calls[0].route, "project-secrets/recovery-export");
  assert.equal(calls[0].body.current, "admin-test-password");
  assert.equal(calls[0].body.includeBackup, true);
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].name, "nixlab-project-secrets-recovery.json");
  assert.deepEqual(downloads[0].data, encrypted);
  for (const id of ["native-recovery-export-passphrase", "native-recovery-export-confirm", "native-recovery-export-admin"])
    assert.equal(controls[id].value, "", "Sensitive browser control must clear: " + id);
  assert.ok(!evalJS("JSON.stringify(nativeSecretsView)").includes("offline-recovery-test-passphrase"));
  await evalJS("nativeRecoveryImport('verify')");
  assert.equal(calls[1].route, "project-secrets/recovery-verify");
  assert.equal(calls[1].body.current, "admin-test-password");
  assert.equal(controls["native-recovery-import-passphrase"].value, "");
  assert.equal(controls["native-recovery-import-admin"].value, "");
  assert.match(evalJS("nativeSecretsView.message"), /authenticated/);

  ctx.inventory.projects[0].indexed = false;
  evalJS("nativeSecretsView.data = inventory");
  assert.match(evalJS("nativeSecretsPanel()"), /Rebuild metadata/);
  await evalJS("nativeSecretsReindex()");
  assert.equal(calls[2].route, "project-secrets/reindex");
  assert.equal(calls[2].body.acknowledge, true);
  assert.equal(controls["native-reindex-password"].value, "");
  assert.ok(!calls.some(call => /decrypt|reveal/i.test(call.route)));
  console.log("Secrets recovery UI: encrypted-only download, verification, reauth and erased inputs passed");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
