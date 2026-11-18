"use strict";
// Authenticated review and publication must be separate UI actions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const code = fs.readFileSync(path.join(__dirname, "../admin/static/incus-projects.js"), "utf8");
const fields = {
  "native-publish-password": {value: "current-admin-password"},
  "native-publish-ack": {checked: false},
  "native-nix-path": {value: "alpha/default.nix"},
  "native-nix-editor": {value: "{ ... }: { nixlab.project.resources.cpus = 3; }"},
  "native-nix-password": {value: "admin-password"},
  "native-nix-ack": {checked: false},
  "native-nix-linked-source": {value: "alpha"},
  "native-nix-linked-revision": {value: "f".repeat(64)},
  "native-workspace-flake": {value: "{ outputs = { self }: {}; }"},
  "native-workspace-lock": {value: '{"version":7,"root":"root","nodes":{"root":{}}}'},
  "native-workspace-password": {value: "admin-password"},
  "native-workspace-ack": {checked: false},
};
const calls = [];
const context = vm.createContext({
  document: {getElementById: id => fields[id] || null, querySelector: selector => fields[selector.slice(1)] || null},
  TextEncoder,
  console,
});
vm.runInContext(code, context);
context.state = {page: "projects"};
context.operationView = {network: {available: true, guests: []}};
context.shell = () => {};
context.title = (tag, name) => name;
context.card = (name, body) => name + body;
context.esc = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
context.api = async (route, payload) => {
  calls.push({route, payload});
  if (route === "project-import/review") {
    return {reviewId: "c".repeat(32), project: "hello-world",
      revision: "b".repeat(64), baseRevision: null, fileCount: 1, bytes: 12,
      changes: {added: ["Dockerfile"], modified: [], removed: []}};
  }
  if (route === "project-import/publish") {
    return {project: "hello-world", status: "published", revision: "b".repeat(64),
      previous: null, noChange: false};
  }
  throw Error("Unexpected API operation: " + route);
};
const evaluate = s => vm.runInContext(s, context);

async function main() {
  context.staged = {id: "a".repeat(32), manifestHash: "b".repeat(64),
    project: "hello-world", status: "ready"};
  evaluate("nativeImportState.result = staged");
  let html = evaluate("nativeProjectPage()");
  assert.match(html, /Review source publication/);
  assert.doesNotMatch(html, /id="native-publish-source"/);

  await evaluate("nativeReviewSource()");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].route, "project-import/review");
  assert.equal(calls[0].payload.id, context.staged.id);
  assert.equal(calls[0].payload.manifestHash, context.staged.manifestHash);
  html = evaluate("nativeProjectPage()");
  assert.match(html, /Current administrator password/);
  assert.match(html, /Publish this exact reviewed source revision/);
  assert.match(html, /No project source revisions published yet/);
  assert.match(html, /This is a source-only publication/);

  // Cannot publish without an explicit acknowledgment even with a review.
  await evaluate("nativePublishSource()");
  assert.equal(calls.length, 1);
  assert.match(evaluate("nativeImportState.error"), /Acknowledge/);
  assert.equal(fields["native-publish-password"].value, "", "Password cleared immediately");
  assert.equal(evaluate("nativeImportState.review"), null);
  await evaluate("nativeReviewSource()");
  fields["native-publish-password"].value = "current-admin-password";
  fields["native-publish-ack"].checked = true;
  await evaluate("nativePublishSource()");
  assert.equal(calls.at(-1).route, "project-import/publish");
  assert.equal(calls.at(-1).payload.current, "current-admin-password");
  assert.equal(calls.at(-1).payload.acknowledge, true);
  assert.equal(calls.at(-1).payload.reviewId, "c".repeat(32));
  assert.equal(fields["native-publish-password"].value, "");
  assert.equal(evaluate("nativeImportState.published.status"), "published");
  assert.equal(evaluate("nativeImportState.review"), null);
  assert.equal(evaluate("nativePublishedInventory.loaded"), false);
  html = evaluate("nativeProjectPage()");
  assert.match(html, /Published inert source revision/);
  assert.match(html, /No build or Incus change occurred/);
  assert.ok(!calls.some(call => /deploy|build|incus/.test(call.route)),
    "Source-only actions must not call deployment or an OCI builder");

  // Independent Nix changes use the same authenticated API, but a DIFFERENT
  // transaction than raw Dockerfile/Compose source publication.
  context.api = async (route, payload) => {
    calls.push({route, payload});
    if (route === "project/status") return {
      id: "d".repeat(32), planHash: "e".repeat(64), status: "ready", changes: [],
      candidateHash: "f".repeat(64), runtimeActions: []
    };
    if (route === "project/review") return {
      id: "d".repeat(32), planHash: "e".repeat(64), status: "ready", changes: [
        {path: "alpha/default.nix", operation: "update"}
      ], candidateHash: "f".repeat(64), runtimeActions: []
    };
    if (route === "project/publish") return {
      id: payload.id, planHash: payload.planHash, status: "published-source",
      changes: [], candidateHash: "f".repeat(64), sourceOnly: true, runtimeActions: []
    };
    if (route === "project/deploy") return {
      id: payload.id, planHash: payload.planHash, status: "published-source",
      deployment: "queued", changes: [], candidateHash: "f".repeat(64),
      sourceOnly: true, runtimeActions: []
    };
    throw Error("Unexpected project transaction request: " + route);
  };
  await evaluate('nativeProjectTransaction("review")');
  assert.equal(calls.filter(x => x.route === "project/review").length, 0,
    "Review must require explicit acknowledgment");
  fields["native-nix-ack"].checked = true;
  fields["native-nix-password"].value = "admin-password";
  await evaluate('nativeProjectTransaction("review")');
  const reviewed = calls.at(-1);
  assert.equal(reviewed.route, "project/review");
  assert.equal(reviewed.payload.current, "admin-password");
  assert.equal(reviewed.payload.acknowledgePrivilegedNix, true);
  assert.equal(reviewed.payload.changes["alpha/default.nix"],
    "{ ... }: { nixlab.project.resources.cpus = 3; }");
  assert.equal(reviewed.payload.sourceRevisions.alpha, "f".repeat(64),
    "Exact private OCI revision digest must enter the approved project review");
  html = evaluate("nativeProjectPage()");
  assert.match(html, /Publish reviewed source/);
  fields["native-nix-password"].value = "admin-password";
  await evaluate('nativeProjectTransaction("publish")');
  assert.equal(calls.at(-1).route, "project/publish");
  assert.equal(calls.at(-1).payload.planHash, "e".repeat(64));
  fields["native-nix-password"].value = "admin-password";
  await evaluate('nativeProjectTransaction("deploy")');
  assert.equal(calls.at(-1).route, "project/deploy");
  assert.equal(calls.at(-1).payload.id, "d".repeat(32));
  await evaluate("nativeRefreshProjectTransaction()");
  assert.equal(evaluate("nativeProjectTx.record.status"), "ready",
    "Refresh must keep the active transaction instead of discarding it");


  // First-run initialization is a separate, reauthenticated operation; no
  // project Nix or uploaded Dockerfile executes when staging/restoring source.
  evaluate("nativePersistedSource.loaded = true; nativePersistedSource.value = {available:false,reason:'not-configured'}");
  assert.match(evaluate("nativeProjectPage()"), /Initialize project workspace/);
  await evaluate("nativeInitializeWorkspace()");
  assert.equal(calls.some(call => call.route === "project/initialize"), false,
    "No workspace mutation without explicit approval");
  fields["native-workspace-ack"].checked = true;
  context.api = async (route, payload) => {
    calls.push({route, payload});
    if (route === "project/initialize") return {available: true, sourceHash: "a".repeat(64)};
    throw Error("Unexpected endpoint: " + route);
  };
  await evaluate("nativeInitializeWorkspace()");
  const initialized = calls.at(-1);
  assert.equal(initialized.route, "project/initialize");
  assert.equal(initialized.payload.current, "admin-password");
  assert.equal(initialized.payload.acknowledgePrivilegedNix, true);
  assert.equal(evaluate("nativePersistedSource.loaded"), false,
    "Successful initialization must refresh the persisted inventory");
    console.log("Native source and Nix transaction UI: explicit review, reauth, source/data separation, publish/deploy and refresh passed");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
