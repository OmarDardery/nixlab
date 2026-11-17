"use strict";

// Phase-1 Incus-native project view. Local source previews do NOT upload,
// execute, or deploy files. All privileged operations require a later broker.
const nativeSourcePreflight = {files: [], error: "", scanning: false};
let nativeSelectedFiles = [];
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
  if (name.split("/").some(part => /^\.env(?:\.|$)/.test(part) && !/\.env\.(?:example|sample|template)$/.test(part))) {
    throw Error("Do not import plaintext .env credentials. Use encrypted project secrets.");
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
    ? '<div class="table-wrap"><table><thead><tr><th>Original path</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>' +
      nativeSourcePreflight.files.map(f => '<tr><td><code>' + esc(f.path) + '</code></td><td>' +
        esc(f.size) + '</td><td><code>' + esc(f.sha256) + '</code></td></tr>').join('') +
      '</tbody></table></div><p class="note">Selected original bytes are only in this browser until an administrator explicitly stages them.</p>'
    : '<p class="note">Selection stays in your browser. No file contents are sent to NixLab.</p>';
  return title("INCUS WORKSPACE", "Projects", "Observe Incus resources and inspect source before a future reviewed import.") +
    card("Observed Incus projects",
      '<button type="button" id="native-project-refresh">Refresh inventory</button>' +
      message + (rows ? '<div class="table-wrap"><table><thead><tr><th>Project</th><th>Guests</th><th>Observed state</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
      '<p><a href="/docs/incus-native-architecture.md">Runtime and ownership contract</a></p>') +
    card("Published declarative source (read-only)", workspace) +
    card("Preview original application files locally",
      '<p>Preview Dockerfiles, Compose YAML and related build-context files without executing them. Relative paths and exact SHA-256 digests remain visible.</p>' +
      '<label class="field"><span>Choose files</span><input id="native-source-files" type="file" multiple></label>' +
      '<label class="field"><span>Choose directory</span><input id="native-source-folder" type="file" webkitdirectory directory multiple></label>' +
      (nativeSourcePreflight.scanning ? '<p role="status">Computing checksums in the browser…</p>' : '') +
      (nativeSourcePreflight.error ? '<p role="alert" class="error">' + esc(nativeSourcePreflight.error) + '</p>' : '') + manifest) +
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
    nativeSourcePreflight.files = items.sort((a, b) => a.path.localeCompare(b.path));
    nativeSelectedFiles = list;
  } catch (error) {
    nativeSourcePreflight.error = error.message || "Source preflight failed.";
    nativeSourcePreflight.files = [];
    nativeSelectedFiles = [];
  } finally {
    nativeSourcePreflight.scanning = false;
    if (state.page === "projects") shell();
  }
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
