"use strict";
// Browser-local source edits must preserve untouched build contexts byte-for-byte.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto, createHash} = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../admin/static/incus-projects.js"), "utf8");
const controls = {value: ""};
const context = vm.createContext({
  Blob, TextDecoder, TextEncoder, Uint8Array, crypto: webcrypto,
  document: {getElementById: () => controls},
});
vm.runInContext(source, context);
context.state = {page: "projects"};
context.shell = () => {};
context.title = (a, b) => b;
context.card = (a, b) => a + b;
context.esc = s => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
context.operationView = {network: {available: true, guests: []}, networkError: ""};
context.api = () => {throw Error("Browser editor must not call any privileged API");};

const read = code => vm.runInContext(code, context);
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
function file(relativePath, bytes) {
  const blob = new Blob([bytes]);
  return {
    name: relativePath.split("/").at(-1), webkitRelativePath: relativePath,
    size: blob.size, arrayBuffer: () => blob.arrayBuffer(),
    slice: (start, end) => blob.slice(start, end),
  };
}
async function bytesFor(relativePath) {
  context.lookup = relativePath;
  const item = read("nativeSelectedFiles.find(f => nativeSourcePath(f) === lookup)");
  return Buffer.from(await item.arrayBuffer());
}

async function main() {
  const text = Buffer.from('services:\n  app:\n    image: "test<img>"\n');
  const binary = Buffer.from([0, 1, 255, 2, 3]);
  context.sources = [
    file("sample/compose.yaml", text),
    file("sample/build/payload.bin", binary),
    file("Dockerfile", Buffer.from("FROM scratch\n")),
  ];
  await read("nativePreviewFiles(sources)");
  assert.equal(read("nativeSourcePreflight.files.length"), 3);
  assert.equal(read("nativeSourcePreflight.error"), "");
  assert.equal(read("nativeSourcePreflight.files.find(f => f.path === 'sample/compose.yaml').sha256"), digest(text));
  assert.equal(read("nativeSelectedFiles.length"), 3);

  await read("nativeOpenSourceEditor('sample/build/payload.bin')");
  assert.equal(read("nativeSourceEditor.path"), "", "Non-UTF-8 binary must not enter editor");
  assert.match(read("nativeSourceEditor.error"), /utf-8|encoded|text/i);
  assert.deepEqual(await bytesFor("sample/build/payload.bin"), binary);

  await read("nativeOpenSourceEditor('sample/compose.yaml')");
  assert.equal(read("nativeSourceEditor.text"), text.toString());
  const html = read("nativeProjectPage()");
  assert.ok(html.includes('data-native-edit="sample/compose.yaml"'));
  assert.ok(html.includes('&lt;img&gt;'));
  assert.ok(!html.includes('<img>'), "Text content must remain escaped in the textarea");
  read("nativeCancelSourceEditor()");
  assert.deepEqual(await bytesFor("sample/compose.yaml"), text, "Cancel must leave bytes unchanged");

  await read("nativeOpenSourceEditor('sample/compose.yaml')");
  read("nativeImportState.result = {id:'old',project:'sample',manifestHash:'abc'}");
  controls.value = "services:\n  app:\n    build: .\n";
  await read("nativeSaveSourceEditor()");
  const edited = Buffer.from(controls.value);
  assert.deepEqual(await bytesFor("sample/compose.yaml"), edited);
  assert.notEqual(digest(text), read("nativeSourcePreflight.files.find(f => f.path === 'sample/compose.yaml').sha256"));
  assert.equal(read("nativeSourcePreflight.files.find(f => f.path === 'sample/compose.yaml').sha256"), digest(edited));
  assert.deepEqual(await bytesFor("sample/build/payload.bin"), binary, "Binary payload must retain exact bytes");
  assert.equal(read("nativeImportState.result"), null, "Any edit invalidates staged import selection");
  assert.equal(read("nativeSourceEditor.path"), "");
  assert.equal(read("nativeSourcePreflight.scanning"), false);

  // Keep path safeguards aligned with the broker, including nested secrets.
  for (const bad of [".env", ".env.production", ".ssh/id_ed25519", ".git/config",
    "build/private.key", "context\\invalid", "nested/../Dockerfile", "x//y",
    Array.from({length: 13}, (_, i) => "f" + i).join("/"), "files/名前"]) {
    context.bad = {name: bad};
    assert.throws(() => read("nativeSourcePath(bad)"), /source path|credential|metadata|unsafe/i, "Reject " + bad);
  }
  context.good = {name: "sample/.env.template"};
  assert.equal(read("nativeSourcePath(good)"), "sample/.env.template");

  // A slower stale selection must never overwrite the newest manifest.
  let release;
  const slow = file("slow.txt", Buffer.from("old"));
  slow.arrayBuffer = () => new Promise(resolve => {release = resolve;});
  context.slow = [slow];
  const pending = read("nativePreviewFiles(slow)");
  context.fast = [file("new.txt", Buffer.from("new"))];
  await read("nativePreviewFiles(fast)");
  release(Buffer.from("old"));
  await pending;
  assert.equal(read("nativeSourcePreflight.files[0].path"), "new.txt");
  assert.equal(read("nativeSourcePreflight.scanning"), false);
  console.log("Incus-native local source editor: opt-in edits, exact bytes, hashes, escaping and stale-selection checks passed");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
