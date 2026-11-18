"use strict";

// Metadata-only project secrets UI. Secret values live solely in password
// inputs until a reauthenticated POST, never in browser-visible JS state.
const nativeSecretsView = {
  loaded: false, loading: false, busy: false, data: null, selected: "",
  error: "", message: "", review: null, editKey: "",
};

function nativeSecretProject() {
  const projects = nativeSecretsView.data?.projects || [];
  return projects.find(item => item.project === nativeSecretsView.selected) || projects[0] || null;
}

function nativeSecretsPanel() {
  const v = nativeSecretsView;
  if (state.principal?.role !== "admin")
    return card("Project secrets", "<p>Only administrators can manage encrypted project secrets.</p>");
  if (!v.loaded)
    return card("Project secrets", "<p>Loading metadata-only SOPS secret inventory…</p>");
  const identity = v.data?.identity || {};
  const status = identity.configured && identity.healthy
    ? '<p>Age recipient: <code>' + esc(identity.recipient) +
      '</code>. The private identity remains on the host; keep a separately protected offline backup.</p>'
    : identity.configured
      ? '<p role="alert" class="error">Installed age identity is unhealthy. Never regenerate over it; restore the original identity safely.</p>'
      : '<p>No project age identity enrolled. Choose generate or restore explicitly before adding secrets.</p>';
  const setup = !identity.configured
    ? '<fieldset><legend>Initialize project age identity once</legend>' +
      '<label class="field"><span>Identity action</span><select id="native-age-mode">' +
      '<option value="generate">Generate a new persistent identity</option>' +
      '<option value="restore">Restore an existing identity</option></select></label>' +
      '<label class="field"><span>Recovery key (only for restore)</span>' +
      '<textarea id="native-age-restore" rows="3" autocomplete="off" spellcheck="false" placeholder="AGE-SECRET-KEY-1…"></textarea></label>' +
      '<p class="note">Generation does not reveal or download the private key. Arrange an independent offline recovery copy before storing irreplaceable secrets.</p>' +
      '<label class="field"><span>Current administrator password</span>' +
      '<input type="password" id="native-age-password" autocomplete="current-password"></label>' +
      '<label><input id="native-age-ack" type="checkbox"> I understand the identity must be backed up separately and never silently replaced</label>' +
      '<button type="button" id="native-age-enroll"' + (v.busy ? " disabled" : "") +
      '>Enroll age identity</button></fieldset>' : "";
  const project = nativeSecretProject();
  const projects = v.data?.projects || [];
  const choose = projects.length
    ? '<label class="field"><span>Project</span><select id="native-secret-project">' +
      projects.map(item => '<option value="' + esc(item.project) + '"' +
        (project?.project === item.project ? " selected" : "") + '>' +
        esc(item.project) + '</option>').join("") + '</select></label>'
    : '<p>No declarative projects with default.nix are available. Create a project in the project workspace first.</p>';
  const summary = project
    ? '<p>Encrypted revision: <code>' + esc(project.revision || "none") + '</code> · ' +
      esc(project.secretCount) + ' keys · ' +
      (project.indexed ? "Metadata synchronized" : "Metadata/revision drift; resolve before editing") +
      '</p>' +
      (project.keys.length
        ? '<div class="table-wrap"><table><thead><tr><th>Key</th><th>Status</th><th>Action</th></tr></thead><tbody>' +
          project.keys.map(key => '<tr><td><code>' + esc(key) +
            '</code></td><td>Encrypted; value never returned</td><td>' +
            '<button type="button" data-native-secret-rotate="' + esc(key) + '"' +
            (v.busy || !identity.healthy ? " disabled" : "") + '>Update</button></td></tr>').join("") +
          '</tbody></table></div>'
        : '<p>No encrypted environment keys yet.</p>')
    : "";
  const form = identity.healthy && project && !v.review
    ? '<fieldset><legend>Review a write-only secret change</legend>' +
      '<label class="field"><span>Variable name</span><input id="native-secret-key" maxlength="128" ' +
      'pattern="[A-Za-z_][A-Za-z0-9_]*" autocomplete="off" value="' + esc(v.editKey) +
      '" placeholder="API_TOKEN"></label>' +
      '<label class="field"><span>Change type</span><select id="native-secret-action">' +
      '<option value="set">Add or replace encrypted value</option>' +
      '<option value="delete">Delete encrypted value</option></select></label>' +
      '<label class="field"><span>New secret (for add/replace only)</span>' +
      '<input id="native-secret-value" type="password" autocomplete="new-password" maxlength="8192"></label>' +
      '<label class="field"><span>Current administrator password</span>' +
      '<input id="native-secret-review-password" type="password" autocomplete="current-password"></label>' +
      '<button type="button" id="native-secret-review"' + (v.busy || !project.indexed ? ' disabled' : '') +
      '>Prepare encrypted review</button>' +
      '<p class="note">SOPS encrypts the value through the root broker before any review artifact is written. No plaintext .env or build-time secret is published.</p></fieldset>'
    : "";
  const importPanel = identity.healthy && project && !v.review
    ? '<fieldset><legend>Import .env values directly into SOPS</legend>' +
      '<p class="note">The selected .env file is parsed locally. Only validated secret variables are sent to the authenticated encrypted-review endpoint. Raw .env files are never published.</p>' +
      '<label class="field"><span>Local .env file</span><input id="native-env-import-file" type="file" accept=".env,.txt,text/plain"></label>' +
      '<label class="field"><span>Current administrator password</span><input id="native-env-import-password" type="password" autocomplete="current-password"></label>' +
      '<button type="button" id="native-env-import-review"' + (v.busy || !project.indexed ? " disabled" : "") +
      '>Encrypt and review imported values</button></fieldset>'
    : "";
  const review = v.review
    ? '<fieldset><legend>Approve ciphertext-only publication</legend>' +
      '<p>Project: <code>' + esc(v.review.project) + '</code> · ' +
      esc(v.review.action) + ' <code>' + esc((v.review.changedKeys || [v.review.key]).join(", ")) + '</code></p>' +
      '<p>From: <code>' + esc(v.review.baseRevision || "none") +
      '</code> → <code>' + esc(v.review.revision) + '</code></p>' +
      '<p class="note">New ciphertext is verified, but publishing it does not automatically deploy or restart a guest.</p>' +
      '<label class="field"><span>Current administrator password</span>' +
      '<input id="native-secret-publish-password" type="password" autocomplete="current-password"></label>' +
      '<label><input type="checkbox" id="native-secret-publish-ack"> Publish this exact encrypted revision</label>' +
      '<button id="native-secret-publish" type="button"' + (v.busy ? " disabled" : "") +
      '>Publish encrypted secrets</button></fieldset>'
    : "";
  const recoveryPanel = '<fieldset><legend>Offline encrypted recovery</legend>' +
    '<p>Export an authenticated, passphrase-encrypted recovery kit. Keep it offline and separate from the Git-synced SOPS ciphertext. Verify the kit before relying on it.</p>' +
    (identity.healthy
      ? '<label class="field"><span>Recovery passphrase (16+ characters)</span><input id="native-recovery-export-passphrase" type="password" autocomplete="new-password"></label>' +
        '<label class="field"><span>Repeat passphrase</span><input id="native-recovery-export-confirm" type="password" autocomplete="new-password"></label>' +
        '<label><input type="checkbox" id="native-recovery-backup"> Include present restic unlock/remote credential files</label>' +
        '<label class="field"><span>Administrator password</span><input id="native-recovery-export-admin" type="password" autocomplete="current-password"></label>' +
        '<button type="button" id="native-recovery-export"' + (v.busy ? " disabled" : "") +
        '>Download encrypted recovery kit</button>'
      : '') +
    '<p>To verify or restore a kit, select the encrypted JSON file and enter its passphrase. Restoring never overwrites a host age identity or existing backup credentials.</p>' +
    '<label class="field"><span>Encrypted recovery kit</span><input id="native-recovery-kit-file" type="file" accept=".json,application/json"></label>' +
    '<label class="field"><span>Recovery passphrase</span><input id="native-recovery-import-passphrase" type="password" autocomplete="off"></label>' +
    '<label class="field"><span>Administrator password</span><input id="native-recovery-import-admin" type="password" autocomplete="current-password"></label>' +
    '<label><input type="checkbox" id="native-recovery-import-ack"> I understand restoring changes protected machine identity/credentials</label>' +
    '<button type="button" id="native-recovery-verify"' + (v.busy ? " disabled" : "") +
    '>Verify kit without changes</button> ' +
    '<button type="button" id="native-recovery-restore"' +
    (v.busy || identity.configured ? ' disabled' : '') + '>Restore kit on new host</button></fieldset>';
  const reindexPanel = project && !project.indexed
    ? '<fieldset><legend>Reconcile restored ciphertext</legend>' +
      '<p class="note">An encrypted project file differs from its metadata index. Inspect source and keys before adopting it; never overwrite the encrypted file.</p>' +
      '<label class="field"><span>Administrator password</span><input id="native-reindex-password" type="password" autocomplete="current-password"></label>' +
      '<label><input id="native-reindex-ack" type="checkbox"> Adopt verified SOPS ciphertext and rebuild key-name metadata</label>' +
      '<button type="button" id="native-secret-reindex">Rebuild metadata</button></fieldset>'
    : '';
  const errors = (v.error ? '<p role="alert" class="error">' + esc(v.error) + '</p>' : '') +
                 (v.message ? '<p role="status">' + esc(v.message) + '</p>' : '');
  return card("Environment & secrets · SOPS/age",
    '<p>Encrypted, declarative variables managed in this Web UI. Only key names and ciphertext hashes are returned; plaintext is never readable through this interface.</p>' +
    status + setup + choose + summary + form + importPanel + review + reindexPanel + recoveryPanel + errors +
    '<p><a href="/docs/incus-native-secrets-ui.md">Secret management, delivery and recovery boundaries</a></p>');
}

async function nativeSecretsRefresh() {
  if (nativeSecretsView.loading) return;
  const v = nativeSecretsView;
  v.loading = true;
  try {
    v.data = await api("project-secrets");
    if (!v.data?.projects?.some(p => p.project === v.selected))
      v.selected = v.data?.projects?.[0]?.project || "";
    v.error = "";
  } catch (error) {
    v.data = {identity: {}, projects: []};
    v.error = error.message || "Secret metadata inventory unavailable.";
  } finally {
    v.loaded = true;
    v.loading = false;
    if (state.page === "projects") shell();
  }
}

async function nativeEnrollIdentity() {
  const v = nativeSecretsView;
  if (v.busy) return;
  const mode = document.getElementById("native-age-mode")?.value;
  const privateEl = document.getElementById("native-age-restore");
  const passwordEl = document.getElementById("native-age-password");
  const privateKey = privateEl?.value || "";
  const current = passwordEl?.value || "";
  const acknowledge = document.getElementById("native-age-ack")?.checked === true;
  if (privateEl) privateEl.value = "";
  if (passwordEl) passwordEl.value = "";
  v.busy = true;
  v.error = "";
  try {
    if (!acknowledge || !current) throw Error("Confirm backup responsibility and reauthenticate.");
    await api("project-secrets/enroll", {
      mode, privateKey: mode === "restore" ? privateKey : null, current, acknowledge,
    });
    v.message = "Identity enrolled. Make a separately protected offline backup before using it.";
    await nativeSecretsRefresh();
  } catch (error) {
    v.error = error.message || "Age identity enrollment failed.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeSecretReview() {
  const v = nativeSecretsView, project = nativeSecretProject();
  if (v.busy || !project || v.review) return;
  const key = document.getElementById("native-secret-key")?.value?.trim();
  const action = document.getElementById("native-secret-action")?.value;
  const valueEl = document.getElementById("native-secret-value");
  const passwordEl = document.getElementById("native-secret-review-password");
  const value = valueEl?.value;
  const current = passwordEl?.value;
  if (valueEl) valueEl.value = "";
  if (passwordEl) passwordEl.value = "";
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(key || "")) throw Error("Invalid variable name.");
    if (!current) throw Error("Administrator password is required.");
    if (action === "set" && (!value || new TextEncoder().encode(value).length > 8192))
      throw Error("Enter a secret of at most 8192 UTF-8 bytes.");
    v.review = await api("project-secrets/review", {
      project: project.project, key, action,
      value: action === "set" ? value : null,
      expectedRevision: project.revision, current,
    });
    v.editKey = "";
    v.message = "Encrypted review prepared. No running workload changed.";
  } catch (error) {
    v.error = error.message || "Encrypted review failed.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeSecretPublish() {
  const v = nativeSecretsView;
  if (v.busy || !v.review) return;
  const passwordEl = document.getElementById("native-secret-publish-password");
  const current = passwordEl?.value || "";
  const acknowledge = document.getElementById("native-secret-publish-ack")?.checked === true;
  if (passwordEl) passwordEl.value = "";
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (!acknowledge || !current) throw Error("Approve the reviewed revision and reauthenticate.");
    const result = await api("project-secrets/publish", {
      reviewId: v.review.reviewId, acknowledge, current,
    });
    v.review = null;
    v.message = "Ciphertext revision " + result.revision + " published. Workload apply remains separate.";
    await nativeSecretsRefresh();
  } catch (error) {
    v.error = error.message || "Secret publication failed. Refresh metadata before retry.";
    v.review = null; // never reuse a possibly stale approved review
    await nativeSecretsRefresh();
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

function nativeParseEnv(text) {
  if (typeof text !== "string" || text.length > 64 * 1024)
    throw Error(".env import exceeds the 64 KiB limit.");
  const values = Object.create(null);
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)$/);
    if (!match) throw Error("Unsupported .env syntax at line " + (i + 1) + ".");
    const key = match[1];
    if (Object.hasOwn(values, key)) throw Error("Duplicate secret name at line " + (i + 1) + ".");
    let value = match[2];
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    } else if (value.startsWith('"') || value.startsWith("'")) {
      throw Error("Unclosed .env quoted value at line " + (i + 1) + ".");
    }
    if (/[\u0000-\u001f]/.test(value) || value.includes("$" + "{") ||
        value.includes("$(") || value.includes(String.fromCharCode(96)))
      throw Error("Unsupported control characters or interpolation at line " + (i + 1) + ".");
    if (new TextEncoder().encode(value).length > 8192)
      throw Error("A secret value exceeds 8192 bytes.");
    values[key] = value;
    if (Object.keys(values).length > 64) throw Error("Import limit is 64 variables.");
  }
  if (!Object.keys(values).length) throw Error("The .env file has no importable variables.");
  return values;
}

async function nativeEnvImportReview() {
  const v = nativeSecretsView, project = nativeSecretProject();
  if (v.busy || !project || v.review) return;
  const fileInput = document.getElementById("native-env-import-file");
  const file = fileInput?.files?.[0];
  const password = document.getElementById("native-env-import-password");
  const current = password?.value;
  if (password) password.value = "";
  if (fileInput) fileInput.value = "";
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (!file || file.size > 64 * 1024 || !current)
      throw Error("Select a .env file of at most 64 KiB and enter the administrator password.");
    const values = nativeParseEnv(await file.text());
    v.review = await api("project-secrets/review", {
      project: project.project, action: "import", key: null, value: values,
      expectedRevision: project.revision, current,
    });
    v.message = "Environment values encrypted in a review; the original .env was not stored.";
  } catch (error) {
    v.error = error.message || "Environment import failed.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeRecoveryExport() {
  const v = nativeSecretsView;
  if (v.busy) return;
  const passEl = document.getElementById("native-recovery-export-passphrase");
  const repeatEl = document.getElementById("native-recovery-export-confirm");
  const adminEl = document.getElementById("native-recovery-export-admin");
  const passphrase = passEl?.value || "", repeat = repeatEl?.value || "", current = adminEl?.value || "";
  const includeBackup = document.getElementById("native-recovery-backup")?.checked === true;
  if (passEl) passEl.value = "";
  if (repeatEl) repeatEl.value = "";
  if (adminEl) adminEl.value = "";
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (passphrase.length < 16 || passphrase !== repeat || !current)
      throw Error("Enter and confirm a 16+ character recovery passphrase and reauthenticate.");
    const result = await api("project-secrets/recovery-export", {passphrase, includeBackup, current});
    if (!result.verified) throw Error("Server did not verify the recovery encryption.");
    const bytes = Uint8Array.from(atob(result.encrypted), c => c.charCodeAt(0));
    download("nixlab-project-secrets-recovery.json", bytes, "application/json");
    v.message = "Encrypted recovery kit downloaded; store it offline and verify it before trusting backups.";
  } catch (error) {
    v.error = error.message || "Recovery kit export failed.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeRecoveryImport(action) {
  const v = nativeSecretsView;
  if (v.busy || !["verify", "restore"].includes(action)) return;
  const file = document.getElementById("native-recovery-kit-file")?.files?.[0];
  const passEl = document.getElementById("native-recovery-import-passphrase");
  const adminEl = document.getElementById("native-recovery-import-admin");
  const passphrase = passEl?.value || "", current = adminEl?.value || "";
  const acknowledge = document.getElementById("native-recovery-import-ack")?.checked === true;
  if (passEl) passEl.value = "";
  if (adminEl) adminEl.value = "";
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (!file || file.size > 256 * 1024 || !passphrase || !current)
      throw Error("Select a bounded encrypted recovery kit and enter both passwords.");
    if (action === "restore" && !acknowledge)
      throw Error("Explicitly acknowledge the host recovery operation.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    let encoded = "";
    for (let i = 0; i < bytes.length; i += 16384)
      encoded += String.fromCharCode(...bytes.subarray(i, i + 16384));
    const result = await api("project-secrets/recovery-" + action, {
      encrypted: btoa(encoded), passphrase, current,
      acknowledge: action === "restore" ? acknowledge : false,
    });
    v.message = action === "verify"
      ? "Recovery kit authenticated for age recipient " + result.recipient + "."
      : "Recovery identity restored; separately restore and verify encrypted project source/data.";
    if (action === "restore") await nativeSecretsRefresh();
  } catch (error) {
    v.error = error.message || "Recovery kit rejected.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

async function nativeSecretsReindex() {
  const v = nativeSecretsView, project = nativeSecretProject();
  if (v.busy || !project || project.indexed) return;
  const password = document.getElementById("native-reindex-password");
  const current = password?.value;
  if (password) password.value = "";
  const acknowledge = document.getElementById("native-reindex-ack")?.checked === true;
  v.busy = true; v.error = ""; v.message = "";
  try {
    if (!current || !acknowledge) throw Error("Reauthenticate and acknowledge metadata recovery.");
    const result = await api("project-secrets/reindex", {
      project: project.project, current, acknowledge,
    });
    v.message = "Encrypted source authenticated and indexed at " + result.revision + ".";
    await nativeSecretsRefresh();
  } catch (error) {
    v.error = error.message || "Secret metadata cannot be recovered.";
  } finally {
    v.busy = false;
    if (state.page === "projects") shell();
  }
}

function bindNativeSecrets() {
  if (state.principal?.role !== "admin") return;
  if (!nativeSecretsView.loaded && !nativeSecretsView.loading) void nativeSecretsRefresh();
  document.querySelector("#native-age-enroll")?.addEventListener("click", () => {
    void nativeEnrollIdentity();
  });
  document.querySelector("#native-recovery-export")?.addEventListener("click", () => {
    void nativeRecoveryExport();
  });
  document.querySelector("#native-env-import-review")?.addEventListener("click", () => {
    void nativeEnvImportReview();
  });
  document.querySelector("#native-recovery-verify")?.addEventListener("click", () => {
    void nativeRecoveryImport("verify");
  });
  document.querySelector("#native-recovery-restore")?.addEventListener("click", () => {
    void nativeRecoveryImport("restore");
  });
  document.querySelector("#native-secret-reindex")?.addEventListener("click", () => {
    void nativeSecretsReindex();
  });
  document.querySelector("#native-secret-project")?.addEventListener("change", event => {
    nativeSecretsView.selected = event.currentTarget.value;
    nativeSecretsView.editKey = "";
    nativeSecretsView.review = null;
    nativeSecretsView.error = "";
    shell();
  });
  for (const button of document.querySelectorAll("[data-native-secret-rotate]")) {
    button.addEventListener("click", () => {
      nativeSecretsView.editKey = button.dataset.nativeSecretRotate;
      nativeSecretsView.review = null;
      shell();
    });
  }
  document.querySelector("#native-secret-review")?.addEventListener("click", () => {
    void nativeSecretReview();
  });
  document.querySelector("#native-secret-publish")?.addEventListener("click", () => {
    void nativeSecretPublish();
  });
}
