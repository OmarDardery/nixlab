'use strict';

function bind() {
  document.querySelectorAll("[data-go]").forEach(
    (el) =>
      (el.onclick = () => navigateTo(el.dataset.go)),
  );
  document.querySelectorAll("[data-path]").forEach((el) => {
    if (
      (state.status.codeOverrides || []).some(
        (c) =>
          c.setting === el.dataset.path ||
          el.dataset.path.startsWith(c.setting + "."),
      )
    )
      el.disabled = true;
  });
  document.querySelectorAll("[data-path]").forEach(
    (el) =>
      (el.onchange = () => {
        const value =
          el.type === "checkbox"
            ? el.checked
            : el.dataset.kind === "list"
              ? el.value
                  .split(el.dataset.path === "adminKeys" ? /\n/ : /[\n,]/)
                  .map((v) => v.trim())
                  .filter(Boolean)
              : el.type === "number"
                ? Number(el.value)
                : el.value;
        put(state.draft, el.dataset.path, value);
        if (el.type === "checkbox") shell();
      }),
  );
  document
    .querySelectorAll("[data-secret]")
    .forEach(
      (el) =>
        (el.oninput = () => (state.secrets[el.dataset.secret] = el.value)),
    );
  document.querySelectorAll("[data-ingress]").forEach(
    (el) =>
      (el.onchange = () => {
        try {
          state.draft.tunnel.ingress = JSON.parse(el.value);
          el.setCustomValidity("");
        } catch {
          el.setCustomValidity("Use a valid JSON hostname mapping");
          el.reportValidity();
        }
      }),
  );
  document.querySelectorAll("[data-mount]").forEach(
    (el) =>
      (el.onchange = () => {
        const m = state.draft.mounts[Number(el.dataset.mount)];
        const key = el.dataset.mountKey;
        m[key] = el.type === "checkbox" ? el.checked : el.value;
        if (key === "uuid") {
          const device = flatDisks(state.host.disks).find(
            (d) => d.uuid === m.uuid,
          );
          m.fsType = device?.fstype === "ntfs" ? "ntfs3" : device?.fstype || "";
        }
        if (key === "name") m.path = "/srv/nixlab/" + m.name;
        if (m.role !== "data") {
          m.required = true;
          m.readOnly = false;
        }
        shell();
      }),
  );
  document.querySelectorAll("[data-remove-mount]").forEach(
    (el) =>
      (el.onclick = () => {
        state.draft.mounts.splice(Number(el.dataset.removeMount), 1);
        shell();
      }),
  );
  document
    .querySelectorAll("[data-action]")
    .forEach((el) => (el.onclick = () => action(el.dataset.action, el)));
  if ((state.status.codeOverrides || []).some((c) => c.setting === "mounts"))
    document
      .querySelectorAll(
        '[data-mount],[data-remove-mount],[data-action="add-mount"]',
      )
      .forEach((el) => (el.disabled = true));
  if (
    (state.status.codeOverrides || []).some(
      (c) =>
        c.setting === "tunnel.ingress" ||
        c.setting.startsWith("tunnel.ingress."),
    )
  )
    document
      .querySelectorAll("[data-ingress]")
      .forEach((el) => (el.disabled = true));
  const update = document.querySelector("#update-inputs");
  if (update) update.onchange = () => (state.updateInputs = update.checked);
  const reuse = document.querySelector("#arcane-reuse");
  if (reuse) reuse.onchange = () => { state.arcaneReuse = reuse.checked; delete state.secrets.arcanePassword; shell(); };
  const arcaneReset = document.querySelector("#arcane-reset-form");
  if (arcaneReset) arcaneReset.onsubmit = async (event) => {
    event.preventDefault();
    const button = arcaneReset.querySelector("button"); button.disabled = true;
    try {
      await api("arcane/reset", Object.fromEntries(new FormData(arcaneReset)));
      toast("Arcane password changed and login verified. NixLab password is unchanged.");
    } catch (error) { toast(error.message); }
    finally { arcaneReset.reset(); button.disabled = false; }
  };
  const password = document.querySelector("#password-form");
  if (password)
    password.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api("password", Object.fromEntries(new FormData(password)));
        login("Password changed. Sign in with your new password.");
      } catch (err) {
        toast(err.message);
      }
    };
}
async function action(name, el) {
  el.disabled = true;
  try {
    if (name === "migrate-layout") {
      await api("migrate-layout", {
        current: document.querySelector("#layout-password").value,
      });
      state.status = await api("status");
      shell();
      toast("Local machine layout migrated. Original files retained.");
      return;
    }
    if (name === "stage-module") {
      const file = document.querySelector("#module-file").files[0];
      if (!file) throw Error("Select a Nix file.");
      if (file.size > 65536) throw Error("File exceeds 64 KiB.");
      const path =
        document.querySelector("#module-path").value.trim() || file.name;
      state.modules[path] = await file.text();
      state.page = "review";
      shell();
      return;
    }
    if (name === "clear-modules") {
      state.modules = {};
      shell();
      return;
    }
    if (name === "copy-ssh") {
      await navigator.clipboard.writeText(
        "ssh operator@" +
          (state.status.managementAddress || (state.status.effectiveSettings || state.status.settings).management.address),
      );
      toast("SSH command copied.");
      return;
    }
    if (name === "export-settings") {
      download(
        "nixlab-settings.json",
        JSON.stringify(state.draft, null, 2) + "\n",
        "application/json",
      );
      return;
    }
    if (name === "logout") {
      await api("logout", {});
      login();
      return;
    }
    if (name === "add-mount") {
      state.draft.mounts.push({
        name: "data-" + (state.draft.mounts.length + 1),
        uuid: "",
        path: "/srv/nixlab/data-" + (state.draft.mounts.length + 1),
        fsType: "ext4",
        role: "data",
        required: false,
        readOnly: false,
      });
      shell();
      return;
    }
    if (name === "suggest") {
      const suggestion = state.host.suggestedGuests;
      if (!suggestion)
        throw Error(
          "No non-overlapping suggestion is available; select a subnet explicitly.",
        );
      state.draft.guests = copy(suggestion);
      shell();
      toast(
        "Suggested guest subnet selected. Review it against any disconnected networks.",
      );
      return;
    }
    if (name === "build") {
      await api("review", {
        settings: state.draft,
        secrets: state.secrets,
        reuseArcanePassword: !!state.arcaneReuse,
        arcaneCurrent: document.querySelector("#arcane-current")?.value,
        updateInputs: state.updateInputs,
        modules: state.modules,
        revision: state.platformRevision || null,
        adminPassword:
          document.querySelector("#platform-password")?.value,
        acknowledgePrivilegedModules:
          document.querySelector("#platform-ack")?.checked,
      });
      // The user may have navigated while the review request was in flight.
      document.querySelectorAll("#platform-password").forEach(input => { input.value = ""; });
      document.querySelectorAll("#platform-ack").forEach(input => { input.checked = false; });
      state.modules = {};
      state.secrets = {}; state.arcaneReuse = false;
      document.querySelectorAll("#arcane-current").forEach(input => { input.value = ""; });
      state.platformRevision = "";
      state.status = await api("status");
      state.page = "review";
      shell();
      return;
    }
    if (["deploy-guests", "abandon-deployment"].includes(name)) {
      await api("deploy-guests", {id: state.status.job.id, resume: !!state.status.job.guestDeployment, abandon: name === "abandon-deployment"});
      state.status = await api("status"); shell(); return;
    }
    if (
      ["apply", "confirm", "rollback", "cancel", "rollback-confirmed"].includes(
        name,
      )
    ) {
      await api(name === "rollback-confirmed" ? "rollback" : name, {
        id:
          name === "rollback-confirmed"
            ? state.status.lastConfirmed.id
            : state.status.job.id,
      });
      state.status = await api("status");
      if (name === "confirm") {
        state.draft = copy(state.status.settings);
        state.secrets = {};
        toast("Generation confirmed. Your configuration is active.");
      }
      shell();
      return;
    }
    if (name === "certificate-download") {
      const bytes = await claimCertificate();
      download("nixlab-browser.p12", bytes, "application/x-pkcs12");
      return;
    }
    if (name === "certificate-list") {
      const result = await api("browser-certificates");
      renderCertificateRows(result.certificates || []);
      return;
    }
    if (name === "certificate-renew") {
      document.querySelector("#certificate-device").value = el.dataset.device;
      document.querySelector("#certificate-renew").value = el.dataset.fingerprint;
      document.querySelector("#certificate-full-admin").checked = false;
      document.querySelector("#certificate-result").textContent = "Renewal selected. Supply a new bundle passphrase and your current password. The previous identity remains trusted until explicitly revoked.";
      document.querySelector("#certificate-passphrase").focus();
      return;
    }
    if (name === "certificate-revoke") {
      const input = document.querySelector("#certificate-current");
      const current = input.value; input.value = "";
      await api("revoke-browser-certificate", { fingerprint: el.dataset.fingerprint, current });
      const result = await api("browser-certificates");
      renderCertificateRows(result.certificates || []);
      toast("Browser identity revoked. Incus will reject subsequent authenticated requests.");
      return;
    }
    if (name === "certificate") {
      const input = document.querySelector("#certificate-passphrase");
      const password = document.querySelector("#certificate-current");
      const passphrase = input.value, current = password.value;
      input.value = ""; password.value = "";
      if (Array.from(passphrase).length < 16 || Array.from(passphrase).length > 256 || /[\r\n\0]/.test(passphrase))
        throw Error("Use a bundle passphrase of 16–256 characters with no line breaks.");
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(document.querySelector("#certificate-device").value))
        throw Error("Use a device name of up to 32 letters, digits, hyphens or underscores.");
      const fullAdmin = document.querySelector("#certificate-full-admin").checked;
      const renew = document.querySelector("#certificate-renew").value || undefined;
      const cert = await api("browser-certificate", {
        device: document.querySelector("#certificate-device").value,
        passphrase, current, fullAdmin, acknowledgeFullAdmin: fullAdmin, renew,
      });
      document.querySelector("#certificate-renew").value = "";
      document.querySelector("#certificate-result").innerHTML =
        `<div class="cert-result"><b>Browser identity registered</b><p>Claiming its one-time encrypted download. If download or import fails, refresh the identity list to revoke or reissue.</p><p>Fingerprint <code>${esc(cert.fingerprint)}</code></p><p>Scope: ${esc(cert.restricted ? cert.projects.join(", ") : "Unrestricted host administration")}. Expires: ${esc(cert.expiresAt)}.</p><p>Import the PKCS#12 file with your bundle passphrase. In Firefox use Settings → Privacy &amp; Security → Certificates → View Certificates → Your Certificates → Import. In Chromium use Settings → Privacy and security → Security → Manage certificates (or your operating system certificate manager).</p><p>Open Incus, select this identity and switch to the lab project. Revocation is independent of your NixLab sign-in.</p></div>`;
      const bytes = await claimCertificate();
      download(cert.name + ".p12", bytes, "application/x-pkcs12");
      return;
    }
  } catch (err) {
    if (name === "apply") {
      // The broker may have refreshed a stale runtime plan while rejecting apply.
      try { state.status = await api("status"); shell(); } catch (_) {}
    }
    toast(err.message);
  } finally {
    if (el.isConnected) el.disabled = false;
  }
}


function certificateRows(items) {
  if (!items.length) return "<p>No NixLab-owned browser identities. Existing Incus trust is unchanged.</p>";
  return `<div class="table-wrap"><table><thead><tr><th>Device / fingerprint</th><th>Scope / expiry</th><th>Status / actions</th></tr></thead><tbody>${items.map(cert => `<tr><td>${esc(cert.device)}<br><code>${esc(cert.fingerprint)}</code></td><td>${esc(cert.restricted ? cert.projects.join(", ") : "Unrestricted host administration")}<br>${esc(cert.expiresAt)}</td><td>${esc(cert.status)}<br><button data-action="certificate-renew" data-device="${esc(cert.device)}" data-fingerprint="${esc(cert.fingerprint)}">Renew</button> <button data-action="certificate-revoke" data-fingerprint="${esc(cert.fingerprint)}" ${cert.status === "revoked" ? "disabled" : ""}>Revoke</button></td></tr>`).join("")}</tbody></table></div>`;
}

function renderCertificateRows(items) {
  const target = document.querySelector("#certificate-list");
  target.innerHTML = certificateRows(items);
  target.querySelectorAll("[data-action]").forEach(button => {
    button.onclick = () => action(button.dataset.action, button);
  });
}
