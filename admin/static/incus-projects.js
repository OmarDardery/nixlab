"use strict";

// Phase-1 Incus-native project view. Local source previews do NOT upload,
// execute, or deploy files. All privileged operations require a later broker.
const nativeSourcePreflight = {files: [], error: "", scanning: false, archive: false};
let nativeSelectedFiles = [];
let nativePreviewSerial = 0;
const nativeSourceEditor = {path: "", text: "", error: ""};
const NATIVE_MAX_EDIT_BYTES = 1024 * 1024;
const nativeImportState = {busy: false, result: null, review: null, published: null, error: ""};
const nativePublishedInventory = {loaded: false, loading: false, projects: [], error: ""};
const nativePersistedSource = {loaded: false, loading: false, value: null};
const nativeProjectTx = {loaded: false, loading: false, busy: false, record: null, error: "",
                         path: "", source: "", linkedSource: "", linkedRevision: ""};
const nativeWorkspaceInit = {busy: false, error: "", initialized: false};
const NATIVE_MAX_FILES = 256;
const NATIVE_MAX_BYTES = 128 * 1024 * 1024;
const NATIVE_MAX_FILE_BYTES = 64 * 1024 * 1024;
const NATIVE_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;

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
    : '<p class="note">Select files and reauthenticate to stage a private import without running code.</p>';
  const published = nativePublishedInventory.loading
    ? '<p>Loading published source revisions…</p>'
    : nativePublishedInventory.error
      ? '<p class="error">' + esc(nativePublishedInventory.error) + '</p>'
      : nativePublishedInventory.projects.length
        ? nativePublishedInventory.projects.map(item =>
            '<div class="service-row"><b>' + esc(item.project) + '</b><code>' +
            esc(item.revision) + '</code></div>').join('')
        : '<p>No project source revisions published yet.</p>';
  const review = nativeImportState.review;
  const diff = review?.changes || {added: [], modified: [], removed: []};
  const changes = ['added', 'modified', 'removed'].map(kind =>
    '<p><b>' + esc(kind) + ' (' + diff[kind].length + ')</b>: ' +
    esc(diff[kind].join(', ') || 'none') + '</p>').join('');
  const reviewPanel = nativeImportState.published
    ? '<p role="status">Published inert source revision <code>' +
      esc(nativeImportState.published.revision) +
      '</code>. Existing revisions are preserved. No build or Incus change occurred.</p>'
    : review
      ? '<p>Review <code>' + esc(review.reviewId) + '</code> · ' + esc(review.fileCount) +
        ' files · ' + esc(review.bytes) + ' bytes</p>' +
        '<p>Previous: <code>' + esc(review.baseRevision || 'none') +
        '</code> · Proposed: <code>' + esc(review.revision) + '</code></p>' + changes +
        '<p class="note">This is a source-only publication. It does not create an Incus project, evaluate Nix, build an image or deploy anything.</p>' +
        '<label class="field"><span>Current administrator password</span><input id="native-publish-password" type="password" autocomplete="current-password"></label>' +
        '<label><input id="native-publish-ack" type="checkbox"> Publish this exact reviewed source revision</label>' +
        '<button type="button" id="native-publish-source"' + (nativeImportState.busy ? ' disabled' : '') +
        '>Publish source only</button>'
      : nativeImportState.result
        ? '<button type="button" id="native-review-source"' + (nativeImportState.busy ? ' disabled' : '') +
          '>Review source publication</button>'
        : '<p class="note">Stage files first, then review the immutable revision before publication.</p>';
  const reviewedFiles = nativeImportState.result?.entries || nativeSourcePreflight.files;
  const manifest = reviewedFiles.length
    ? '<div class="table-wrap"><table><thead><tr><th>Original path</th><th>Bytes</th><th>SHA-256</th><th>Optional edit</th></tr></thead><tbody>' +
      reviewedFiles.map(f => '<tr><td><code>' + esc(f.path) + '</code></td><td>' +
        esc(f.size) + '</td><td><code>' + esc(f.sha256) + '</code></td><td>' +
        '<button type="button" data-native-edit="' + esc(f.path) + '"' +
        (nativeImportState.busy || nativeImportState.result || nativeSourcePreflight.scanning || nativeSourcePreflight.archive || f.size > NATIVE_MAX_EDIT_BYTES ? ' disabled' : '') +
        '>Edit text</button></td></tr>').join("") +
      '</tbody></table></div><p class="note">' +
      (nativeImportState.result?.expandedFromArchive
        ? 'Archive entries were verified by the root broker. Source files are now frozen for reviewed publication.'
        : nativeSourcePreflight.archive
        ? 'Archive members are validated and shown with per-file SHA-256 hashes after private server-side extraction; archive entries cannot be edited here.'
        : 'Original file bytes are preserved unless you explicitly save a text edit. Files are local to this browser until staging.') +
      '</p>'
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
  const transaction = nativeProjectTx.record || {};
  const isReviewed = transaction.status === "ready";
  const isPublished = transaction.status === "published-source";
  const hasTransaction = !!transaction.id;
  const scope = (transaction.runtimeActions || []).map(item =>
    '<p><code>' + esc(item.project + '/' + item.name) + '</code>: ' +
    esc(item.action) + ' · ' + esc(JSON.stringify(item.before)) + ' → ' +
    esc(JSON.stringify(item.after)) + (item.potentialDisruption ? ' (possible interruption)' : '') +
    '</p>').join("");
  const initPanel = nativePersistedSource.loaded && !persisted?.available
    ? '<p>Restore a pinned flake.nix and flake.lock from your Git backup to initialize this machine. Their bytes are verified as private NixLab workspace source; no uploaded Nix code is evaluated during initialization.</p>' +
      '<label class="field"><span>Restored flake.nix</span><textarea id="native-workspace-flake" rows="6" spellcheck="false" placeholder="Paste exact backed-up flake.nix"></textarea></label>' +
      '<label class="field"><span>Restored flake.lock (JSON)</span><textarea id="native-workspace-lock" rows="5" spellcheck="false" placeholder="Paste exact pinned flake.lock"></textarea></label>' +
      '<label class="field"><span>Current administrator password</span><input type="password" id="native-workspace-password" autocomplete="current-password"></label>' +
      '<label><input type="checkbox" id="native-workspace-ack"> Initialize the root-owned workspace from these pinned source files</label>' +
      '<button type="button" id="native-workspace-init"' + (nativeWorkspaceInit.busy ? ' disabled' : '') + '>Initialize project workspace</button>' +
      (nativeWorkspaceInit.error ? '<p role="alert" class="error">' + esc(nativeWorkspaceInit.error) + '</p>' : '')
    : '';
  const txPanel =
    '<p>Review a full Nix module as an independent project revision. Source changes never rebuild NixOS. Deployment only supports already-owned native Incus guest CPU/memory limits until full reconciliation is implemented.</p>' +
    '<label class="field"><span>Relative workspace Nix file (for example: alpha/default.nix)</span><input id="native-nix-path" type="text" spellcheck="false" value="' +
    esc(nativeProjectTx.path) + '" placeholder="alpha/default.nix"></label>' +
    '<label class="field"><span>Complete Nix file content</span><textarea id="native-nix-editor" rows="9" spellcheck="false">' +
    esc(nativeProjectTx.source) + '</textarea></label>' +
    '<label class="field"><span>Linked published source project (optional)</span><input id="native-nix-linked-source" type="text" placeholder="app-name" value="' +
    esc(nativeProjectTx.linkedSource) + '"></label>' +
    '<label class="field"><span>Exact published source revision SHA-256 (optional; original bytes stay private)</span><input id="native-nix-linked-revision" type="text" maxlength="64" value="' +
    esc(nativeProjectTx.linkedRevision) + '"></label>' +
    '<label class="field"><span>Current administrator password (required separately for every operation)</span><input id="native-nix-password" type="password" autocomplete="current-password"></label>' +
    '<label><input id="native-nix-ack" type="checkbox"> I approve the reviewed privileged project Nix change and possible service impact</label>' +
    '<button type="button" id="native-nix-review"' + (nativeProjectTx.busy ? ' disabled' : '') + '>Review exact project revision</button> ' +
    '<button type="button" id="native-nix-refresh">Refresh transaction</button>' +
    (nativeProjectTx.error ? '<p role="alert" class="error">' + esc(nativeProjectTx.error) + '</p>' : '') +
    (hasTransaction ? '<section aria-live="polite"><h3>Transaction: ' + esc(transaction.status) + '</h3>' +
      '<p>ID <code>' + esc(transaction.id) + '</code> · SHA-256 candidate <code>' + esc(transaction.candidateHash) +
      '</code> · plan <code>' + esc(transaction.planHash) + '</code></p>' +
      '<p>Source only: ' + (transaction.sourceOnly ? 'yes' : 'no') +
      ' · Deployment: ' + esc(transaction.deployment || 'not-started') + '</p>' +
      (transaction.changes || []).map(item =>
        '<p>' + esc(item.operation) + ': <code>' + esc(item.path) + '</code></p>').join("") + scope +
      (isReviewed ? '<button type="button" data-native-nix-action="publish">Publish reviewed source</button> ' +
        '<button type="button" data-native-nix-action="cancel">Cancel review</button>' : '') +
      (isPublished ? '<button type="button" data-native-nix-action="deploy">Deploy exact reviewed revision</button> ' +
        '<button type="button" data-native-nix-action="rollback">Rollback source only</button>' : '') +
      (transaction.recoveryRequired ? '<button type="button" data-native-nix-action="recover">Recover interrupted source</button> ' +
        '<button type="button" data-native-nix-action="resume">Resume interrupted deployment</button>' : '') +
      '</section>' : '<p>No active reviewed project revision.</p>');
  return title("INCUS WORKSPACE", "Projects", "Manage project source and reviewed native Incus revisions.") +
    card("Observed Incus projects",
      '<button type="button" id="native-project-refresh">Refresh inventory</button>' +
      message + (rows ? '<div class="table-wrap"><table><thead><tr><th>Project</th><th>Guests</th><th>Observed state</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
      '<p><a href="/docs/incus-native-architecture.md">Runtime and ownership contract</a></p>') +
    card("Published declarative source (read-only)", workspace) +
    (initPanel ? card("Initialize or restore project workspace", initPanel) : '') +
    (persisted?.available ? card("Review, publish and deploy declarative Nix", txPanel) : '') +
    card("Published application source revisions (not deployed)", published) +
    card("Preview original application files locally",
      '<p>Preview Dockerfiles, Compose YAML and related build-context files without executing them. Relative paths and exact SHA-256 digests remain visible.</p>' +
      '<label class="field"><span>Choose files</span><input id="native-source-files" type="file" multiple' + (nativeImportState.busy ? ' disabled' : '') + '></label>' +
      '<label class="field"><span>Choose directory</span><input id="native-source-folder" type="file" webkitdirectory directory multiple' + (nativeImportState.busy ? ' disabled' : '') + '></label>' +
      '<label class="field"><span>Or import ZIP/TAR archive</span><input id="native-source-archive" type="file" accept=".zip,.tar,.tar.gz,.tgz"' + (nativeImportState.busy ? ' disabled' : '') + '></label>' +
      (nativeSourcePreflight.scanning ? '<p role="status">Computing checksums in the browser…</p>' : '') +
      (nativeSourcePreflight.error ? '<p role="alert" class="error">' + esc(nativeSourcePreflight.error) + '</p>' : '') + manifest + editorError + editor) +
    card("Stage source in private review storage",
      '<p>After selecting files, reauthenticate and stage their exact bytes with an immutable SHA-256 manifest. This does not publish a Nix flake or deploy applications.</p>' +
      '<label class="field"><span>Project slug</span><input id="native-import-project" type="text" maxlength="63" placeholder="my-project" pattern="[a-z][a-z0-9-]*"></label>' +
      '<label class="field"><span>Current administrator password</span><input id="native-import-password" type="password" autocomplete="current-password"></label>' +
      '<button type="button" id="native-import-stage"' + (nativeSourcePreflight.files.length && !nativeImportState.busy ? '' : ' disabled') + '>Stage exact source bytes</button>' +
      (nativeImportState.busy ? '<p role="status">Transferring verified source chunks…</p>' : '') +
      (nativeImportState.error ? '<p role="alert" class="error">' + esc(nativeImportState.error) + '</p>' : '') + upload) +
    card("Review and publish staged source", reviewPanel) +
    card("OCI / Compose deployment",
      '<p>Original Dockerfile/Compose source publication is not application deployment. OCI building, scoped secrets, new Incus resource creation and data restore require #133/#139/#134 and verified recovery tests.</p>' +
      '<p><a href="/docs/incus-native-plan.md">Implementation phases</a></p>');
}
async function nativePreviewFiles(files, archive = false) {
  if (nativeImportState.busy) return;
  const serial = ++nativePreviewSerial;
  nativeSourceEditor.path = "";
  nativeSourceEditor.text = "";
  nativeSourceEditor.error = "";
  nativeSourcePreflight.files = [];
  nativeSourcePreflight.archive = false;
  nativeSelectedFiles = [];
  nativeImportState.result = null;
  nativeImportState.review = null;
  nativeImportState.published = null;
  nativeImportState.error = "";
  nativeSourcePreflight.error = "";
  nativeSourcePreflight.scanning = true;
  if (state.page === "projects") shell();
  try {
    const list = [...files];
    if (!list.length || list.length > NATIVE_MAX_FILES || (archive && list.length !== 1))
      throw Error(archive ? "Choose exactly one archive." : "Choose 1–256 source files.");
    if (archive && !/\.(?:zip|tar|tar\.gz|tgz)$/i.test(list[0].name))
      throw Error("Choose a ZIP, TAR, TAR.GZ or TGZ archive.");
    if (!globalThis.crypto?.subtle) throw Error("SHA-256 unavailable in this browser.");
    let total = 0;
    const paths = new Set();
    const items = [];
    for (const file of list) {
      const path = nativeSourcePath(file);
      if (paths.has(path)) throw Error("Duplicate source path: " + path);
      paths.add(path);
      if (file.size > (archive ? NATIVE_MAX_ARCHIVE_BYTES : NATIVE_MAX_FILE_BYTES))
        throw Error("Source file exceeds the 64 MiB upload limit.");
      total += file.size;
      if (total > NATIVE_MAX_BYTES) throw Error("Source selection exceeds 128 MiB.");
      const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const sha256 = [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
      items.push({path, size: file.size, sha256});
    }
    if (serial !== nativePreviewSerial) return;
    nativeSourcePreflight.files = items.sort((a, b) => a.path.localeCompare(b.path));
    nativeSourcePreflight.archive = archive;
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
  nativeImportState.review = null;
  nativeImportState.published = null;
  if (state.page === "projects") shell();
  try {
    if (!/^[a-z][a-z0-9-]{0,62}$/.test(project || "")) throw Error("Use a lowercase project slug.");
    if (!current) throw Error("Enter your administrator password.");
    const files = nativeSourcePreflight.files;
    const original = new Map(nativeSelectedFiles.map(file => [nativeSourcePath(file), file]));
    const candidate = await api("project-import/prepare", {
      project, files, archive: nativeSourcePreflight.archive, current
    });
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
async function nativeReviewSource() {
  if (nativeImportState.busy || !nativeImportState.result || nativeImportState.published) return;
  nativeImportState.busy = true;
  nativeImportState.error = "";
  nativeImportState.review = null;
  if (state.page === "projects") shell();
  try {
    nativeImportState.review = await api("project-import/review", {
      id: nativeImportState.result.id, manifestHash: nativeImportState.result.manifestHash,
    });
  } catch (error) {
    nativeImportState.error = error.message || "Could not prepare source publication review.";
  } finally {
    nativeImportState.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativePublishSource() {
  const review = nativeImportState.review;
  const staged = nativeImportState.result;
  if (nativeImportState.busy || !review || !staged || nativeImportState.published) return;
  const password = document.getElementById("native-publish-password");
  const current = password?.value;
  if (password) password.value = "";
  const acknowledge = document.getElementById("native-publish-ack")?.checked === true;
  nativeImportState.busy = true;
  nativeImportState.error = "";
  if (state.page === "projects") shell();
  try {
    if (!acknowledge) throw Error("Acknowledge the exact source revision before publishing.");
    if (!current) throw Error("Reauthenticate with your current administrator password.");
    nativeImportState.published = await api("project-import/publish", {
      id: staged.id, reviewId: review.reviewId, acknowledge, current,
    });
    nativeImportState.review = null;
    nativePublishedInventory.loaded = false;
  } catch (error) {
    nativeImportState.error = error.message || "Publication rejected; the previous revision is unchanged.";
    // Force a new review on failure (including concurrent revision drift).
    nativeImportState.review = null;
  } finally {
    nativeImportState.busy = false;
    if (state.page === "projects") shell();
  }
}


async function nativeInitializeWorkspace() {
  if (nativeWorkspaceInit.busy) return;
  const flake = document.querySelector("#native-workspace-flake")?.value || "";
  const lock = document.querySelector("#native-workspace-lock")?.value || "";
  const current = document.querySelector("#native-workspace-password")?.value || "";
  const acknowledgePrivilegedNix = document.querySelector("#native-workspace-ack")?.checked === true;
  if (!flake || !lock || !current || !acknowledgePrivilegedNix ||
      new TextEncoder().encode(flake).length > 32768 ||
      new TextEncoder().encode(lock).length > 131072) {
    nativeWorkspaceInit.error = "Supply bounded pinned flake and lock files, your current password, and explicit approval.";
    if (state.page === "projects") shell();
    return;
  }
  nativeWorkspaceInit.busy = true;
  nativeWorkspaceInit.error = "";
  try {
    await api("project/initialize", {flake, lock, current, acknowledgePrivilegedNix});
    nativePersistedSource.loaded = false;
    nativePersistedSource.value = null;
    nativeWorkspaceInit.initialized = true;
  } catch (error) {
    nativeWorkspaceInit.error = error.message || "Workspace initialization was refused.";
  } finally {
    nativeWorkspaceInit.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeRefreshProjectTransaction() {
  if (nativeProjectTx.loading || nativeProjectTx.busy) return;
  nativeProjectTx.loading = true;
  try {
    const response = await api("project/status");
    nativeProjectTx.record = response?.id ? response : null;
    nativeProjectTx.error = "";
  } catch {
    nativeProjectTx.error = "Project transaction status is unavailable.";
  } finally {
    nativeProjectTx.loaded = true;
    nativeProjectTx.loading = false;
    if (state.page === "projects") shell();
  }
}

async function nativeProjectTransaction(action) {
  if (nativeProjectTx.busy) return;
  const password = document.querySelector("#native-nix-password")?.value || "";
  const acknowledged = document.querySelector("#native-nix-ack")?.checked === true;
  const path = document.querySelector("#native-nix-path")?.value?.trim() || "";
  const source = document.querySelector("#native-nix-editor")?.value ?? "";
  const linkedSource = document.querySelector("#native-nix-linked-source")?.value?.trim() || "";
  const linkedRevision = document.querySelector("#native-nix-linked-revision")?.value?.trim() || "";
  nativeProjectTx.path = path;
  nativeProjectTx.source = source;
  nativeProjectTx.linkedSource = linkedSource;
  nativeProjectTx.linkedRevision = linkedRevision;
  if (!password || !acknowledged) {
    nativeProjectTx.error = "Current administrator password and explicit approval are required.";
    if (state.page === "projects") shell();
    return;
  }
  if (action === "review" &&
      ((linkedSource && !/^[a-z][a-z0-9-]{0,62}$/.test(linkedSource)) ||
       (linkedRevision && !/^[0-9a-f]{64}$/.test(linkedRevision)) ||
       (!!linkedSource !== !!linkedRevision))) {
    nativeProjectTx.error = "Select a valid published source project and its complete revision SHA-256 together.";
    if (state.page === "projects") shell();
    return;
  }
  if (action === "review" && (!path || !source || new TextEncoder().encode(source).length > 65536)) {
    nativeProjectTx.error = "Choose a relative Nix module path with no more than 64 KiB of text.";
    if (state.page === "projects") shell();
    return;
  }
  nativeProjectTx.busy = true;
  nativeProjectTx.error = "";
  try {
    const record = nativeProjectTx.record;
    const request = action === "review" ? {
      changes: {[path]: source},
      sourceRevisions: linkedSource ? {[linkedSource]: linkedRevision} : {}
    } : {
      id: record?.id, planHash: record?.planHash
    };
    const endpoint = action === "resume" ? "deploy" : action;
    if (action === "resume") request.resume = true;
    nativeProjectTx.record = await api("project/" + endpoint, {
      ...request, current: password, acknowledgePrivilegedNix: true
    });
  } catch (error) {
    nativeProjectTx.error = error.message || "The project operation was not committed.";
  } finally {
    nativeProjectTx.loaded = true;
    nativeProjectTx.busy = false;
    if (state.page === "projects") shell();
  }
}

function bindNativeProjects() {
  document.querySelector("#native-workspace-init")?.addEventListener("click", () => void nativeInitializeWorkspace());
  if (!nativeProjectTx.loaded && !nativeProjectTx.loading) void nativeRefreshProjectTransaction();
  document.querySelector("#native-nix-review")?.addEventListener("click", () => void nativeProjectTransaction("review"));
  document.querySelector("#native-nix-refresh")?.addEventListener("click", () => void nativeRefreshProjectTransaction());
  for (const button of document.querySelectorAll("[data-native-nix-action]")) {
    button.addEventListener("click", () => void nativeProjectTransaction(button.dataset.nativeNixAction));
  }
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
  if (!nativePublishedInventory.loaded && !nativePublishedInventory.loading) {
    nativePublishedInventory.loading = true;
    void api("project-imports").then(value => {
      nativePublishedInventory.projects = Array.isArray(value?.projects) ? value.projects : [];
      nativePublishedInventory.error = "";
    }).catch(() => {
      nativePublishedInventory.error = "Published source inventory is unavailable.";
    }).finally(() => {
      nativePublishedInventory.loading = false;
      nativePublishedInventory.loaded = true;
      if (state.page === "projects") shell();
    });
  }
  document.querySelector("#native-import-stage")?.addEventListener("click", () => {
    void nativeStageSource();
  });
  document.querySelector("#native-review-source")?.addEventListener("click", () => {
    void nativeReviewSource();
  });
  document.querySelector("#native-publish-source")?.addEventListener("click", () => {
    void nativePublishSource();
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
  for (const id of ["native-source-files", "native-source-folder", "native-source-archive"]) {
    document.getElementById(id)?.addEventListener("change", event => {
      const files = [...(event.currentTarget.files || [])];
      void nativePreviewFiles(files, id === "native-source-archive");
    });
  }
}
