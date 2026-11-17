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
};
const calls = [];
const context = vm.createContext({
  document: {getElementById: id => fields[id] || null},
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
  console.log("Native source publication UI checks passed: review, reauth, explicit acknowledgment, inert source");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
