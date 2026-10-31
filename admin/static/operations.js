"use strict";
let operationView = {
  inventory: null,
  jobs: [],
  archives: [],
  health: null,
  users: [],
  recipes: [],
  generations: [],
  backupStatus: {},
  backupService: {},
  releases: [],
  loading: false,
  error: "",
};
const administrator = () => (state.principal?.role || "admin") === "admin";

function healthSummary() {
  const h = operationView.health;
  if (!h)
    return "<p>Health information is unavailable. Refresh to try again.</p>";
  const units = (h.services || []).filter((u) => u.LoadState === "loaded");
  const services = units
    .map(
      (u) =>
        `<li><code>${esc(u.Id)}</code>: ${esc(u.ActiveState)}${u.Result && u.Result !== "success" ? " · " + esc(u.Result) : ""}</li>`,
    )
    .join("");
  const certificates = Object.entries(h.certificates || {})
    .map(
      ([name, c]) =>
        `<li>${esc(name)}: ${c.present ? (c.expiresWithin30Days ? "expires soon" : esc(c.expires || "present")) : "not present"}</li>`,
    )
    .join("");
  return `<p>Backup: <b>${esc(h.backup?.health || "unknown")}</b> · Guests awaiting restart: ${esc(h.guestsAwaitingRestart ?? "unknown")}</p>${h.rootStorage ? `<p>Root storage: ${esc(formatBytes(h.rootStorage.availableBytes))} available of ${esc(formatBytes(h.rootStorage.totalBytes))}</p>` : ""}<ul>${services}${certificates}</ul>`;
}

function operationsPage() {
  const busy = operationView.jobs.some((j) =>
    ["queued", "running"].includes(j.status),
  );
  const status = operationView.backupStatus || {};
  const backupRunning = ["active", "activating", "deactivating"].includes(
    operationView.backupService?.ActiveState,
  );
  const localComplete = ["complete", "complete-with-exclusions"].includes(
    status.phase,
  );
  const failedUpload =
    localComplete &&
    !backupRunning &&
    operationView.backupService?.Result &&
    operationView.backupService.Result !== "success";
  const phase =
    backupRunning && localComplete
      ? "Local export complete; off-host transfer/retention running"
      : failedUpload
        ? "Local export complete; off-host backup failed"
        : status.phase || "No backup recorded";
  return (
    title(
      "OFF-HOST RECOVERY",
      "Back up the host and recover your data.",
      "Incus owns ordinary snapshots and guest controls. Nixlab coordinates independent backups and new-name disaster recovery.",
    ) +
    card(
      "Backup status",
      `<p>Current phase: <b>${esc(phase)}</b> ${status.group ? `· group ${esc(status.group)} of ${esc(status.groups)}` : ""}</p><p>${esc((status.members || []).join(", "))}</p>${status.excluded?.length ? `<p class="error">Excluded from coverage: ${esc(status.excluded.join(", "))}</p>` : ""}<p>Last full-host success: ${operationView.health?.lastBackupSuccess ? esc(new Date(operationView.health.lastBackupSuccess * 1000).toLocaleString()) : "Not recorded"}</p><p>Guests awaiting restart recovery: ${esc(operationView.health?.guestsAwaitingRestart ?? "Unknown")}</p>${operationView.error ? `<p class="error">${esc(operationView.error)}</p>` : ""}<button data-ops-refresh>Refresh</button> <button data-run-backup ${!administrator() || busy || !state.status.settings.backup.enable ? "disabled" : ""}>Run scheduled backup</button> <button data-check-backup ${!administrator() || busy || !state.status.settings.backup.enable ? "disabled" : ""}>Check restic repository</button> <button data-cancel-backup ${!administrator() || !backupRunning ? "disabled" : ""}>Cancel scheduled backup</button>`,
    ) +
    backupSettings() +
    card(
      "Complete local generations",
      `<div class="table-wrap"><table><thead><tr><th>Created</th><th>Coverage</th><th>Archives</th></tr></thead><tbody>${operationView.generations.map((g) => `<tr><td>${esc(new Date(g.created * 1000).toLocaleString())}<br><code>${esc(g.id)}</code></td><td>${esc(g.coverage)} · ${esc(g.guests)} guest(s)${g.excluded.length ? `<br>Excluded: ${esc(g.excluded.join(", "))}` : ""}</td><td>${esc(g.archives)}</td></tr>`).join("") || '<tr><td colspan="3">No complete generations recorded.</td></tr>'}</tbody></table></div><p>Incomplete exports never replace a complete generation. Off-host backups are inspected with the restic client; recover their files to an isolated directory and register an instance archive with <code>nixlab-admin guests register-export</code>.</p>`,
    ) +
    card(
      "Restore an independent export",
      `<div class="fields"><label class="field"><span>Available archive</span><select id="restore-archive"><option value="">Select an export</option>${operationView.archives.map((a) => `<option value="${esc(a.id)}" ${a.available === false ? "disabled" : ""}>${esc(a.project)}/${esc(a.name)} · ${esc(new Date(a.created * 1000).toLocaleString())}${a.available === false ? " (unavailable)" : ""}</option>`).join("")}</select></label><label class="field"><span>New stopped instance name</span><input id="restore-target" placeholder="restore-test"></label></div><button data-restore-export ${!administrator() || busy ? "disabled" : ""}>Restore stopped copy</button><p>Verify networking, volumes and credentials in Incus before releasing a restored instance with the recovery CLI. Snapshots and ordinary guest operations stay in Incus.</p><a href="/docs/recovery.md">Full off-host recovery procedure</a>`,
    ) +
    card(
      "Nixlab task history",
      `<div class="table-wrap"><table><thead><tr><th>Action</th><th>Target</th><th>Status</th><th>Result</th></tr></thead><tbody>${operationView.jobs
        .filter((j) => ["backup", "backup-check", "restore"].includes(j.action))
        .map(
          (j) =>
            `<tr><td>${esc(j.action)}</td><td>${esc(j.project)}/${esc(j.name)}</td><td>${esc(j.status)}</td><td>${esc(j.error || j.restoredInstance || "")}</td></tr>`,
        )
        .join("")}</tbody></table></div>`,
    ) +
    card(
      "Host service and certificate health",
      healthSummary() +
        `<details><summary>Diagnostic details</summary><pre class="log">${esc(JSON.stringify(operationView.health, null, 2))}</pre></details><button data-download-diagnostics>Download diagnostics</button>`,
    ) +
    (administrator()
      ? card(
          "Portable recovery kit",
          `<p>Export host configuration and optionally separately encrypted credentials. VM/application data still requires its independent backup.</p><div class="fields"><label class="field full"><span>age public recipient (optional)</span><input id="recovery-recipient" placeholder="age1…"></label><label class="field"><span>Your password</span><input id="recovery-password" type="password" autocomplete="current-password"></label></div><button data-download-recovery>Download recovery kit</button>`,
        ) + accountCard()
      : "")
  );
}

function accountCard() {
  return card(
    "Portal accounts",
    `<ul>${operationView.users.map((u) => `<li>${esc(u.username)} · ${esc(u.role)}</li>`).join("")}</ul><p>Administrators control the host. Operators can run permitted guest actions in lab. Viewers can inspect status. SSH/sudo and Incus certificates have their own permissions.</p><div class="fields"><label class="field"><span>Account name</span><input id="account-name" autocomplete="off"></label><label class="field"><span>Role</span><select id="account-role"><option>viewer</option><option>operator</option><option>admin</option></select></label><label class="field"><span>New account password (blank keeps an existing password)</span><input id="account-password" type="password" autocomplete="new-password"></label><label class="field"><span>Your password</span><input id="account-current" type="password" autocomplete="current-password"></label></div><button data-account-save>Save account</button> <button class="danger" data-account-delete>Delete named account</button>`,
  );
}

function platformReview() {
  if (!administrator()) return "";
  const job = state.status.job;
  return card(
    "Platform source upgrade",
    `<p>Installed revision: <code>${esc(state.status.release?.revision || "unknown")}</code>. Select a published release or enter a reviewed commit. Local machine settings and modules are preserved.</p><button type="button" data-load-releases>Find published releases</button><label class="field full"><span>Release</span><select id="platform-release"><option value="">Select a release</option>${operationView.releases.map((r) => `<option value="${esc(r.revision)}">${esc(r.name)} · ${esc(r.tag)} · ${esc(r.revision.slice(0, 12))}</option>`).join("")}</select></label><details><summary>Advanced: select a commit</summary><label class="field full"><span>Upstream commit SHA (optional)</span><input id="platform-revision" value="${esc(state.platformRevision || "")}" maxlength="40" placeholder="40 hexadecimal characters"></label></details><div class="fields"><label class="field"><span>Your password for code changes</span><input id="platform-password" type="password" autocomplete="current-password"></label></div><label class="toggle"><input id="platform-ack" type="checkbox">I reviewed this privileged platform revision and approve building its system code.</label>${job?.platformRevision ? `<p>Candidate: <a href="${projectRepository}/commit/${esc(job.platformRevision)}" target="_blank" rel="noopener noreferrer">${esc(job.platformRevision)}</a></p><pre class="log">${esc(JSON.stringify(job.platformChanges || [], null, 2))}</pre>` : ""}${job?.inputChanges ? `<p>Changed pinned inputs:</p><pre class="log">${esc(JSON.stringify(job.inputChanges, null, 2))}</pre>` : ""}${job?.rebootRequired ? '<p class="note">The candidate kernel differs from the booted kernel. Plan an explicit reboot after confirming activation.</p>' : ""}`,
  );
}

async function refreshOperations(full = true) {
  if (operationView.loading) return;
  operationView.loading = true;
  let changed = false;
  try {
    const operations = await api("operations");
    changed =
      JSON.stringify([
        operations.jobs,
        operations.generations,
        operations.backupStatus,
        operations.backupService,
      ]) !==
      JSON.stringify([
        operationView.jobs,
        operationView.generations,
        operationView.backupStatus,
        operationView.backupService,
      ]);
    Object.assign(operationView, operations);
    if (full || changed) {
      operationView.health = await api("health");
      if (administrator()) operationView.users = (await api("users")).users;
    }
    operationView.error = "";
    operationView.inventory = [];
  } catch (error) {
    operationView.error = error.message;
    operationView.inventory ??= [];
    changed = true;
  } finally {
    operationView.loading = false;
    if (
      state.page === "operations" &&
      (full || changed) &&
      !document.activeElement?.matches("input,textarea,select")
    )
      shell();
  }
}

async function submitOperation(request) {
  await api("operation", request);
  await refreshOperations();
}
function bindOperations() {
  const safe = (fn) => async (event) => {
    const el = event.currentTarget;
    el.disabled = true;
    try {
      await fn(el);
    } catch (error) {
      toast(error.message);
    } finally {
      if (el.isConnected) el.disabled = false;
    }
  };
  document
    .querySelectorAll("[data-ops-refresh]")
    .forEach((el) => (el.onclick = safe(() => refreshOperations())));
  const by = (id) => document.getElementById(id);
  const restore = by("restore-archive");
  if (restore)
    document.querySelector("[data-restore-export]").onclick = safe(async () => {
      const archive = operationView.archives.find(
        (a) => a.id === restore.value,
      );
      if (!archive) throw Error("Select an export.");
      if (window.confirm("Restore to a new stopped instance?"))
        await submitOperation({
          action: "restore",
          project: archive.project,
          name: archive.name,
          archive: archive.id,
          target: by("restore-target").value.trim(),
          acknowledge: true,
        });
    });
  for (const [selector, action] of [
    ["[data-run-backup]", "backup"],
    ["[data-check-backup]", "backup-check"],
  ])
    document.querySelectorAll(selector).forEach(
      (el) =>
        (el.onclick = safe(async () => {
          if (
            window.confirm(
              action === "backup"
                ? "Back up the configured scope? Selected guest groups stop briefly."
                : "Check the independent repository?",
            )
          )
            await submitOperation({
              action,
              project: "lab",
              name: "all",
              acknowledge: true,
            });
        })),
    );
  document.querySelectorAll("[data-cancel-backup]").forEach(
    (el) =>
      (el.onclick = safe(async () => {
        if (
          window.confirm(
            "Cancel the scheduled backup and recover stopped guests?",
          )
        ) {
          await api("cancel-backup", {});
          await refreshOperations();
        }
      })),
  );
  document
    .querySelectorAll("[data-download-diagnostics]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          download(
            "nixlab-diagnostics.json",
            JSON.stringify(operationView.health, null, 2),
            "application/json",
          )),
    );
  document.querySelectorAll("[data-download-recovery]").forEach(
    (el) =>
      (el.onclick = safe(async () => {
        const result = await api("recovery-export", {
          recipient: by("recovery-recipient").value.trim() || null,
          current: by("recovery-password").value,
        });
        for (const [name, data] of Object.entries(result.files))
          download(
            name,
            Uint8Array.from(atob(data), (c) => c.charCodeAt(0)),
            "application/octet-stream",
          );
        by("recovery-password").value = "";
      })),
  );
  for (const [selector, remove] of [
    ["[data-account-save]", false],
    ["[data-account-delete]", true],
  ])
    document.querySelectorAll(selector).forEach(
      (el) =>
        (el.onclick = safe(async () => {
          if (remove && !window.confirm("Delete this named account?")) return;
          operationView.users = (
            await api("users", {
              username: by("account-name").value.trim(),
              role: by("account-role").value,
              password: by("account-password").value,
              current: by("account-current").value,
              delete: remove,
            })
          ).users;
          shell();
        })),
    );
  document.querySelectorAll("[data-load-releases]").forEach(
    (el) =>
      (el.onclick = safe(async () => {
        operationView.releases = (await api("releases")).releases;
        const select = by("platform-release");
        select.innerHTML =
          '<option value="">Select a release</option>' +
          operationView.releases
            .map(
              (r) =>
                `<option value="${esc(r.revision)}">${esc(r.name)} · ${esc(r.tag)} · ${esc(r.revision.slice(0, 12))}</option>`,
            )
            .join("");
        if (!operationView.releases.length)
          toast("No published releases are available yet.");
      })),
  );
  const release = by("platform-release");
  if (release)
    release.onchange = () => {
      state.platformRevision = release.value;
      const revision = by("platform-revision");
      if (revision) revision.value = release.value;
    };
  const revision = by("platform-revision");
  if (revision)
    revision.oninput = () => (state.platformRevision = revision.value.trim());
  if (
    state.page === "operations" &&
    operationView.inventory === null &&
    !operationView.loading
  )
    refreshOperations();
  if (!administrator()) {
    document
      .querySelectorAll(
        "[data-path],[data-secret],[data-mount],[data-remove-mount],[data-ingress]",
      )
      .forEach((el) => (el.disabled = true));
    document.querySelectorAll("[data-action]").forEach((el) => {
      if (
        !["logout", "copy-ssh", "export-settings"].includes(el.dataset.action)
      )
        el.disabled = true;
    });
  }
}
