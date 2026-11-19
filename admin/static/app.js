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
// Page IDs continue to match the existing renderers and API handlers.
const pages = [
  ["overview","Overview"],["projects","Projects"],["guests","Guests"],["network","Networking"],["storage","Storage"],
  ["hardware","Hardware"],["integrations","Services"],["security","Access"],
  ["accounts","Accounts"],["ingress","Public ingress"],["operations","Backups"],
  ["review","Changes"],["system","Maintenance"],["files","Advanced"],["commands","Help"],
];
const navigationGroups = [
  ["WORKSPACE",["overview","projects","guests"]],
  ["INFRASTRUCTURE",["network","storage","hardware","integrations"]],
  ["SECURITY",["security","accounts","ingress"]],
  ["OPERATIONS",["operations","review","system","files"]],
  ["RESOURCES",["commands"]],
];
const pageIcons = {overview:"◫",projects:"▧",guests:"▤",network:"◎",storage:"▱",hardware:"⌘",integrations:"▦",security:"◇",accounts:"♧",ingress:"↗",operations:"◷",review:"⇄",system:"⚙",files:"{ }",commands:"?"};
function primaryPage() { return state.page; }
function hostTabs() { return ""; }
function validPage(id) { return pages.some(([page]) => page === id); }
function selectedHash() {
  try { const id = decodeURIComponent(window.location.hash.slice(1)); return validPage(id) ? id : "overview"; }
  catch { return "overview"; }
}
function navigateTo(id, replace = false) {
  if (!validPage(id)) return;
  const jump = document.querySelector("#ui-jump");
  if (jump?.open) jump.close();
  state.page = id;
  if (window.history) window.history[replace ? "replaceState" : "pushState"](null, "", "#" + id);
  shell();
  window.scrollTo(0, 0);
}
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function brandLogo() {
  return '<div class="brand" aria-label="Nixlab host console"><span class="brand-mark" aria-hidden="true">[n]</span><div class="brand-wordmark">nixlab<span class="brand-cursor" aria-hidden="true">_</span><small>HOST CONSOLE</small></div></div>';
}
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
const pageGuides = {overview:"start.md",guests:"networking.md",network:"networking.md",storage:"hardware.md",hardware:"hardware.md",integrations:"homelab.md",security:"administration.md",accounts:"administration.md",ingress:"networking.md",system:"setup.md",files:"file-modules.md",review:"setup.md",operations:"recovery.md",commands:"helpers.md"};
const title = (eyebrow, heading, text) =>
  `<div class="page-title"><div><h1>${heading}</h1><p>${text}</p><a class="page-guide" href="/docs/${pageGuides[state.page] || "setup.md"}" target="_blank" rel="noopener noreferrer">Guide</a></div>${["overview", "guests", "accounts", "operations", "review", "commands"].includes(state.page) ? "" : '<button class="primary" data-go="review">Review changes ↗</button>'}</div>`;
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
  return `<div class="banner warn status-banner"><div><h3>Confirm your connection after activation</h3><p>${j?.status === "awaiting-confirmation" ? "Sign out, reconnect and sign in again after activation before confirming; the previous generation will be restored otherwise." : "Activation is in progress; recovery is armed."} <span id="countdown"></span></p></div><button class="primary" data-action="confirm" ${j?.status !== "awaiting-confirmation" ? "disabled" : ""}>Confirm access</button></div>`;
}
function quickManagementLinks() {
  const links = managementServices().filter(service => service.url);
  return links.length ? `<div class="sidebar-shortcuts"><div class="nav-label">MANAGE</div>${links.map(service => `<a href="${esc(service.url)}" target="_blank" rel="noopener noreferrer">${esc(service.name)}</a>`).join("")}</div>` : "";
}

function draftChanges() {
  if (!state.draft || !state.status?.settings) return [];
  const differences = [];
  const walk = (before, after, path) => {
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    if (before && after && typeof before === "object" && typeof after === "object" && !Array.isArray(before) && !Array.isArray(after)) {
      for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) walk(before[key], after[key], path ? path + "." + key : key);
    } else differences.push(path);
  };
  walk(state.status.settings, state.draft, "");
  return differences;
}
function platformUpdateSelected() { return !!state.platformRevision && state.platformRevision !== state.status.release?.revision; }
function reviewScope() { return JSON.stringify([state.principal?.username, state.platformRevision, state.modules]); }
let renderedReviewScope = null;
function stagedCount() { return draftChanges().length + Object.keys(state.modules).length + Object.values(state.secrets).filter(Boolean).length + (state.updateInputs ? 1 : 0) + (platformUpdateSelected() ? 1 : 0); }
function stagedBanner() {
  const n = stagedCount();
  return n ? `<div class="draft-bar" role="status"><div><strong>${n} staged change${n === 1 ? "" : "s"}</strong><span>Not applied to your host</span></div><div class="draft-actions"><button class="small" data-ui-discard>Discard drafts</button><button class="primary" data-go="review">Review changes</button></div></div>` : "";
}
function navigationHtml() {
  return navigationGroups.map(([heading, ids]) =>
    `<div class="nav-group"><div class="nav-label">${heading}</div><nav class="nav" aria-label="${heading.toLowerCase()}">${ids.map(id => {
      const label = pages.find(([key]) => key === id)[1];
      return `<button type="button" data-go="${id}" class="${state.page === id ? "active" : ""}" ${state.page === id ? 'aria-current="page"' : ""}><span class="nav-symbol" aria-hidden="true">${pageIcons[id]}</span><span>${label}</span>${id === "review" && stagedCount() ? `<span class="nav-count">${stagedCount()}</span>` : ""}</button>`;
    }).join("")}</nav></div>`).join("");
}
function bindUiChrome() {
  const sidebar = document.querySelector("#ui-sidebar");
  const shade = document.querySelector("#ui-shade");
  const navToggle = document.querySelector("#ui-nav-toggle");
  const closeNav = () => { sidebar?.classList.remove("open"); shade?.classList.remove("open"); navToggle?.setAttribute("aria-expanded", "false"); };
  navToggle?.addEventListener("click", () => {
    const open = sidebar.classList.toggle("open");
    shade.classList.toggle("open", open);
    navToggle.setAttribute("aria-expanded", String(open));
  });
  shade?.addEventListener("click", closeNav);
  const dialog = document.querySelector("#ui-jump");
  const search = dialog?.querySelector("#ui-search-input");
  const openSearch = () => { if (!dialog.open) dialog.showModal(); search.value = ""; search.dispatchEvent(new Event("input")); search.focus(); };
  document.querySelector("#ui-search-open")?.addEventListener("click", openSearch);
  search?.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    dialog.querySelectorAll("[data-go]").forEach(btn => { btn.hidden = !btn.textContent.toLowerCase().includes(query); });
  });
  document.querySelector("#ui-theme-toggle")?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("nixlab-theme", next); } catch {}
  });
  document.querySelector("[data-ui-discard]")?.addEventListener("click", () => {
    if (!window.confirm("Discard all staged settings, uploaded modules and pending secret input?")) return;
    state.draft = copy(state.status.settings);
    state.modules = {}; state.secrets = {}; state.updateInputs = false; state.platformRevision = "";
    shell();
  });
  // A per-render handler is used so detached dialogs do not retain listeners.
  document.onkeydown = event => {
    if (event.key === "Escape") closeNav();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !event.repeat) {
      event.preventDefault(); openSearch();
    }
  };
}
function shell() {
  // Keep authorization only in live controls, never in app state or storage.
  // Restore across a synchronous rerender only while the reviewed code is unchanged.
  const scope = state.page === "review" && administrator() ? reviewScope() : null;
  const password = document.querySelector("#platform-password");
  const approval = document.querySelector("#platform-ack");
  const authorization = scope !== null && scope === renderedReviewScope && password && approval
    ? {password: password.value, approved: approval.checked} : null;
  const settings = state.status.effectiveSettings || state.status.settings;
  const current = pages.find(([id]) => id === state.page)?.[1] || "Overview";
  const address = state.status.managementAddress || settings.management.address;
  const observedHealth = freshObservation(operationView.health);
  const requiredUnits = ["incus.service", "nixlab-admin-backend.service", "nixlab-ui.service",
    ...(settings.cockpit?.enable ? ["cockpit.socket"] : [])];
  const units = operationView.health?.services || [];
  const knownHealth = observedHealth && units.length > 0;
  const live = knownHealth && requiredUnits.every(id => units.some(u => u.Id === id && u.ActiveState === "active"));
  const hostStatus = !knownHealth ? "Unknown" : live ? "Active" : "Needs attention";
  app.innerHTML = `<div class="layout"><div id="ui-shade" class="drawer-shade" aria-hidden="true"></div>
  <aside class="sidebar" id="ui-sidebar" aria-label="Host console navigation">
    ${brandLogo()}<div class="sidebar-navigation">${navigationHtml()}</div>${quickManagementLinks(settings)}
    <div class="sidebar-bottom"><div class="sidebar-server"><span class="status-dot ${live ? "active" : knownHealth ? "warning" : "unknown"}" aria-hidden="true"></span><div><b>${esc(settings.hostName)}</b><span class="host-state">${esc(hostStatus)}</span><span>NixOS host · ${esc(address)}</span></div></div>
      <div class="sidebar-links"><a href="/docs/troubleshooting.md">Offline help</a><a href="https://nixlab.dardery.work/" target="_blank" rel="noopener noreferrer">Website ↗</a></div><button class="small" data-action="logout">Sign out</button>
    </div>
  </aside>
  <div class="content"><header class="topbar"><div class="topbar-left"><button class="mobile-nav-toggle" type="button" id="ui-nav-toggle" aria-label="Open navigation" aria-controls="ui-sidebar" aria-expanded="false">☰</button><div class="breadcrumb">NixLab <span class="muted">/</span> <b>${esc(current)}</b></div></div>
    <div class="top-actions"><button type="button" class="search-trigger" id="ui-search-open" aria-label="Search console pages">⌕ <span>Search pages</span> <kbd>Ctrl K</kbd></button>
      <button class="theme-toggle" id="ui-theme-toggle" aria-label="Toggle light and dark theme" type="button">◐</button>
      <a class="pill" href="/docs/beta.md">${esc(state.status.release?.version || "Development")}</a>
      <span class="principal" title="Signed-in portal identity">${esc(state.principal.username)} · ${esc(state.principal.role)}</span></div>
  </header><main id="main" class="page" tabindex="-1">${pendingBanner()}${ownershipBanner()}${renderPage()}${stagedBanner()}
     <footer class="footer-note">NixOS owns the host · Incus owns guests · <a href="/docs/projects.md">Upstream credits</a></footer>
  </main></div></div>
  <dialog id="ui-jump" class="ui-dialog" aria-label="Navigate to a console page"><div class="ui-dialog-heading"><strong>Go to a page</strong><form method="dialog"><button aria-label="Close page search" type="submit">✕</button></form></div><label for="ui-search-input">Search navigation</label><input id="ui-search-input" type="search" placeholder="Guests, backups, access…" autocomplete="off"><nav aria-label="Search results">${pages.map(([id,label]) => `<button type="button" data-go="${id}">${esc(label)}</button>`).join("")}</nav></dialog>`;
  renderedReviewScope = scope;
  if (authorization) {
    document.querySelector("#platform-password").value = authorization.password;
    document.querySelector("#platform-ack").checked = authorization.approved;
  }
  bind();
  bindOperations();
  bindNativeProjects();
  bindFoundation();
  bindCommands();
  bindUiChrome();
  countdown();
}
function renderPage() {
  return {
    overview: hostOverview,
    projects: nativeProjectPage,
    guests: () => title("", "Guests & addresses", "Observed Incus guests and IP addresses. Manage guest lifecycles in Incus.") + guestNetwork(),
    network: hostConnectivity,
    hardware: hostHardware,
    storage: () => title("", "Storage & mounts", "Discovered disks, existing filesystems and staged Nix-managed mounts.") + storage(),
    integrations,
    apps: integrations,
    security,
    ingress: ingressPage,
    accounts: () => title("", "Portal accounts", "Portal permissions are separate from Linux accounts and Incus certificates.") + (administrator() ? accountCard() : card("Account permissions", "<p>Only administrators can manage portal accounts.</p>")),
    system: () => system() + card("Host health", healthSummary()),
    files,
    review,
    operations: operationsPage,
    commands: commandsPage,
  }[state.page]();
}

function linkRow(icon, heading, detail, url, label = "Open ↗", docs = "") {
  return `<div class="service-row">${icon ? `<span class="service-icon" aria-hidden="true">${esc(icon)}</span>` : ""}<div class="service-copy"><h3>${esc(heading)}</h3><small>${esc(detail)}</small>${docs ? `<a class="service-doc" href="${esc(docs)}" target="_blank" rel="noopener noreferrer">Docs</a>` : ""}</div><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a></div>`;
}
function homepageTools() {
  const tools = managementServices().filter(service => service.url);
  const rows = tools.map(service => linkRow("", service.name, service.detail, service.url, "Open", service.docs[0][1])).join("");
  return card("Manage", (rows || (state.status.setupComplete ? '<p>Management endpoints unavailable. Check host addressing.</p>' : '<p>Native tools become available after the first activation.</p><button data-go="review">Finish setup</button>')) + '<div class="service-shortcuts"><button class="small" data-go="integrations">Service settings</button><button class="small" data-go="security">Incus browser access</button></div>', "Enabled tools");
}

function publicApplications() {
  const active = state.status.effectiveSettings || state.status.settings;
  const apps = active.tunnel.enable ? Object.keys(active.tunnel.ingress)
    .filter(name => /^[a-zA-Z0-9][a-zA-Z0-9.-]*$/.test(name) && !name.includes("*"))
    .map(name => linkRow("↗", name, "Configured public application; availability is not checked", "https://" + name)).join("") : "";
  return apps ? card("Public applications", apps, "Configured hostnames") : "";
}
function homepageResources() {
  return referenceCatalog() + publicApplications();
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
  return card("Incus-native applications",
    '<p>All application workloads belong to Incus projects. Use the Projects page to see observed resources.</p><button type="button" data-go="projects">Open Projects</button><p class="note">Native deployment requires an approved revision and is not implemented in this portal yet.</p>');
}

function security() {
  return (
    title(
      "MANAGEMENT ACCESS",
      "Private by default.",
      "Linux password or SSH key access for the host, certificate identities for Incus, and application-only public ingress.",
    ) +
    `<div class="grid">${card("SSH administration", `<div class="fields">${toggle("Allow SSH password login for non-root Linux accounts", "access.sshPasswordLogin")}${field("Operator SSH public keys", "adminKeys", { type: "textarea", list: true, full: true, placeholder: "ssh-ed25519 AAAA… your-device", hint: "One public key per line. Private keys never belong here." })}</div><p class="note">The operator account can administer the platform through sudo. Existing local accounts remain available from the installed configuration.</p><div class="fields"><label class="field"><span>Incus browser device name</span><input id="certificate-device" maxlength="32" autocomplete="off"></label><label class="field"><span>Bundle passphrase (at least 16 characters)</span><input id="certificate-passphrase" type="password" minlength="16" maxlength="256" autocomplete="new-password"></label></div><input id="certificate-renew" type="hidden"><div class="fields"><label class="field"><span>Current NixLab password</span><input id="certificate-current" type="password" autocomplete="current-password"></label><label class="field"><span><input id="certificate-full-admin" type="checkbox"> Grant unrestricted Incus host administration (advanced)</span></label></div><p class="note">Default: lab project only, 90 days. The encrypted bundle is available once for two minutes in this browser session. Renewal creates a new identity; the previous one remains trusted until you revoke it.</p><button data-action="certificate" ${!state.status.setupComplete ? "disabled" : ""}>Create an Incus browser identity</button><button data-action="certificate-download">Claim pending download</button><button data-action="certificate-list">Refresh browser identities</button><p class="note">Enter your current password above to issue or revoke. Older identities created outside this portal remain managed through Incus.</p><div id="certificate-result" aria-live="polite"></div><div id="certificate-list"></div>`)}</div>`
  );
}
function ingressPage() {
  return title("", "Public application ingress", "Configure application-only ingress. NixLab, SSH, Cockpit and Incus administration must remain private.")
    + `<div class="grid">${card("Cloudflare Tunnel", `
      ${toggle("Enable a locally managed tunnel", "tunnel.enable")}
      <div class="fields">${field("Existing tunnel UUID", "tunnel.uuid", {full:true})}
      <label class="field full"><span>Hostname → internal application URL</span>
      <textarea data-ingress spellcheck="false" placeholder='{"app.example.com":"http://GUEST_IP:8080"}'>${esc(JSON.stringify(state.draft.tunnel.ingress, null, 2))}</textarea>
      <small>Cloudflare owns the account and DNS; NixLab stages the ingress rules.</small></label>
      ${secret("Tunnel-specific credentials JSON", "tunnelCredentials", "Secrets stay outside Git and the Nix store.", true)}</div>
      <p class="note">Do not expose private management services. Protect public apps with their own authentication or Cloudflare Access.</p>
    `)}</div>` + publicApplications();
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
      "Host preferences",
      "Radio controls and maintenance settings use separate Nix modules and the same reviewed deployment path.",
    ) +
    `<div class="grid">${card("Identity & maintenance", `<div class="fields">${field("Host name", "hostName")}${field("Time zone", "maintenance.timeZone", { placeholder: "Region/City" })}${field("Setup HTTPS port", "setup.port", { type: "number" })}${field("Confirmation window, seconds", "setup.confirmationSeconds", { type: "number", hint: "300–1800 seconds after activation completes." })}</div><div class="separator"></div>${toggle("Enable scheduled filesystem TRIM", "maintenance.trim")}${toggle("Deduplicate the Nix store weekly", "maintenance.optimiseStore")}${toggle("Laptop server: keep running with lid closed", "maintenance.laptopMode")}<p class="note">Previous OS generations remain available. Updates and reboots are initiated explicitly.</p><button data-action="export-settings">Export desired settings JSON</button>`)}${card("Wi-Fi & Bluetooth", `${toggle("Enable Wi-Fi", "radios.wifi.enable")}<div class="fields">${field("Wi-Fi interface", "radios.wifi.interface", { options: wifiChoices, full: true })}</div>${toggle("Declare a Wi-Fi connection profile", "radios.wifi.manageProfile")}<div class="fields">${field("Network name / SSID", "radios.wifi.ssid", { full: true })}${secret("WPA password", "wifiPassword", "WPA personal networks are supported. Existing NetworkManager profiles can be retained without entering a new password.")}</div><div class="separator"></div>${toggle("Enable Bluetooth and power it on at boot", "radios.bluetooth.enable")}<p class="note">Wi-Fi uplinks work with the private routed/NAT guest bridge. The physical Wi-Fi interface is never enslaved into an Ethernet bridge.</p>`)}${card("Change portal password", '<form id="password-form"><div class="fields"><label class="field full"><span>Current password</span><input name="current" type="password" autocomplete="current-password" required></label><label class="field full"><span>New password</span><input name="password" type="password" autocomplete="new-password" minlength="16" required></label></div><div class="actions"><button>Update password & sign out</button></div></form>')}${card("Monitoring history", `${toggle("Collect host metrics for external monitoring", "monitoring.collection")}${toggle("Enable local Prometheus history and alert rules", "monitoring.history")}<div class="fields">${field("Retention, days", "monitoring.retentionDays", { type: "number", hint: "1–90 days; default 14." })}${field("Metrics interval, seconds", "monitoring.scrapeSeconds", { type: "number", hint: "15–300 seconds; default 60." })}</div><p class="note">Off by default to save memory and disk writes. Incus live resource views, systemd health and journal logs remain available. Metrics collection is optional. Prometheus alert rules are evaluated only while history is enabled; external notifications need an alert receiver.</p>`)}${card("Package maintenance", `<p>The repository uses a lock file on the NixOS 26.05 stable branch. You can include a package refresh in the next build and inspect the resulting activation plan before switching.</p><label class="toggle"><input type="checkbox" id="update-inputs" ${state.updateInputs ? "checked" : ""}> Refresh pinned Nixpkgs for the next review</label><p class="note">Guest database migrations and mutable data have their own recovery requirements. An OS rollback restores the OS generation and settings.</p><button data-go="review">Build a maintenance review →</button>`)}</div>`
  );
}
function files() {
  return (
    title(
      "ADMINISTRATOR CONFIGURATION",
      "Configuration files",
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
    `${pending.length ? `<p>${pending.length} staged file(s). Reauthenticate to build these privileged configuration changes.</p>Use the privileged code approval below.` : ""}${recorded.map((c) => `<h3>${esc(c.path)}</h3><p>Previous file</p><pre class="log">${esc(c.before ?? "(new file)")}</pre><p>Candidate file</p><pre class="log">${esc(c.after)}</pre>`).join("")}`,
  );
}
function deploymentTimeline() {
  const job = state.status?.job;
  const phase = !job ? 0 : ({
    queued:1, building:1, ready:2, applying:2, activating:2,
    "awaiting-confirmation":3, confirmed:4,
    failed:2, "rolling-back":3, "rolled-back":0, cancelled:0
  })[job.status] ?? 0;
  const guestPlan = job?.guestPlan?.targets || [];
  const deployed = job?.guestDeployment?.status === "complete" || guestPlan.length === 0;
  const steps = [
    ["Stage edits", "Settings are saved only as a local draft."],
    ["Build & review", "Validate Nix configuration and inspect impact."],
    ["Activate host", "Switch the reviewed system generation."],
    ["Confirm access", "Reconnect from a fresh session before rollback expires."],
    ["Deploy guests", "Optional reviewed guest closures, after host confirmation."],
  ];
  return `<section class="card lifecycle-card" aria-label="Guarded activation workflow">
    <div class="card-header"><h2>Deployment workflow</h2><span class="caption">${job ? esc(job.status.replaceAll("-", " ")) : "No candidate yet"}</span></div>
    <ol class="lifecycle">${steps.map(([label, detail], i) => {
      const complete = i < phase || (i === 4 && phase === 4 && deployed);
      const active = i === phase && !complete;
      return `<li class="${complete ? "complete" : active ? "current" : ""}" ${active ? 'aria-current="step"' : ""}>
        <span class="step-num" aria-hidden="true">${complete ? "✓" : i + 1}</span>
        <div><strong>${esc(label)}</strong><p>${esc(detail)}</p></div>
      </li>`;
    }).join("")}</ol>
    <p class="note">No activation happens until you explicitly apply a reviewed candidate. Unconfirmed access changes are automatically rolled back.</p>
  </section>`;
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
    deploymentTimeline() +
    moduleReview() +
    settingsMigrationReview() +
    candidateOverridesReview() +
    platformReview() +
    impactReview() +
    guestDeploymentReview() +
    `<section class="card"><div class="card-header"><div class="status-line"><span class="progress-dot"></span><h2>${job ? esc(job.status.replaceAll("-", " ")) : "Ready to prepare your first generation"}</h2></div>${job ? `<span class="caption mono">${esc(job.id.slice(0, 10))}</span>` : ""}</div><p>Build before activating. A separate recovery service restores the previous generation if the change fails or you do not confirm access in time.</p><div class="actions"><button class="primary" data-action="build" ${building || state.status.pending ? "disabled" : ""}>${job ? "Build a new review" : "Build & review configuration"}</button>${job?.status === "ready" ? `<button class="primary" data-action="apply" ${!job.impactPlan || job.impactPlan.blocked ? "disabled" : ""}>Activate reviewed generation</button>` : ""}${job && ["building", "ready", "failed"].includes(job.status) ? '<button data-action="cancel">Discard candidate</button>' : ""}${state.status.lastConfirmed && state.status.lastConfirmed.id !== job?.id ? '<button data-action="rollback-confirmed">Roll back last confirmed change</button>' : ""}${job?.status === "awaiting-confirmation" ? '<button class="primary" data-action="confirm">Confirm access</button>' : ""}${job?.previousSystem && ["confirmed", "awaiting-confirmation", "failed"].includes(job.status) ? '<button data-action="rollback">Roll back this change</button>' : ""}</div>${state.updateInputs ? '<p class="note">This review includes a Nixpkgs package refresh.</p>' : ""}${job?.error ? `<p class="error">${esc(job.error)}</p>` : ""}</section>${job ? `<section class="card section"><div class="card-header"><h2>Settings in this candidate</h2><span class="caption">${job.changes.length} change(s)</span></div>${job.changes.map((d) => `<div class="diff"><code>${esc(d.setting)}</code><span class="before">${esc(JSON.stringify(d.before))}</span><span class="after">${esc(JSON.stringify(d.after))}</span></div>`).join("") || "<p>No settings changed; the build can still include package updates.</p>"}${job.secretChanges.length ? `<p class="note">Runtime secrets updated: ${esc(job.secretChanges.join(", "))}. Values are excluded from the Nix snapshot and this review.</p>` : ""}<div class="separator"></div><p>Management address after activation: <a href="https://${esc((job.effectiveSettings || job.settings).management.address)}:${esc((job.effectiveSettings || job.settings).setup.port)}">https://${esc((job.effectiveSettings || job.settings).management.address)}:${esc((job.effectiveSettings || job.settings).setup.port)}</a></p></section><section class="card section"><div class="card-header"><h2>Build & activation output</h2><span class="caption">Last 64 KiB</span></div><pre class="log" id="build-log">${esc(job.log || "Waiting for the build worker…")}</pre></section>` : ""}`
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
  app.innerHTML = `<main id="main" class="login"><section class="login-card">${brandLogo()}<h1>Sign in to your host.</h1><p>Use your NixLab portal account. Hosts with browser enrollment enabled will show console approval here. If you need a new portal password, use <code>sudo nixlab-admin reset-password</code> from an authorized host terminal.</p><form id="login-form"><label class="field"><span>Account</span><input name="username" value="admin" autocomplete="username" required></label><label class="field"><span>Portal password</span><input name="password" type="password" autocomplete="current-password" required autofocus></label><p class="error" id="login-error">${esc(message)}</p><button class="primary">Sign in →</button></form><p class="note">SSH and Cockpit use separate identities from the NixLab portal. Verify the local CA fingerprint before signing in. <a href="/ca.crt">Download the management CA</a>.</p></section></main>`;
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
  const form = document.querySelector("#login-form");
  api("enrollment").then(({available, recoveryRequired}) => {
    if (!form.isConnected) return;
    if (available) showEnrollment();
    else if (recoveryRequired) document.querySelector("#login-error").textContent = "First-admin enrollment is locked. Use sudo nixlab-admin reset-password from a private host terminal or restore supported account state.";
  }).catch(() => {});
}

async function showEnrollment() {
  let bootstrap;
  try { bootstrap = await api("bootstrap"); }
  catch { return login("Enrollment is unavailable. Refresh or use authorized console recovery."); }
  app.innerHTML = `<main id="main" class="login"><section class="login-card">${brandLogo()}<h1>Verify this machine</h1><p>Verify the management CA before entering credentials. Authenticate an existing local Linux administrator whose sudo policy permits a root shell. This does not link Linux and portal passwords.</p><form id="host-proof-form"><label class="field"><span>Linux administrator account</span><input name="hostUsername" autocomplete="username" required maxlength="32"></label><label class="field"><span>Linux administrator password</span><input name="hostPassword" type="password" autocomplete="current-password" required maxlength="1024"></label><button class="primary">Verify host administrator</button></form><details><summary>Use SSH keys or local console approval instead</summary><p>On the host run <code>sudo nixlab-admin approve-enrollment</code> and enter this browser's code at the private prompt. The command prints no password or bearer token.</p><label class="field"><span>Browser approval code</span><input id="bootstrap-code" readonly autocomplete="off"></label></details><p id="bootstrap-status" role="status"></p><form id="enrollment-form"><label class="field"><span>New NixLab administrator username</span><input name="username" autocomplete="username" pattern="[a-z][a-z0-9_-]{0,31}" maxlength="32" required></label><label class="field"><span>New NixLab password</span><input name="password" type="password" autocomplete="new-password" minlength="16" maxlength="256" required></label><p id="enrollment-error" class="error" aria-live="polite"></p><button class="primary" disabled>Create administrator</button></form><p><a href="/ca.crt">Download the management CA</a></p></section></main>`;
  const form = document.querySelector("#enrollment-form");
  const proof = document.querySelector("#host-proof-form");
  let submitting = false;
  const update = () => {
    document.querySelector("#bootstrap-code").value = bootstrap.approvalCode;
    document.querySelector("#bootstrap-status").textContent = bootstrap.approved
      ? "This browser is approved. Choose your independent NixLab account."
      : "Host approval required. Verification expires after ten minutes and cannot be transferred to another browser.";
    form.querySelector("button").disabled = submitting || !bootstrap.approved;
  };
  update();
  proof.onsubmit = async event => {
    event.preventDefault();
    const button = proof.querySelector("button"); button.disabled = true;
    const password = proof.elements.hostPassword;
    try {
      await api("bootstrap/verify", {hostUsername: proof.elements.hostUsername.value, hostPassword: password.value}, bootstrap.csrf);
      bootstrap = await api("bootstrap");
      if (form.isConnected) update();
    } catch { if (form.isConnected) document.querySelector("#enrollment-error").textContent = "Host verification failed. Check the local sudo administrator password or use console approval; repeated attempts are limited."; }
    finally { password.value = ""; button.disabled = false; }
  };
  form.onsubmit = async event => {
    event.preventDefault(); submitting = true; const button = form.querySelector("button"); button.disabled = true;
    try {
      state.principal = await api("bootstrap/claim", {username: form.elements.username.value, password: form.elements.password.value}, bootstrap.csrf);
      state.csrf = state.principal.csrf; form.elements.password.value = "";
      await load();
    } catch { submitting = false; if (form.isConnected) { document.querySelector("#enrollment-error").textContent = "Enrollment did not complete. Refresh to check account state; if it committed, sign in with your chosen account."; button.disabled = false; } }
  };
  const timer = setInterval(async () => {
    if (!form.isConnected) return clearInterval(timer);
    try {
      const current = await api("bootstrap");
      if (form.isConnected) { bootstrap = current; update(); }
    } catch { if (form.isConnected) document.querySelector("#bootstrap-status").textContent = "Approval unavailable or enrollment completed. Refresh and sign in, or request fresh host approval."; }
  }, 3000);
}

async function load() {
  state.status = await api("status");
  state.host = await api("inventory");
  await refreshOverview();
  state.draft = copy(state.status.settings);
  state.page = state.status.pending ? "review" : selectedHash();
  shell();
  if (!state.timer) state.timer = setInterval(poll, 3000);
}
let lastOverviewHealthPoll = 0;
let overviewLoading = false;
async function refreshOverview() {
  if (overviewLoading) return;
  overviewLoading = true;
  try {
    const [health, network] = await Promise.allSettled([api("health"), api("guest-network")]);
    operationView.health = health.status === "fulfilled" ? health.value : null;
    if (network.status === "fulfilled") operationView.network = network.value;
    operationView.networkError = network.status === "rejected" ? "Incus inventory unavailable. Open Incus or refresh to retry." : "";
    lastOverviewHealthPoll = Date.now();
  } finally { overviewLoading = false; }
}
async function poll() {
  try {
    if (state.page === "operations") await refreshOperations(false);
    let healthChanged = false;
    if (["overview","guests","system"].includes(state.page) && Date.now() - lastOverviewHealthPoll >= 30000) {
      lastOverviewHealthPoll = Date.now();
      await refreshOverview();
      healthChanged = true;
    }
    const before = JSON.stringify(state.status?.job);
    state.status = await api("status");
    if (state.page === "review" && before !== JSON.stringify(state.status.job))
      shell();
    else if (["overview","guests","system"].includes(state.page) && healthChanged) shell();
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

if (typeof window !== "undefined") {
  try { document.documentElement.dataset.theme = localStorage.getItem("nixlab-theme") === "light" ? "light" : "dark"; } catch {}
  window.addEventListener("popstate", () => { if (state.status) { state.page = selectedHash(); shell(); } });
  window.addEventListener("hashchange", () => { if (state.status && selectedHash() !== state.page) { state.page = selectedHash(); shell(); } });
}

(async () => {
  try {
    state.principal = await api("session");
    state.csrf = state.principal.csrf;
    await load();
  } catch {
    login();
  }
})();

