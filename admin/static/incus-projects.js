"use strict";

// Phase-1 Incus-native project view. Local source previews do NOT upload,
// execute, or deploy files. All privileged operations require a later broker.
const nativeSourcePreflight = {files: [], error: "", scanning: false};
let nativeSelectedFiles = [];
let nativePreviewSerial = 0;
const nativeSourceEditor = {path: "", text: "", error: ""};
const NATIVE_MAX_EDIT_BYTES = 1024 * 1024;
const nativeImportState = {busy: false, result: null, error: ""};
const nativePersistedSource = {loaded: false, loading: false, value: null};
const NATIVE_MAX_FILES = 64;
const NATIVE_MAX_BYTES = 32 * 1024 * 1024;
const NATIVE_MAX_FILE_BYTES = 8 * 1024 * 1024;

function nativeSourcePath(file) {
  const name = file.webkitRelativePath || file.name;
  if (!name || name.length > 255 || name.startsWith("/") || name.includes("\\") ||
      name.split("/").some(part => !part || part === "." || part === ".." || /[\x00-\x1f]/.test(part))) {
    throw Error("Unsafe or ambiguous source path.");
  }
  const parts = name.split("/");
  if (parts.length > 12 || parts.some(part => !/^(?:[A-Za-z0-9][A-Za-z0-9_.-]*|\.[A-Za-z0-9][A-Za-z0-9._-]*)$/.test(part))) {
    throw Error("Unsupported source path; use simple relative names up to 12 levels deep.");
  }
  if (parts.some(part => {
    const lower = part.toLowerCase();
    return [".git", ".ssh", ".gnupg", ".nixlab", "node_modules", ".env",
      "id_rsa", "id_ed25519", "id_ecdsa", "projects.agekey"].includes(lower) ||
      (lower.startsWith(".env.") && ![".env.example", ".env.sample", ".env.template"].includes(lower)) ||
      [".agekey", ".p12", ".pfx", ".key"].some(ext => lower.endsWith(ext)) ||
      (lower.startsWith("secrets.") && !/^secrets\.enc\.(?:ya?ml|json)$/.test(lower));
  })) {
    throw Error("Potential credentials or unsafe metadata cannot be uploaded as source.");
  }
  return name;
}
function nativeGroupedGuests() {
  const source = operationView.network;
  if (!source?.available || !Array.isArray(source.guests)) return [];
  const byProject = new Map();
  for (const guest of source.guests) {
    const name = String(guest.project || "default");
    if (!byProject.has(name)) byProject.set(name, []);
    byProject.get(name).push(guest);
  }
  return [...byProject].sort(([a], [b]) => a.localeCompare(b));
}
function nativeProjectPage() {
  const network = operationView.network || {};
  const groups = nativeGroupedGuests();
  const message = operationView.networkError
    ? '<p class="error">' + esc(operationView.networkError) + '</p>'
    : !network.available
      ? '<p>Incus inventory is unavailable or not commissioned.</p>'
      : groups.length === 0
        ? '<p>No Incus guests observed. No project is auto-created.</p>'
        : '<p>Observed Incus namespaces are read-only; browsing never adopts a guest.</p>';
  const rows = groups.map(([project, guests]) => '<tr><td><code>' + esc(project) +
    '</code></td><td>' + esc(guests.length) + '</td><td>' +
    esc(guests.map(g => String(g.name) + ' (' + (g.status || 'Unknown') + ')').join(', ')) +
    '</td></tr>').join('');
  const persisted = nativePersistedSource.value;
  const workspace = !nativePersistedSource.loaded
    ? "<p>Loading reviewed source metadata…</p>"
    : !persisted?.available
      ? "<p>Declarative source unavailable (" + esc(persisted?.reason || "unknown") + "). No resources are changed.</p>"
      : "<p>Source SHA-256: <code>" + esc(persisted.sourceHash) + "</code> · " + esc(persisted.fileCount) + " files</p>" +
        (persisted.projects || []).map(item => "<div class=\"service-row\"><b>" + esc(item.name) + "</b><span>" + esc(item.moduleCount) + " modules</span></div>").join("");
  const upload = nativeImportState.result
    ? '<p role="status">Staged original source for <b>' + esc(nativeImportState.result.project) +
      '</b> as immutable import <code>' + esc(nativeImportState.result.id) +
      '</code> (manifest <code>' + esc(nativeImportState.result.manifestHash) +
      '</code>). No OCI build or Incus deployment has run.</p>'
    : '<p class="note">Only source upload is supported here; publishing to a project, execution and resource apply are not yet available.</p>';
  const manifest = nativeSourcePreflight.files.length
    ? '<div class="table-wrap"><table><thead><tr><th>Original path</th><th>Bytes</th><th>SHA-256</th><th>Optional edit</th></tr></thead><tbody>' +
      nativeSourcePreflight.files.map(f => '<tr><td><code>' + esc(f.path) + '</code></td><td>' +
        esc(f.size) + '</td><td><code>' + esc(f.sha256) + '</code></td><td>' +
        '<button type="button" data-native-edit="' + esc(f.path) + '"' +
        (nativeImportState.busy || nativeSourcePreflight.scanning || f.size > NATIVE_MAX_EDIT_BYTES ? ' disabled' : '') +
        '>Edit text</button></td></tr>').join("") +
      '</tbody></table></div><p class="note">Original file bytes are preserved unless you explicitly save a text edit. Files are local to this browser until staging.</p>'
    : '<p class="note">Selection stays in your browser. No file contents are sent to NixLab.</p>';
  const editor = nativeSourceEditor.path
    ? '<section><h3>Optional text edit: <code>' + esc(nativeSourceEditor.path) + '</code></h3>' +
      '<p class="note">Saving replaces only this browser copy, recalculates SHA-256, and clears any previous staged selection. It never edits a deployed file.</p>' +
      '<label class="field"><span>UTF-8 source</span><textarea id="native-source-editor" rows="14" spellcheck="false">' +
      esc(nativeSourceEditor.text) + '</textarea></label>' +
      '<button type="button" id="native-edit-save">Save local edit</button> ' +
      '<button type="button" id="native-edit-cancel">Discard edit</button></section>'
    : '';
  const editorError = nativeSourceEditor.error
    ? '<p role="alert" class="error">' + esc(nativeSourceEditor.error) + '</p>' : '';
  return title("INCUS WORKSPACE", "Projects", "Observe Incus resources and inspect source before a future reviewed import.") +
    card("Observed Incus projects",
      '<button type="button" id="native-project-refresh">Refresh inventory</button>' +
      message + (rows ? '<div class="table-wrap"><table><thead><tr><th>Project</th><th>Guests</th><th>Observed state</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
      '<p><a href="/docs/incus-native-architecture.md">Runtime and ownership contract</a></p>') +
    card("Published declarative source (read-only)", workspace) +
    card("Preview original application files locally",
      '<p>Preview Dockerfiles, Compose YAML and related build-context files without executing them. Relative paths and exact SHA-256 digests remain visible.</p>' +
      '<label class="field"><span>Choose files</span><input id="native-source-files" type="file" multiple' + (nativeImportState.busy ? ' disabled' : '') + '></label>' +
      '<label class="field"><span>Choose directory</span><input id="native-source-folder" type="file" webkitdirectory directory multiple' + (nativeImportState.busy ? ' disabled' : '') + '></label>' +
      (nativeSourcePreflight.scanning ? '<p role="status">Computing checksums in the browser…</p>' : '') +
      (nativeSourcePreflight.error ? '<p role="alert" class="error">' + esc(nativeSourcePreflight.error) + '</p>' : '') + manifest + editorError + editor) +
    card("Stage source in private review storage",
      '<p>After selecting files, reauthenticate and stage their exact bytes with an immutable SHA-256 manifest. This does not publish a Nix flake or deploy applications.</p>' +
      '<label class="field"><span>Project slug</span><input id="native-import-project" type="text" maxlength="63" placeholder="my-project" pattern="[a-z][a-z0-9-]*"></label>' +
      '<label class="field"><span>Current administrator password</span><input id="native-import-password" type="password" autocomplete="current-password"></label>' +
      '<button type="button" id="native-import-stage"' + (nativeSourcePreflight.files.length && !nativeImportState.busy ? '' : ' disabled') + '>Stage exact source bytes</button>' +
      (nativeImportState.busy ? '<p role="status">Transferring verified source chunks…</p>' : '') +
      (nativeImportState.error ? '<p role="alert" class="error">' + esc(nativeImportState.error) + '</p>' : '') + upload) +
    card("Declarative deployment",
      '<p>Source staging is available, but OCI building, secrets, reviewed publication, managed deployment, revisions and backups require the privileged deployment adapter and recovery tests.</p>' +
      '<p><a href="/docs/incus-native-plan.md">Implementation phases</a></p>');
}
async function nativePreviewFiles(files) {
  if (nativeImportState.busy) return;
  const serial = ++nativePreviewSerial;
  nativeSourceEditor.path = "";
  nativeSourceEditor.text = "";
  nativeSourceEditor.error = "";
  nativeSourcePreflight.files = [];
  nativeSelectedFiles = [];
  nativeImportState.result = null;
  nativeImportState.error = "";
  nativeSourcePreflight.error = "";
  nativeSourcePreflight.scanning = true;
  if (state.page === "projects") shell();
  try {
    const list = [...files];
    if (!list.length || list.length > NATIVE_MAX_FILES) throw Error("Choose between 1 and 64 files.");
    if (!globalThis.crypto?.subtle) throw Error("SHA-256 unavailable in this browser.");
    let total = 0;
    const paths = new Set();
    const items = [];
    for (const file of list) {
      const path = nativeSourcePath(file);
      if (paths.has(path)) throw Error("Duplicate source path: " + path);
      paths.add(path);
      if (file.size > NATIVE_MAX_FILE_BYTES) throw Error("Preview limit is 8 MiB per file.");
      total += file.size;
      if (total > NATIVE_MAX_BYTES) throw Error("Preview limit is 32 MiB per selection.");
      const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const sha256 = [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
      items.push({path, size: file.size, sha256});
    }
    if (serial !== nativePreviewSerial) return;
    nativeSourcePreflight.files = items.sort((a, b) => a.path.localeCompare(b.path));
    nativeSelectedFiles = list;
  } catch (error) {
    if (serial !== nativePreviewSerial) return;
    nativeSourcePreflight.error = error.message || "Source preflight failed.";
    nativeSourcePreflight.files = [];
    nativeSelectedFiles = [];
  } finally {
    if (serial === nativePreviewSerial) {
      nativeSourcePreflight.scanning = false;
      if (state.page === "projects") shell();
    }
  }
}

// Saving a text file is opt-in. Never re-encode an untouched binary context.
async function nativeOpenSourceEditor(path) {
  if (nativeImportState.busy || nativeSourcePreflight.scanning) return;
  const serial = nativePreviewSerial;
  nativeSourceEditor.error = "";
  nativeSourceEditor.path = "";
  nativeSourceEditor.text = "";
  try {
    const file = nativeSelectedFiles.find(item => nativeSourcePath(item) === path);
    if (!file) throw Error("Selected file is no longer available.");
    if (file.size > NATIVE_MAX_EDIT_BYTES) throw Error("Text editor limit is 1 MiB per file.");
    const text = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
    if (text.includes("\u0000")) throw Error("Binary files cannot be edited as text.");
    if (serial !== nativePreviewSerial || nativeImportState.busy) return;
    nativeSourceEditor.path = path;
    nativeSourceEditor.text = text;
  } catch (error) {
    if (serial !== nativePreviewSerial) return;
    nativeSourceEditor.error = error.message || "This file is not valid UTF-8 text.";
  }
  if (state.page === "projects") shell();
}

async function nativeSaveSourceEditor() {
  if (!nativeSourceEditor.path || nativeImportState.busy || nativeSourcePreflight.scanning) return;
  const path = nativeSourceEditor.path;
  const text = document.getElementById("native-source-editor")?.value;
  if (typeof text !== "string") return;
  try {
    const index = nativeSelectedFiles.findIndex(item => nativeSourcePath(item) === path);
    if (index < 0) throw Error("Source selection changed; reopen the editor.");
    const bytes = new TextEncoder().encode(text);
    if (bytes.length > NATIVE_MAX_FILE_BYTES) throw Error("Edited file exceeds 8 MiB.");
    const total = nativeSelectedFiles.reduce((sum, file) => sum + file.size, 0) -
      nativeSelectedFiles[index].size + bytes.length;
    if (total > NATIVE_MAX_BYTES) throw Error("Edited selection exceeds 32 MiB.");
    const old = nativeSelectedFiles[index];
    const blob = new Blob([bytes], {type: "text/plain"});
    // webkitRelativePath on a real File is read-only. Preserve it on the
    // Blob-backed browser copy so nested original paths remain unchanged.
    const replacement = {
      name: old.name, webkitRelativePath: path, size: blob.size,
      arrayBuffer: () => blob.arrayBuffer(),
      slice: (start, end) => blob.slice(start, end),
    };
    const next = nativeSelectedFiles.slice();
    next[index] = replacement;
    await nativePreviewFiles(next);
  } catch (error) {
    nativeSourceEditor.error = error.message || "Text edit was not saved.";
    nativeSourceEditor.text = text;
    if (state.page === "projects") shell();
  }
}

function nativeCancelSourceEditor() {
  nativeSourceEditor.path = "";
  nativeSourceEditor.text = "";
  nativeSourceEditor.error = "";
  if (state.page === "projects") shell();
}

async function nativeStageSource() {
  if (nativeImportState.busy || nativeSourcePreflight.scanning || !nativeSelectedFiles.length) return;
  const project = document.getElementById("native-import-project")?.value?.trim();
  const current = document.getElementById("native-import-password")?.value;
  if (document.getElementById("native-import-password")) document.getElementById("native-import-password").value = "";
  nativeImportState.busy = true;
  nativeImportState.error = "";
  nativeImportState.result = null;
  if (state.page === "projects") shell();
  try {
    if (!/^[a-z][a-z0-9-]{0,62}$/.test(project || "")) throw Error("Use a lowercase project slug.");
    if (!current) throw Error("Enter your administrator password.");
    const files = nativeSourcePreflight.files;
    const original = new Map(nativeSelectedFiles.map(file => [nativeSourcePath(file), file]));
    const candidate = await api("project-import/prepare", {project, files, current});
    for (const item of files) {
      const file = original.get(item.path);
      if (!file || file.size !== item.size) throw Error("Selected source changed before upload.");
      for (let offset = 0; offset < item.size; offset += 65536) {
        const bytes = new Uint8Array(await file.slice(offset, Math.min(offset + 65536, item.size)).arrayBuffer());
        let binary = "";
        for (let i = 0; i < bytes.length; i += 8192)
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        const result = await api("project-import/chunk",
          {id: candidate.id, path: item.path, offset, data: btoa(binary)});
        if (result.offset !== offset + bytes.length) throw Error("Broker chunk acknowledgment mismatch.");
      }
    }
    nativeImportState.result = await api("project-import/finish",
      {id: candidate.id, manifestHash: candidate.manifestHash});
  } catch (error) {
    nativeImportState.error = error.message || "Source staging failed without deployment.";
  } finally {
    nativeImportState.busy = false;
    if (state.page === "projects") shell();
  }
}
function bindNativeProjects() {
  if (!nativePersistedSource.loaded && !nativePersistedSource.loading) {
    nativePersistedSource.loading = true;
    void api("project-sources").then(value => {
      nativePersistedSource.value = value;
    }).catch(() => {
      nativePersistedSource.value = {available: false, reason: "read-failed", projects: []};
    }).finally(() => {
      nativePersistedSource.loaded = true;
      nativePersistedSource.loading = false;
      if (state.page === "projects") shell();
    });
  }
  document.querySelector("#native-import-stage")?.addEventListener("click", () => {
    void nativeStageSource();
  });
  for (const button of document.querySelectorAll("[data-native-edit]")) {
    button.addEventListener("click", () => { void nativeOpenSourceEditor(button.dataset.nativeEdit); });
  }
  document.querySelector("#native-edit-save")?.addEventListener("click", () => {
    void nativeSaveSourceEditor();
  });
  document.querySelector("#native-edit-cancel")?.addEventListener("click", nativeCancelSourceEditor);
  document.querySelector("#native-project-refresh")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      operationView.network = await api("guest-network");
      operationView.networkError = "";
    } catch {
      operationView.networkError = "Incus project inventory could not be refreshed.";
    }
    if (state.page === "projects") shell();
  });
  for (const id of ["native-source-files", "native-source-folder"]) {
    document.getElementById(id)?.addEventListener("change", event => {
      const files = [...(event.currentTarget.files || [])];
      void nativePreviewFiles(files);
    });
  }
}
