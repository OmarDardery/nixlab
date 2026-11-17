"use strict";

// Phase-1 Incus-native project view. Local source previews do NOT upload,
// execute, or deploy files. All privileged operations require a later broker.
const nativeSourcePreflight = {files: [], error: "", scanning: false};
const NATIVE_MAX_FILES = 64;
const NATIVE_MAX_BYTES = 16 * 1024 * 1024;
const NATIVE_MAX_FILE_BYTES = 4 * 1024 * 1024;

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
  const manifest = nativeSourcePreflight.files.length
    ? '<div class="table-wrap"><table><thead><tr><th>Original path</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>' +
      nativeSourcePreflight.files.map(f => '<tr><td><code>' + esc(f.path) + '</code></td><td>' +
        esc(f.size) + '</td><td><code>' + esc(f.sha256) + '</code></td></tr>').join('') +
      '</tbody></table></div><p class="note">Local preview only. Original bytes have not been uploaded or deployed.</p>'
    : '<p class="note">Selection stays in your browser. No file contents are sent to NixLab.</p>';
  return title("INCUS WORKSPACE", "Projects", "Observe Incus resources and inspect source before a future reviewed import.") +
    card("Observed Incus projects",
      '<button type="button" id="native-project-refresh">Refresh inventory</button>' +
      message + (rows ? '<div class="table-wrap"><table><thead><tr><th>Project</th><th>Guests</th><th>Observed state</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
      '<p><a href="/docs/incus-native-architecture.md">Runtime and ownership contract</a></p>') +
    card("Preview original application files locally",
      '<p>Preview Dockerfiles, Compose YAML and related build-context files without executing them. Relative paths and exact SHA-256 digests remain visible.</p>' +
      '<label class="field"><span>Choose files</span><input id="native-source-files" type="file" multiple></label>' +
      '<label class="field"><span>Choose directory</span><input id="native-source-folder" type="file" webkitdirectory directory multiple></label>' +
      (nativeSourcePreflight.scanning ? '<p role="status">Computing checksums in the browser…</p>' : '') +
      (nativeSourcePreflight.error ? '<p role="alert" class="error">' + esc(nativeSourcePreflight.error) + '</p>' : '') + manifest) +
    card("Declarative deployment",
      '<p>File upload, OCI building, secrets, managed deployment, revisions and backups will be enabled only after the privileged broker and native Incus adapter have passed their security and recovery tests.</p>' +
      '<p><a href="/docs/incus-native-plan.md">Implementation phases</a></p>');
}
async function nativePreviewFiles(files) {
  nativeSourcePreflight.files = [];
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
      if (file.size > NATIVE_MAX_FILE_BYTES) throw Error("Preview limit is 4 MiB per file.");
      total += file.size;
      if (total > NATIVE_MAX_BYTES) throw Error("Preview limit is 16 MiB per selection.");
      const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const sha256 = [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
      items.push({path, size: file.size, sha256});
    }
    nativeSourcePreflight.files = items.sort((a, b) => a.path.localeCompare(b.path));
  } catch (error) {
    nativeSourcePreflight.error = error.message || "Source preflight failed.";
    nativeSourcePreflight.files = [];
  } finally {
    nativeSourcePreflight.scanning = false;
    if (state.page === "projects") shell();
  }
}
function bindNativeProjects() {
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
