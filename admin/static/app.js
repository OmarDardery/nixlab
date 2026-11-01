"use strict";
const state = {
  csrf: "",
  page: "overview",
  status: null,
  host: null,
  draft: null,
  secrets: {},
  modules: {},
  updateInputs: false,
  timer: null,
  principal: { username: "admin", role: "admin" },
  platformRevision: "",
};
const app = document.querySelector("#app");
const pages = [
  ["overview", "◫", "Overview"],
  ["network", "⌁", "Host"],
  ["operations", "▥", "Recovery"],
  ["integrations", "↗", "Integrations"],
  ["review", "↗", "Changes"],
];
const hostPages = [
  ["network", "Connectivity"],
  ["hardware", "Hardware & disks"],
  ["system", "Preferences"],
  ["security", "Access & ingress"],
  ["files", "Advanced files"],
];
function primaryPage() {
  return hostPages.some(([id]) => id === state.page) ? "network" : state.page;
}
function hostTabs() {
  return primaryPage() === "network"
    ? `<nav class="host-tabs">${hostPages.map(([id, label]) => `<button data-go="${id}" ${state.page === id ? 'aria-current="page"' : ""}>${label}</button>`).join("")}</nav>`
    : "";
}
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const at = (obj, path) => path.split(".").reduce((a, k) => a?.[k], obj);
function put(obj, path, value) {
  const keys = path.split(".");
  const key = keys.pop();
  let target = obj;
  for (const k of keys) target = target[k] ??= {};
  target[key] = value;
}
const copy = (v) => JSON.parse(JSON.stringify(v));
function displayedSetting(path) {
  const owned = (state.status?.codeOverrides || []).some(c => c.setting === path || path.startsWith(c.setting + "."));
  return at(owned && state.status?.effectiveSettings ? state.status.effectiveSettings : state.draft, path);
}
function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 7000);
}
function field(
  label,
  path,
  {
    type = "text",
    hint = "",
    placeholder = "",
    full = false,
    disabled = false,
    list = false,
    options = null,
  } = {},
) {
  let value = displayedSetting(path);
  if (list) value = (value || []).join("\n");
  const attrs = `data-path="${esc(path)}" ${list ? 'data-kind="list"' : ""} ${disabled ? "disabled" : ""}`;
  let control;
  if (options)
    control = `<select ${attrs}>${options.map((o) => `<option value="${esc(o.value ?? o)}" ${(o.value ?? o) === value ? "selected" : ""}>${esc(o.label ?? o)}</option>`).join("")}</select>`;
  else if (type === "textarea")
    control = `<textarea ${attrs} placeholder="${esc(placeholder)}">${esc(value)}</textarea>`;
  else
    control = `<input ${attrs} type="${esc(type)}" value="${esc(value)}" placeholder="${esc(placeholder)}">`;
  return `<label class="field ${full ? "full" : ""}"><span>${esc(label)}</span>${control}${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
}
function toggle(label, path, hint = "") {
  return `<label class="toggle"><input type="checkbox" data-path="${esc(path)}" ${displayedSetting(path) ? "checked" : ""}><span>${esc(label)}${hint ? `<br><small class="muted">${esc(hint)}</small>` : ""}</span></label>`;
}
function secret(label, key, hint = "", textarea = false) {
  return `<label class="field full"><span>${esc(label)} ${state.host?.secretPresent?.[key] ? '<span class="tag">Already provisioned</span>' : ""}</span>${textarea ? `<textarea data-secret="${key}" spellcheck="false" placeholder="Leave empty to keep the current value">${esc(state.secrets[key] || "")}</textarea>` : `<input data-secret="${key}" type="password" autocomplete="new-password" value="${esc(state.secrets[key] || "")}" placeholder="${state.host?.secretPresent?.[key] ? "Leave empty to keep current" : "Enter an initial password"}">`}${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
}
const title = (eyebrow, heading, text) =>
  `<div class="page-title"><div><div class="eyebrow">${eyebrow}</div><h1>${heading}</h1><p>${text}</p></div><button class="primary" data-go="review">Review changes ↗</button></div>`;
const card = (heading, body, caption = "") =>
  `<section class="card"><div class="card-header"><h2>${heading}</h2>${caption ? `<span class="caption">${caption}</span>` : ""}</div>${body}</section>`;
function ownershipBanner() {
  const changes = state.status?.codeOverrides || [];
  return changes.length
    ? `<div class="banner"><div><h3>File-managed settings are active</h3><p>${esc(changes.map((c) => c.setting).join(", "))}. Edit the owning Nix module to change these effective values.</p></div></div>`
    : "";
}
function pendingBanner() {
  const p = state.status?.pending,
    j = state.status?.job;
  if (!p) return "";
  return `<div class="banner warn status-banner"><div><h3>Confirm your connection after activation</h3><p>${j?.status === "awaiting-confirmation" ? "Your previous generation will be restored unless you confirm." : "Activation is in progress; recovery is armed."} <span id="countdown"></span></p></div><button class="primary" data-action="confirm" ${j?.status !== "awaiting-confirmation" ? "disabled" : ""}>Confirm access</button></div>`;
}
function shell() {
  const settings = state.status.effectiveSettings || state.status.settings;
  app.innerHTML = `<div class="layout"><aside class="sidebar"><div class="brand"><span class="mark">N</span><div>nixlab<small>HOMELAB CONTROL</small></div></div><div><div class="nav-label">WORKSPACE</div><nav class="nav">${pages.map(([id, icon, label]) => `<button data-go="${id}" class="${primaryPage() === id ? "active" : ""}"><span class="nav-icon">${icon}</span>${label}</button>`).join("")}</nav></div><div class="sidebar-bottom"><b><span class="dot"></span>${esc(settings.hostName)}</b>Declarative host · NixOS<br><span class="mono">${esc(settings.management.address)}</span><br><button class="small" data-action="logout">Sign out</button></div></aside><div class="content"><header class="topbar"><div class="breadcrumb">Workspace <span class="muted">/</span> <b>${esc(hostPages.find((p) => p[0] === state.page)?.[1] || pages.find((p) => p[0] === state.page)?.[2])}</b></div><div class="top-actions"><span class="pill"><span class="dot"></span>${settings.ready ? "Configured host" : "Initial setup"}</span><span class="mono muted">${esc(state.principal.username)} · ${esc(state.principal.role)}</span></div></header><main class="page">${pendingBanner()}${ownershipBanner()}${hostTabs()}${renderPage()}</main></div></div>`;
  bind();
  bindOperations();
  bindFoundation();
  countdown();
}
function renderPage() {
  return {
    overview: hostOverview,
    network: hostConnectivity,
    hardware: hostHardware,
    storage: hostHardware,
    integrations,
    apps: integrations,
    security,
    system,
    files,
    review,
    operations: operationsPage,
  }[state.page]();
}

const projectRepository = "https://github.com/OmarDardery/nixlab";
// Keep pre-release guides reachable until the implementation branch is merged.
const projectGuides = "/docs/";
function linkRow(icon, heading, detail, url, label = "Open ↗") {
  return `<div class="service-row"><span class="service-icon" aria-hidden="true">${esc(icon)}</span><div class="service-copy"><h3>${esc(heading)}</h3><small>${esc(detail)}</small></div><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a></div>`;
}
function homepageTools() {
  const active = state.status.effectiveSettings || state.status.settings,
    address = state.status.managementAddress || active.management.address,
    ready = state.status.setupComplete;
  const incus = ready
    ? linkRow(
        "▦",
        "Incus",
        "Virtual machines, containers, networks, storage & snapshots",
        `https://${address}:8443`,
      )
    : '<div class="service-row"><div class="service-copy"><h3>Incus</h3><small>Available after the first activation.</small></div><button data-go="review">Finish setup →</button></div>';
  const portainer =
    ready && active.apps.enable
      ? linkRow(
          "▱",
          "Portainer",
          "Compose projects, logs & service configuration",
          `https://${address}:${active.apps.portainerPort ?? 9441}`,
        )
      : `<div class="service-row"><div class="service-copy"><h3>Portainer</h3><small>${active.apps.enable ? "Available after the first activation." : "Applications VM is disabled; no dashboard is running."}</small></div><button data-go="apps">${active.apps.enable ? "Configure →" : "Enable apps →"}</button></div>`;
  const cockpit = ready && active.cockpit?.enable
    ? linkRow("⌘", "Cockpit", "Host terminal, systemd services, journal & resource usage", `https://${address}:${active.cockpit.port}`)
    : '<div class="service-row"><div class="service-copy"><h3>Cockpit</h3><small>Host web terminal uses a local Linux account; separate from portal accounts.</small></div><button data-go="integrations">Configure host access →</button></div>';
  return card(
    "Management tools",
    incus +
      cockpit +
      portainer +
      `<div class="service-row"><div class="service-copy"><h3>SSH administration</h3><small>Key-based access to the active host as operator.</small></div><button data-action="copy-ssh" ${ready ? "" : "disabled"}>Copy command</button></div><div class="service-row"><div class="service-copy"><h3>Monitoring & logs</h3><small>${active.monitoring.history ? "Local history is enabled." : "Live Incus views and host health; local history is disabled."} Prometheus stays on host loopback.</small></div><button data-go="system">Settings →</button></div><div class="separator"></div><p class="note">Incus browser access requires a separate, revocable client certificate. Links use active settings, including while you edit a different desired address.</p><button data-action="certificate" ${ready ? "" : "disabled"}>Create browser certificate</button><div id="certificate-result"></div>`,
    "Private management",
  );
}
function homepageResources() {
  const active = state.status.effectiveSettings || state.status.settings;
  const apps = active.tunnel.enable
    ? Object.keys(active.tunnel.ingress)
        .filter(
          (name) =>
            /^[a-zA-Z0-9][a-zA-Z0-9.-]*$/.test(name) && !name.includes("*"),
        )
        .map((name) =>
          linkRow(
            "↗",
            name,
            "Configured public application endpoint; availability is not checked",
            "https://" + name,
          ),
        )
        .join("")
    : "";
  return (
    card(
      "Application helpers",
      linkRow(
        "↗",
        "Incus helper scripts",
        "Community installers adapted for Incus; Pinned runner; see Integrations for verification status",
        "https://github.com/community-scripts/Incus",
        "Browse ↗",
      ) +
        linkRow(
          "↗",
          "Community Scripts catalog",
          "Check platform support before choosing an installer",
          "https://community-scripts.org/",
          "Browse ↗",
        ) +
        linkRow(
          "↗",
          "Proxmox helper scripts",
          "Proxmox host/VM tools remain platform-specific",
          "https://github.com/community-scripts/ProxmoxVE",
          "Reference ↗",
        ) +
        '<p class="note">These links open external resources and never execute installers. Use the packaged CLI runner for the upstream wizard. Script-created applications remain mutable and Incus-managed.</p>',
      "External resources",
    ) +
    card(
      "Guides & project",
      linkRow(
        "▤",
        "Setup & recovery",
        "Installation, storage roles, safe activation and recovery",
        projectGuides + "setup.md",
      ) +
        linkRow(
          "▤",
          "Administration & CLI",
          "SSH, guest commands and project operations",
          projectGuides + "administration.md",
        ) +
        linkRow(
          "▤",
          "Networking",
          "Host, guest, application and public endpoint boundaries",
          projectGuides + "networking.md",
        ) +
        linkRow(
          "▤",
          "Backups & restoration",
          "Independent backups and recovery procedures",
          projectGuides + "recovery.md",
        ) +
        linkRow(
          "▤",
          "Proxmox migration",
          "Plan guest and application migrations",
          projectGuides + "migration.md",
        ) +
        linkRow(
          "▤",
          "Quality-of-life roadmap",
          "Priorities, acceptance criteria and declarative boundaries",
          projectGuides + "roadmap.md",
        ) +
        linkRow(
          "▤",
          "Architecture & trade-offs",
          "Why each component was selected",
          projectGuides + "architecture.md",
        ) +
        linkRow(
          "▤",
          "Validation checklist",
          "Verified checks and target-hardware acceptance tests",
          projectGuides + "validation.md",
        ),
    ) +
    card(
      "Project & references",
      linkRow(
        "↗",
        "Nixlab source",
        "Review the declarative configuration",
        projectRepository,
      ) +
        linkRow(
          "↗",
          "Report an issue",
          "Include version and redacted diagnostics",
          projectRepository + "/issues",
        ) +
        linkRow(
          "↗",
          "Incus documentation",
          "Official guest, storage, networking and CLI reference",
          "https://linuxcontainers.org/incus/docs/main/",
        ) +
        linkRow(
          "↗",
          "Cloudflare dashboard",
          "Manage your account, tunnels and DNS",
          "https://dash.cloudflare.com/",
        ),
    ) +
    (apps ? card("Public applications", apps, "Configured hostnames") : "")
  );
}

function flatDisks(devices) {
  return devices.flatMap((d) => [d, ...flatDisks(d.children || [])]);
}
function storage() {
  const disks = flatDisks(state.host.disks || []);
  const rows = state.draft.mounts
    .map(
      (m, i) =>
        `<div class="mount-row"><div class="mount-head"><h3>Filesystem ${i + 1}</h3><button class="small danger" data-remove-mount="${i}">Remove</button></div><div class="fields"><label class="field full"><span>Existing filesystem</span><select data-mount="${i}" data-mount-key="uuid"><option value="">Select a discovered filesystem UUID</option>${disks
          .filter((d) => d.uuid)
          .map(
            (d) =>
              `<option value="${esc(d.uuid)}" ${d.uuid === m.uuid ? "selected" : ""} ${(d.mountpoints || []).some((p) => p && p !== m.path) ? "disabled" : ""}>${esc(d.path)} · ${esc(d.fstype)} · ${formatBytes(d.size)} · ${esc(d.label || d.uuid)}${(d.mountpoints || []).filter(Boolean).length ? " · mounted" : ""}</option>`,
          )
          .join(
            "",
          )}</select></label><label class="field"><span>Mount name</span><input data-mount="${i}" data-mount-key="name" value="${esc(m.name)}"></label><label class="field"><span>Use this filesystem for</span><select data-mount="${i}" data-mount-key="role">${[
          ["data", "General data"],
          ["guests", "Primary guest storage"],
          ["pool", "Additional Incus pool"],
          ["backup", "Backup staging"],
        ]
          .map(
            ([v, t]) =>
              `<option value="${v}" ${v === m.role ? "selected" : ""}>${t}</option>`,
          )
          .join(
            "",
          )}</select></label></div><p class="note mono">${esc(m.path || "/srv/nixlab/" + m.name)}</p><label class="toggle"><input type="checkbox" data-mount="${i}" data-mount-key="required" ${m.required ? "checked" : ""} ${m.role !== "data" ? "disabled" : ""}> Required for storage readiness / dependent services</label><label class="toggle"><input type="checkbox" data-mount="${i}" data-mount-key="readOnly" ${m.readOnly ? "checked" : ""} ${m.role !== "data" ? "disabled" : ""}> Read-only data mount</label></div>`,
    )
    .join("");
  return (
    card(
      "Detected drives",
      `<div class="table-wrap"><table><thead><tr><th>Device</th><th>Size</th><th>Filesystem</th><th>Mounts</th></tr></thead><tbody>${disks.map((d) => `<tr><td><b>${esc(d.path)}</b><br><code>${esc(d.uuid || d.type)}</code></td><td>${formatBytes(d.size)}</td><td>${esc(d.fstype || "—")}</td><td>${esc((d.mountpoints || []).filter(Boolean).join(", ") || "Not mounted")}</td></tr>`).join("")}</tbody></table></div>`,
      "Read-only discovery",
    ) +
    `<section class="card section"><div class="card-header"><h2>Declared mounts</h2><button data-action="add-mount">＋ Add filesystem</button></div><p class="section-intro">Mounts use <code>/srv/nixlab/&lt;name&gt;</code>. Guest and backup storage need writable Linux filesystems. Initialized Incus pools require a separate migration before moving them.</p>${rows || '<div class="empty">No additional disks selected. Guest data will use the system filesystem.</div>'}<div class="separator"></div><p class="note">This wizard mounts existing filesystems. Disk partitioning, formatting, encrypted-volume enrollment and ZFS pool creation use a separately reviewed storage workflow. Local backup staging still needs an independent backup destination.</p></section>`
  );
}
function apps() {
  return card(
    "Optional Docker/Portainer integration",
    `${toggle("Enable the isolated applications VM", "apps.enable")}<div class="fields">${field("Portainer HTTPS port on the host", "apps.portainerPort", { type: "number", hint: "Use 9443 for the homelab stack. Existing installations retain 9441 until changed." })}${field("Portainer image", "apps.portainerImage")}${secret("Initial Portainer administrator password", "portainerPassword", "Existing application passwords are changed inside Portainer.")}</div>${toggle("Enable the optional demo workload", "apps.enableDemo")}${state.draft.apps.enableDemo ? secret("Demo database password", "demoPassword") : ""}<p>Paste Compose YAML into Portainer’s stack editor. Docker stays inside the apps VM; Incus manages system containers and VMs. The bundled demo remains Nix-owned.</p>`,
  );
}
function security() {
  return (
    title(
      "MANAGEMENT ACCESS",
      "Private by default.",
      "SSH keys for the host, certificate identities for Incus, and application-only public ingress.",
    ) +
    `<div class="grid">${card("SSH administration", `<div class="fields">${field("Operator SSH public keys", "adminKeys", { type: "textarea", list: true, full: true, placeholder: "ssh-ed25519 AAAA… your-device", hint: "One public key per line. Private keys never belong here." })}</div><p class="note">The operator account can administer the platform through sudo. Existing local accounts remain available from the installed configuration.</p><button data-action="certificate" ${!state.status.setupComplete ? "disabled" : ""}>Create an Incus browser identity</button><div id="certificate-result"></div>`)}${card("Cloudflare Tunnel", `${toggle("Enable a locally managed tunnel", "tunnel.enable")}<div class="fields">${field("Existing tunnel UUID", "tunnel.uuid", { full: true })}<label class="field full"><span>Hostname → internal application URL</span><textarea data-ingress spellcheck="false" placeholder='{"app.example.com":"http://GUEST_IP:8080"}'>${esc(JSON.stringify(state.draft.tunnel.ingress, null, 2))}</textarea><small>Nix owns ingress rules. Account, tunnel and DNS creation stay in Cloudflare.</small></label>${secret("Tunnel-specific credentials JSON", "tunnelCredentials", "Runtime credentials stay outside Git and the Nix store.", true)}</div><p class="note">Incus, SSH and dashboard origins are excluded from public ingress. Protect public applications with their own authentication or Cloudflare Access.</p>`)}</div>`
  );
}
function system() {
  const wifiChoices = [
    { value: "", label: "Select Wi-Fi interface" },
    ...state.host.interfaces
      .filter((i) => i.wireless)
      .map((i) => ({ value: i.name, label: i.name })),
  ];
  return (
    title(
      "HOST PREFERENCES",
      "Make the host fit its home.",
      "Radio controls and maintenance settings use separate Nix modules and the same reviewed deployment path.",
    ) +
    `<div class="grid">${card("Identity & maintenance", `<div class="fields">${field("Host name", "hostName")}${field("Time zone", "maintenance.timeZone", { placeholder: "Region/City" })}${field("Setup HTTPS port", "setup.port", { type: "number" })}${field("Confirmation window, seconds", "setup.confirmationSeconds", { type: "number", hint: "300–1800 seconds after activation completes." })}</div><div class="separator"></div>${toggle("Enable scheduled filesystem TRIM", "maintenance.trim")}${toggle("Deduplicate the Nix store weekly", "maintenance.optimiseStore")}${toggle("Laptop server: keep running with lid closed", "maintenance.laptopMode")}<p class="note">Previous OS generations remain available. Updates and reboots are initiated explicitly.</p><button data-action="export-settings">Export desired settings JSON</button>`)}${card("Wi-Fi & Bluetooth", `${toggle("Enable Wi-Fi", "radios.wifi.enable")}<div class="fields">${field("Wi-Fi interface", "radios.wifi.interface", { options: wifiChoices, full: true })}</div>${toggle("Declare a Wi-Fi connection profile", "radios.wifi.manageProfile")}<div class="fields">${field("Network name / SSID", "radios.wifi.ssid", { full: true })}${secret("WPA password", "wifiPassword", "WPA personal networks are supported. Existing NetworkManager profiles can be retained without entering a new password.")}</div><div class="separator"></div>${toggle("Enable Bluetooth and power it on at boot", "radios.bluetooth.enable")}<p class="note">Wi-Fi uplinks work with the private routed/NAT guest bridge. The physical Wi-Fi interface is never enslaved into an Ethernet bridge.</p>`)}${card("Change portal password", '<form id="password-form"><div class="fields"><label class="field full"><span>Current password</span><input name="current" type="password" autocomplete="current-password" required></label><label class="field full"><span>New password</span><input name="password" type="password" autocomplete="new-password" minlength="16" required></label></div><div class="actions"><button>Update password & sign out</button></div></form>')}${card("Monitoring history", `${toggle("Collect host metrics for external monitoring", "monitoring.collection")}${toggle("Enable local Prometheus history and alert rules", "monitoring.history")}<div class="fields">${field("Retention, days", "monitoring.retentionDays", { type: "number", hint: "1–90 days; default 14." })}${field("Metrics interval, seconds", "monitoring.scrapeSeconds", { type: "number", hint: "15–300 seconds; default 60." })}</div><p class="note">Off by default to save memory and disk writes. Incus live resource views, systemd health and journal logs remain available. Metrics collection is optional. Prometheus alert rules are evaluated only while history is enabled; external notifications need an alert receiver.</p>`)}${card("Package maintenance", `<p>The repository uses a lock file on the NixOS 26.05 stable branch. You can include a package refresh in the next build and inspect the resulting activation plan before switching.</p><label class="toggle"><input type="checkbox" id="update-inputs" ${state.updateInputs ? "checked" : ""}> Refresh pinned Nixpkgs for the next review</label><p class="note">Guest database migrations and mutable data have their own recovery requirements. An OS rollback restores the OS generation and settings.</p><button data-go="review">Build a maintenance review →</button>`)}</div>`
  );
}
function files() {
  return (
    title(
      "ADMINISTRATOR CONFIGURATION",
      "Add a configuration file",
      "Nix modules can define root services. Upload only code you trust; never include credentials.",
    ) +
    (state.status.layoutVersion === 0
      ? card(
          "Upgrade the local configuration layout",
          `<p>Move legacy settings and modules into the local machine directory. Original files are retained. Finish or discard a candidate first.</p><label class="field"><span>Administrator password</span><input id="layout-password" type="password" autocomplete="current-password"></label><button data-action="migrate-layout">Migrate local configuration</button>`,
        )
      : "") +
    card(
      "Stage a Nix module",
      `<label class="field"><span>Nix file</span><input type="file" id="module-file" accept=".nix"></label><label class="field"><span>Relative destination (optional)</span><input id="module-path" placeholder="disks/archive.nix"></label><button data-action="stage-module">Stage file for review</button><p>Files are imported under ${esc(state.status.moduleDirectory || "site-modules/")} after activation. Nothing is changed on the host while staging. Existing storage must be migrated before changing its backing configuration.</p><pre class="log">${esc(JSON.stringify(state.modules, null, 2))}</pre><button data-action="clear-modules">Clear staged files</button>`,
    )
  );
}
function moduleReview() {
  const pending = Object.keys(state.modules);
  const recorded = state.status.job?.moduleChanges || [];
  return card(
    "Nix module changes",
    `${pending.length ? `<p>${pending.length} staged file(s). Reauthenticate to build these privileged configuration changes.</p><label class="field"><span>Administrator password</span><input id="module-password" type="password" autocomplete="current-password"></label><label class="toggle"><input type="checkbox" id="module-ack"> I authorize these modules as privileged system configuration.</label>` : ""}${recorded.map((c) => `<h3>${esc(c.path)}</h3><p>Previous file</p><pre class="log">${esc(c.before ?? "(new file)")}</pre><p>Candidate file</p><pre class="log">${esc(c.after)}</pre>`).join("")}`,
  );
}
function review() {
  const job = state.status.job;
  const building =
    job && ["building", "applying", "rolling-back"].includes(job.status);
  return (
    title(
      "REPRODUCIBLE DEPLOYMENT",
      "Review. Build. Make it live.",
      "The server validates structured settings, evaluates Nix, builds an immutable candidate and shows its activation output.",
    ) +
    moduleReview() +
    platformReview() +
    `<section class="card"><div class="card-header"><div class="status-line"><span class="progress-dot"></span><h2>${job ? esc(job.status.replaceAll("-", " ")) : "Ready to prepare your first generation"}</h2></div>${job ? `<span class="caption mono">${esc(job.id.slice(0, 10))}</span>` : ""}</div><p>Build before activating. A separate recovery service restores the previous generation if the change fails or you do not confirm access in time.</p><div class="actions"><button class="primary" data-action="build" ${building || state.status.pending ? "disabled" : ""}>${job ? "Build a new review" : "Build & review configuration"}</button>${job?.status === "ready" ? '<button class="primary" data-action="apply">Activate reviewed generation</button>' : ""}${job && ["building", "ready", "failed"].includes(job.status) ? '<button data-action="cancel">Discard candidate</button>' : ""}${state.status.lastConfirmed && state.status.lastConfirmed.id !== job?.id ? '<button data-action="rollback-confirmed">Roll back last confirmed change</button>' : ""}${job?.status === "awaiting-confirmation" ? '<button class="primary" data-action="confirm">Confirm access</button>' : ""}${job?.previousSystem && ["confirmed", "awaiting-confirmation", "failed"].includes(job.status) ? '<button data-action="rollback">Roll back this change</button>' : ""}</div>${state.updateInputs ? '<p class="note">This review includes a Nixpkgs package refresh.</p>' : ""}${job?.error ? `<p class="error">${esc(job.error)}</p>` : ""}</section>${job ? `<section class="card section"><div class="card-header"><h2>Settings in this candidate</h2><span class="caption">${job.changes.length} change(s)</span></div>${job.changes.map((d) => `<div class="diff"><code>${esc(d.setting)}</code><span class="before">${esc(JSON.stringify(d.before))}</span><span class="after">${esc(JSON.stringify(d.after))}</span></div>`).join("") || "<p>No settings changed; the build can still include package updates.</p>"}${job.secretChanges.length ? `<p class="note">Runtime secrets updated: ${esc(job.secretChanges.join(", "))}. Values are excluded from the Nix snapshot and this review.</p>` : ""}<div class="separator"></div><p>Management address after activation: <a href="https://${esc((job.effectiveSettings || job.settings).management.address)}:${esc((job.effectiveSettings || job.settings).setup.port)}">https://${esc((job.effectiveSettings || job.settings).management.address)}:${esc((job.effectiveSettings || job.settings).setup.port)}</a></p></section><section class="card section"><div class="card-header"><h2>Build & activation output</h2><span class="caption">Last 64 KiB</span></div><pre class="log" id="build-log">${esc(job.log || "Waiting for the build worker…")}</pre></section>` : ""}`
  );
}
function formatBytes(v) {
  return Number(v) >= 1024 ** 3
    ? (Number(v) / 1024 ** 3).toFixed(1) + " GiB"
    : (Number(v) / 1024 ** 2).toFixed(0) + " MiB";
}
function login(message = "") {
  clearInterval(state.timer);
  state.timer = null;
  app.innerHTML = `<main class="login"><section class="login-card"><div class="brand"><span class="mark">N</span><div>nixlab<small>HOMELAB CONTROL</small></div></div><div class="eyebrow">YOUR PRIVATE WORKSPACE</div><h1>Welcome to your homelab.</h1><p>Sign in with the setup password printed by the installer.</p><form id="login-form"><label class="field"><span>Account</span><input name="username" value="admin" autocomplete="username" required></label><label class="field"><span>Administrator password</span><input name="password" type="password" autocomplete="current-password" required autofocus></label><p class="error" id="login-error">${esc(message)}</p><button class="primary">Sign in →</button></form><p class="note">Verify the local CA fingerprint from the installer before signing in. <a href="/ca.crt">Download the management CA</a>.</p></section></main>`;
  document.querySelector("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button");
    button.disabled = true;
    try {
      state.principal = await api(
        "login",
        Object.fromEntries(new FormData(e.target)),
      );
      state.csrf = state.principal.csrf;
      await load();
    } catch (err) {
      document.querySelector("#login-error").textContent = err.message;
      button.disabled = false;
    }
  };
}
async function load() {
  state.status = await api("status");
  state.host = await api("inventory");
  try {
    operationView.health = await api("health");
  } catch (_error) {
    operationView.health = null;
  }
  state.draft = copy(state.status.settings);
  state.page = state.status.pending ? "review" : "overview";
  shell();
  if (!state.timer) state.timer = setInterval(poll, 3000);
}
let lastOverviewHealthPoll = 0;
async function poll() {
  try {
    if (state.page === "operations") await refreshOperations(false);
    let healthChanged = false;
    if (state.page === "overview" && Date.now() - lastOverviewHealthPoll >= 30000) {
      lastOverviewHealthPoll = Date.now();
      const previous = JSON.stringify(operationView.health);
      operationView.health = await api("health");
      healthChanged = previous !== JSON.stringify(operationView.health);
    }
    const before = JSON.stringify(state.status?.job);
    state.status = await api("status");
    if (state.page === "review" && before !== JSON.stringify(state.status.job))
      shell();
    else if (state.page === "overview" && healthChanged) shell();
    countdown();
  } catch (err) {
    if (!document.querySelector("#login-form"))
      toast(
        "Connection changed. Open the new management address and sign in to confirm.",
      );
  }
}
function countdown() {
  const el = document.querySelector("#countdown");
  if (el && state.status?.pending) {
    const seconds = Math.max(
      0,
      Math.ceil(state.status.pending.deadline - Date.now() / 1000),
    );
    el.textContent = `Recovery deadline: ${Math.floor(seconds / 60)}m ${seconds % 60}s.`;
  }
}
setInterval(countdown, 1000);

(async () => {
  try {
    state.principal = await api("session");
    state.csrf = state.principal.csrf;
    await load();
  } catch {
    login();
  }
})();

