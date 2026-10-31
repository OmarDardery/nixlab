'use strict';

function bind() {
  document.querySelectorAll("[data-go]").forEach(
    (el) =>
      (el.onclick = () => {
        state.page = el.dataset.go;
        shell();
        window.scrollTo(0, 0);
      }),
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
    if (name === "management-stack") {
      for (const [path, value] of Object.entries({"cockpit.enable": true, "apps.enable": true, "apps.portainerPort": 9443, "maintenance.laptopMode": true})) {
        if (!(state.status.codeOverrides || []).some(c => c.setting === path || path.startsWith(c.setting + "."))) put(state.draft, path, value);
      }
      shell();
      toast("Management stack staged. Add credentials and review before activation; file-owned settings are preserved.");
      return;
    }
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
          (state.status.effectiveSettings || state.status.settings).management
            .address,
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
        updateInputs: state.updateInputs,
        modules: state.modules,
        revision: state.platformRevision || null,
        adminPassword:
          document.querySelector("#module-password")?.value ||
          document.querySelector("#platform-password")?.value,
        acknowledgePrivilegedModules:
          document.querySelector("#module-ack")?.checked ||
          document.querySelector("#platform-ack")?.checked,
      });
      state.modules = {};
      state.platformRevision = "";
      state.status = await api("status");
      state.page = "review";
      shell();
      return;
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
    if (name === "certificate") {
      const cert = await api("browser-certificate", {});
      const bytes = Uint8Array.from(atob(cert.pkcs12), (c) => c.charCodeAt(0));
      download(cert.name + ".p12", bytes, "application/x-pkcs12");
      document.querySelector("#certificate-result").innerHTML =
        `<div class="cert-result"><b>Browser identity downloaded</b><p>Import the PKCS#12 file into your browser or OS personal certificates, then open Incus. Keep the identity and its password private.</p><p>Password: <code>${esc(cert.password)}</code></p><p class="note">Identity: ${esc(cert.name)}. Revoke it through Incus when no longer needed.</p></div>`;
      return;
    }
  } catch (err) {
    toast(err.message);
  } finally {
    if (el.isConnected) el.disabled = false;
  }
}
