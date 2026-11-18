"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const code = fs.readFileSync(path.join(__dirname, "../admin/static/incus-secrets.js"), "utf8");
const controls = {
  "native-secret-key": {value: "API_TOKEN"},
  "native-secret-value": {value: "private-value-not-in-UI"},
  "native-secret-action": {value: "set"},
  "native-secret-review-password": {value: "admin-test-password"},
  "native-secret-publish-password": {value: "admin-test-password"},
  "native-secret-publish-ack": {checked: true},
};
const sent = [];
const ctx = vm.createContext({TextEncoder,
  document: {getElementById: id => controls[id] || null}, console,
});
vm.runInContext(code, ctx);
const run = value => vm.runInContext(value, ctx);
ctx.state = {page: "projects", principal: {role: "admin"}};
ctx.card = (title, body) => title + body;
ctx.esc = value => String(value).replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
ctx.shell = () => {};
const base = {project: "alpha", keys: [], secretCount: 0, revision: null, indexed: true};
ctx.inventory = {identity: {healthy: true, configured: true, recipient: "age1" + "a".repeat(58)},
                 projects: [base]};
ctx.api = async (route, body) => {
  sent.push({route, body});
  if (route === "project-secrets/review")
    return {reviewId: "d".repeat(32), project: "alpha", key: "API_TOKEN", action: "set",
            baseRevision: null, revision: "f".repeat(64), keys: ["API_TOKEN"],
            requiresApply: true};
  if (route === "project-secrets/publish")
    return {project: "alpha", published: true, requiresApply: true,
            revision: "f".repeat(64)};
  if (route === "project-secrets")
    return {...ctx.inventory, projects: [{...base, keys: ["API_TOKEN"], secretCount: 1,
             revision: "f".repeat(64)}]};
  throw Error("Unexpected endpoint " + route);
};
async function main() {
  run("nativeSecretsView.data = inventory; nativeSecretsView.loaded = true; nativeSecretsView.selected = 'alpha'");
  let panel = run("nativeSecretsPanel()");
  assert.match(panel, /Environment & secrets/);
  assert.match(panel, /Encrypted, declarative variables/);
  assert.doesNotMatch(panel, /private-value-not-in-UI/);
  assert.ok(!sent.length, "Rendering must not call a broker");

  await run("nativeSecretReview()");
  assert.equal(sent[0].route, "project-secrets/review");
  assert.equal(sent[0].body.current, "admin-test-password");
  assert.equal(sent[0].body.value, "private-value-not-in-UI");
  assert.equal(controls["native-secret-value"].value, "");
  assert.equal(controls["native-secret-review-password"].value, "");
  panel = run("nativeSecretsPanel()");
  assert.match(panel, /Approve ciphertext-only publication/);
  assert.doesNotMatch(panel, /private-value-not-in-UI/);
  const serialized = run("JSON.stringify(nativeSecretsView)");
  assert.ok(!serialized.includes("private-value-not-in-UI"),
            "Secret value must never enter persistent UI state");
  await run("nativeSecretPublish()");
  assert.equal(sent[1].route, "project-secrets/publish");
  assert.deepEqual(JSON.parse(JSON.stringify(sent[1].body)), {
    reviewId: "d".repeat(32), current: "admin-test-password", acknowledge: true,
  });
  assert.equal(controls["native-secret-publish-password"].value, "");
  assert.equal(run("nativeSecretsView.review"), null);
  assert.equal(run("nativeSecretsView.data.projects[0].secretCount"), 1);
  assert.ok(sent.every(item => !["project-secrets/decrypt", "project-secrets/reveal"].includes(item.route)));
  panel = run("nativeSecretsPanel()");
  assert.match(panel, /API_TOKEN/);
  assert.match(panel, /Encrypted; value never returned/);
  assert.doesNotMatch(panel, /private-value-not-in-UI/);

  ctx.state.principal.role = "viewer";
  panel = run("nativeSecretsPanel()");
  assert.match(panel, /Only administrators can manage/);
  assert.doesNotMatch(panel, /API_TOKEN/);
  console.log("SOPS Secrets UI checks passed: write-only values, separate review/commit and admin-only metadata");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
