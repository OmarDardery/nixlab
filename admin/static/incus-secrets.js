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
  const review = v.review
    ? '<fieldset><legend>Approve ciphertext-only publication</legend>' +
      '<p>Project: <code>' + esc(v.review.project) + '</code> · ' +
      esc(v.review.action) + ' <code>' + esc(v.review.key) + '</code></p>' +
      '<p>From: <code>' + esc(v.review.baseRevision || "none") +
      '</code> → <code>' + esc(v.review.revision) + '</code></p>' +
      '<p class="note">New ciphertext is verified, but publishing it does not automatically deploy or restart a guest.</p>' +
      '<label class="field"><span>Current administrator password</span>' +
      '<input id="native-secret-publish-password" type="password" autocomplete="current-password"></label>' +
      '<label><input type="checkbox" id="native-secret-publish-ack"> Publish this exact encrypted revision</label>' +
      '<button id="native-secret-publish" type="button"' + (v.busy ? " disabled" : "") +
      '>Publish encrypted secrets</button></fieldset>'
    : "";
  const errors = (v.error ? '<p role="alert" class="error">' + esc(v.error) + '</p>' : '') +
                 (v.message ? '<p role="status">' + esc(v.message) + '</p>' : '');
  return card("Environment & secrets · SOPS/age",
    '<p>Encrypted, declarative variables managed in this Web UI. Only key names and ciphertext hashes are returned; plaintext is never readable through this interface.</p>' +
    status + setup + choose + summary + form + review + errors +
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

function bindNativeSecrets() {
  if (state.principal?.role !== "admin") return;
  if (!nativeSecretsView.loaded && !nativeSecretsView.loading) void nativeSecretsRefresh();
  document.querySelector("#native-age-enroll")?.addEventListener("click", () => {
    void nativeEnrollIdentity();
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
