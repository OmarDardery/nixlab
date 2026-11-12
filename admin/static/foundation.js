"use strict";

function guestDeploymentReview() {
  const job = state.status.job, plan = job?.guestPlan;
  if (!plan?.targets?.length) return "";
  const deployment = job.guestDeployment;
  const busy = ["queued", "running"].includes(deployment?.status);
  const rows = plan.targets.map(target => {
    const status = deployment?.targets?.[target.id];
    return `<tr><td><code>${esc(target.project)}/${esc(target.name)}</code><br><small>${esc(target.dependsOn.length ? "After: " + target.dependsOn.join(", ") : "After host confirmation")}</small></td><td><small>Current: <code>${esc(status?.currentSystem || target.currentSystem || "Unknown / not yet reachable")}</code><br>Target: <code>${esc(target.targetSystem)}</code></small></td><td>${esc(status?.phase || "Not deployed")}<br><small>${esc(status?.attempts || 0)}/${esc(target.maxAttempts)} attempts · reboot: ${esc(target.restartPolicy)}<br>Checks: ${esc(target.healthUnits.join(", "))}${target.httpChecks ? " · " + esc(target.httpChecks) + " HTTP" : ""}</small>${status?.checkedAt ? `<p><small>Last passed: ${esc(new Date(status.checkedAt * 1000).toLocaleString())}</small></p>` : ""}${status?.error ? `<p class="error">${esc(status.error)}</p>` : !status && target.observation ? `<p>${esc(target.observation)}</p>` : ""}</td></tr>`;
  }).join("");
  return card("Guest deployment", `<p>Host access and guest health are separate. After confirming the host, deploy these reviewed OS closures in dependency order. Overall: <b>${esc(deployment?.status || "Not started")}</b>.</p><div class="table-wrap" tabindex="0"><table><thead><tr><th>Guest & order</th><th>OS generations</th><th>Deployment state</th></tr></thead><tbody>${rows}</tbody></table></div><p class="note">${esc(plan.boundary)}</p>${deployment?.error ? `<p class="error">${esc(deployment.error)}</p>` : ""}<div class="actions"><button data-action="deploy-guests" ${!administrator() || job.status !== "confirmed" || busy || deployment?.status === "abandoned" ? "disabled" : ""}>${deployment ? "Resume / recheck guests" : "Deploy reviewed guests"}</button>${deployment && !busy && deployment.status !== "abandoned" ? `<button data-action="abandon-deployment" ${!administrator() ? "disabled" : ""}>Abandon retries</button>` : ""}</div><p><a href="/docs/guest-deployments.md">Guest update and recovery guide →</a></p>`);
}

function impactReview() {
  const plan = state.status.job?.impactPlan;
  if (!plan) return state.status.job?.status === "ready"
    ? card("Change impact", '<p>An older candidate has no impact plan. Build a new review before activation.</p>') : "";
  const labels = {configuration: "Configuration", "restart-required": "Possible restart", "migration-required": "Migration required", "ownership-conflict": "Ownership conflict", unknown: "Unknown effects"};
  const renderRows = items => items.map(item => `<tr><td><code>${esc(item.identity)}</code><br><small>${esc(item.scope)}</small></td><td>${esc(labels[item.kind] || item.kind)}${item.blocked ? ' · blocked' : ''}</td><td>${esc(item.message)}${item.fields.length ? `<p><small>Fields: ${esc(item.fields.join(", "))}</small></p>` : ""}${item.affected.length ? `<p><small>Affects: ${esc(item.affected.join(", "))}</small></p>` : ""}${item.resolution ? `<p>${esc(item.resolution)}</p>` : ""}</td></tr>`).join("");
  const rows = renderRows(plan.changes.filter(item => item.scope !== "service"));
  const services = plan.changes.filter(item => item.scope === "service");
  const owners = plan.ownership.map(item => `<tr><td><code>${esc(item.identity)}</code></td><td>${esc(item.owner)}</td><td>${esc(item.fields.join(", ") || "Native/external configuration")}${item.consumers.length ? `<br><small>Consumers: ${esc(item.consumers.join(", "))}</small>` : ""}</td></tr>`).join("");
  return card("Change impact", `<p>${plan.blocked ? "Resolve the blocking changes before activation." : "Review affected resources and activation output before applying."} Runtime: ${esc(plan.runtimeState)} · ${esc(plan.preconditionCount)} observed preconditions.</p>${plan.refreshedAfterDrift ? '<p class="error">Runtime changed since review. This plan was refreshed; review it again before activation.</p>' : ""}${state.status.job.rebootRequired ? '<p class="note">The candidate requires a host reboot.</p>' : ""}<div class="table-wrap" tabindex="0"><table><thead><tr><th>Resource</th><th>Change</th><th>Impact & resolution</th></tr></thead><tbody>${rows || '<tr><td colspan="3">No modeled resource changes.</td></tr>'}</tbody></table></div>${services.length ? `<details><summary>Changed host services (${services.length})</summary><div class="table-wrap" tabindex="0"><table><thead><tr><th>Service</th><th>Change</th><th>Impact</th></tr></thead><tbody>${renderRows(services)}</tbody></table></div></details>` : ""}<details><summary>Who owns what</summary><div class="table-wrap" tabindex="0"><table><thead><tr><th>Resource</th><th>Owner</th><th>Fields & consumers</th></tr></thead><tbody>${owners}</tbody></table></div></details>${plan.boundaries.map(value => `<p class="note">${esc(value)}</p>`).join("")}<p><a href="/docs/ownership.md">Ownership and review boundaries →</a></p>`);
}

function hostOverview() {
  const s = state.status.effectiveSettings || state.status.settings;
  const health = operationView.health;
  const failed = (health?.services || []).filter(unit => unit.ActiveState === "failed");
  const known = (health?.services || []).some(unit => unit.LoadState === "loaded");
  const services = failed.length ? failed.length + " service(s) need attention" : known ? "No failed services reported" : "Service health unavailable";
  return title("HOST CONSOLE / OVERVIEW", "Your homelab.", "Manage the host here. Open Incus, Cockpit or Portainer for the rest.")
    + `<div class="grid overview-grid">${homepageTools()}${card("Host status", `<dl class="host-facts"><div><dt>Management</dt><dd><code>${esc(s.management.interface)} · ${esc(state.status.managementAddress || s.management.address)}</code></dd></div><div><dt>Services</dt><dd class="${failed.length ? "error" : ""}">${esc(services)}</dd></div><div><dt>Virtualization</dt><dd>${state.host.kvm ? "KVM available" : "Needs attention"}</dd></div><div><dt>Backup</dt><dd>${esc(health?.backup?.health || "unknown")}</dd></div><div><dt>Reboot</dt><dd>${state.status.rebootRequired ? "Required" : "Not pending"}</dd></div>${health?.rootStorage ? `<div><dt>Free root space</dt><dd>${esc(formatBytes(health.rootStorage.availableBytes))}</dd></div>` : ""}<div><dt>Configuration</dt><dd>${esc(state.status.job?.status || "No pending change")}</dd></div></dl><div class="actions"><button data-go="commands">Commands & docs</button><button data-go="operations">Backups & recovery</button></div><p class="note">OS rollback does not restore guest data. <a href="/docs/ownership.md">Who owns what →</a></p>`)}</div>${publicApplications()}`;
}

function recordEditor(heading, path, columns, defaults) {
  const values = at(state.draft, path) || [];
  return card(
    heading,
    values
      .map(
        (row, index) =>
          `<div class="mount-row"><div class="fields">${columns.map(([key, label, kind = "text"]) => `<label class="field"><span>${esc(label)}</span>${kind === "protocol" ? `<select data-record="${path}" data-index="${index}" data-key="${key}"><option ${row[key] === "tcp" ? "selected" : ""}>tcp</option><option ${row[key] === "udp" ? "selected" : ""}>udp</option></select>` : `<input data-record="${path}" data-index="${index}" data-key="${key}" data-kind="${kind}" type="${kind === "number" ? "number" : "text"}" value="${esc(Array.isArray(row[key]) ? row[key].join(", ") : row[key])}">`}</label>`).join("")}</div><button class="small danger" data-record-remove="${path}" data-index="${index}">Remove declaration</button></div>`,
      )
      .join("") +
      `<button data-record-add="${path}" data-defaults="${esc(JSON.stringify(defaults))}">Add declaration</button>`,
  );
}

function hostConnectivity() {
  const choices = [
    ...state.host.interfaces.map((i) => i.name),
    ...(state.draft.connectivity?.bridges || []).map((b) => b.name),
    ...(state.draft.connectivity?.vlans || []).map((v) => v.name),
  ];
  return (
    title(
      "PHYSICAL NETWORKING",
      "Connectivity",
      "Ethernet bridges and VLANs are host prerequisites. Create guest networks, reservations, NIC attachments and ACLs in Incus.",
    ) +
    card(
      "Management addressing",
      `${toggle("Declare management addressing", "management.configure")}<div class="fields">${field("Interface or declared bridge", "management.interface", { hint: "Discovered/declared: " + [...new Set(choices)].join(", ") })}${field("IPv4 address", "management.address")}${field("Prefix length", "management.prefixLength", { type: "number" })}${field("Gateway", "management.gateway")}${field("DNS servers", "management.dns", { list: true, type: "textarea" })}${field("Trusted management CIDRs", "management.trustedCIDRs", { list: true, type: "textarea" })}</div><p class="note">When bridging the management NIC, move its address onto the bridge in the same review. Keep console access; an unconfirmed activation restores the previous host configuration.</p>`,
    ) +
    recordEditor(
      "Ethernet LAN bridges",
      "connectivity.bridges",
      [
        ["name", "Bridge name"],
        ["interfaces", "Ethernet/VLAN ports", "list"],
      ],
      { name: "br-lan", interfaces: [] },
    ) +
    recordEditor(
      "VLAN interfaces",
      "connectivity.vlans",
      [
        ["name", "Interface name"],
        ["interface", "Ethernet parent"],
        ["id", "VLAN tag", "number"],
      ],
      { name: "vlan20", interface: "", id: 20 },
    ) +
    card(
      "Private guest firewall integration",
      `<div class="fields">${field("Private Incus bridge interfaces", "connectivity.guestInterfaces", { list: true, type: "textarea", full: true, hint: "Register private bridge names after creating them in Incus. Never list physical/LAN interfaces here." })}</div>${toggle("Permit trusted LAN clients to reach private guests", "connectivity.trustedLANAccess", "Private guest addresses require a route on your router/client. Incus forwards and ACLs remain in Incus.")}<p class="note">The reserved incusbr0 baseline uses host NAT. Additional Incus networks own their own NAT. Host management is isolated from private guests.</p>`,
    ) +
    recordEditor(
      "Narrow guest access to LAN services",
      "connectivity.privateAccess",
      [
        ["cidr", "Destination IPv4 CIDR"],
        ["protocol", "Protocol", "protocol"],
        ["ports", "Ports", "ports"],
      ],
      { cidr: "", protocol: "udp", ports: [53] },
    ) +
    card(
      "Bootstrap guest network",
      `<p>The initial private network is source-managed. Existing address space requires a migration, not a casual edit in either UI.</p><div class="fields">${field("Subnet", "guests.cidr")}${field("Bridge address/prefix", "guests.gatewayCIDR")}${field("DHCP range", "guests.dhcpRange")}${field("Reserved apps address", "guests.appsAddress")}</div><button data-action="suggest">Suggest an unused subnet</button><p class="note">LAN bridges use your Ethernet network’s DHCP/router. Switch trunks and router routes are configured on that equipment. Wi-Fi cannot be bridged as an Ethernet port.</p><a href="/docs/networking.md">Network ownership and examples</a>`,
    )
  );
}

function hostHardware() {
  const devices = state.host.serialDevices || [];
  return (
    title(
      "HARDWARE & DISKS",
      "Hardware & disks",
      "Nixlab handles stable host identities and prerequisites. Assign devices and guest permissions in Incus.",
    ) +
    card(
      "USB serial devices",
      devices
        .map(
          (d, index) =>
            `<div class="service-row"><div class="service-copy"><h3>${esc(d.product || d.path)}</h3><p><code>${esc(d.path)}</code> · ${esc(d.vendorId)}:${esc(d.productId)} · ${esc(d.serial || "No unique serial")}</p><small>${esc(d.driver)} · ${esc(d.stablePaths.join(", "))}</small></div><button data-serial-pick="${index}" ${d.serial ? "" : "disabled"}>Declare stable alias</button></div>`,
        )
        .join("") ||
        "<p>No USB serial devices detected. Nothing will be assigned or installed automatically.</p>",
    ) +
    recordEditor(
      "Selected stable serial aliases",
      "hardware.serialAliases",
      [
        ["name", "Alias name"],
        ["vendorId", "USB vendor ID"],
        ["productId", "USB product ID"],
        ["serial", "Exact device serial"],
      ],
      { name: "serial-device", vendorId: "", productId: "", serial: "" },
    ) +
    card(
      "Use the device in Incus",
      '<p>An alias such as <code>/dev/nixlab/serial-device</code> identifies one selected device. In Incus, add a unix-char device with that source, an appropriate guest path, <code>required=false</code>, and the application’s guest UID/GID and mode.</p><p>Do not pass all of /dev or make the container privileged. Reconnect and reboot behavior must be tested with the actual device. No application-specific service is bundled.</p><a href="/docs/hardware.md">Attachment and hardware qualification guide</a>',
    ) +
    storage()
  );
}

function integrations() {
  const info = state.host.helpers || { sources: {}, applications: [] };
  return (
    title(
      "UPSTREAM INTEGRATIONS",
      "Integrations",
      "Incus owns guests. Community Scripts provisions applications through its native Incus backend; Nixlab supplies a pinned host runner.",
    ) +
    card("Homelab management", `<p>Cockpit runs on the host for its terminal, services and journal. Incus manages guests. Portainer manages Docker Compose inside the isolated apps VM.</p><button data-action="management-stack">Stage Cockpit + Portainer + laptop mode</button><p>This stages settings only. Enter credentials below, then Build & review → Activate → Confirm access. Existing networks, disks and workloads are preserved.</p><a href="/docs/homelab.md">Your homelab setup and service migration</a>`) +
    card("Cockpit host terminal", `${toggle("Enable Cockpit on the host", "cockpit.enable")}<div class="fields">${field("Cockpit HTTPS port", "cockpit.port", { type: "number" })}${secret("Local operator password for Cockpit", "cockpitPassword", "At least 16 characters. This enrolls the Linux operator account; SSH accepts the same password when password login is enabled. Leave blank to retain an existing local account password.")}</div><p>Sign in as operator using its Linux password, or use an existing non-root local account. Portal accounts and passwords are separate. The operator has sudo administration rights.</p>`) +
    card(
      "Community Scripts",
      `${toggle("Install the packaged helper runner", "helpers.enable")}<p>The packaged runner opens an interactive Incus wizard from your host terminal.</p><button class="small" data-go="commands">Helper commands & documentation →</button><p>The upstream interactive wizard selects resources, storage and network. Its guests remain Incus-managed. Nothing on this page executes a script.</p><a href="https://community-scripts.org/" target="_blank" rel="noopener noreferrer">Browse upstream applications ↗</a><p>Proxmox-specific host/VM scripts requiring pct, qm or /etc/pve are unsupported. Upstream Incus scripts are available; Nixlab verification is recorded separately.</p><details><summary>Compatibility & pinned revisions</summary><div class="table-wrap"><table><thead><tr><th>Qualification target</th><th>Compatibility</th><th>Notes</th></tr></thead><tbody>${info.applications.map((item) => `<tr><td>${esc(item.title)}</td><td>${item.status === "verified" ? "Verified on Nixlab" : "Upstream-supported; unverified here"}</td><td>${esc(item.notes)}</td></tr>`).join("")}</tbody></table></div><pre class="log">${esc(JSON.stringify(info.sources, null, 2))}</pre></details><a href="/docs/helpers.md">Runner, prerequisites and compatibility</a>`,
    ) +
    apps() +
    '<p class="note">Looking for commands or upstream resources? <button class="small" data-go="commands">Commands & docs →</button></p>'
  );
}

function backupSettings() {
  return card(
    "Scheduled off-host backup",
    `${toggle("Enable independent restic backups", "backup.enable")}<div class="fields">${field("Repository", "backup.repository", { full: true })}${field("Schedule", "backup.schedule", { hint: "systemd calendar, for example daily or *-*-* 03:00:00" })}${field("Free staging reserve, MiB", "backup.reserveMiB", { type: "number" })}${secret("Repository password", "resticPassword")}${secret("Cloud credential environment as JSON", "resticEnvironment", "Runtime credentials stay outside the Nix store.", true)}${field("Maintenance exclusions: project/guest", "backup.exclude", { list: true, type: "textarea", full: true })}<label class="field full"><span>Additional consistency groups</span><textarea id="backup-groups" placeholder="lab/database, lab/frontend">${esc((state.draft.backup.groups || []).map((g) => g.join(", ")).join("\n"))}</textarea><small>One group per line. Shared writable volumes automatically join their consumers.</small></label></div>${toggle("Initialize a new repository", "backup.initialize")}<p>Guests stop one group at a time and immediately recover their original state. Incus guests tagged <code>user.nixlab.backup=manual</code> are excluded with their consistency group. Coverage exclusions stay visible and do not refresh the full-host success indicator.</p><p class="note">Allow space for two retained complete generations plus a full new export and overhead. Preflight estimates exports from observed usage and previous archives plus a reserve; unknown estimates stay explicit. Estimates cannot guarantee future growth or compression. External bind mounts and raw disks require independent protection. Restic keeps 7 daily, 4 weekly and 6 monthly snapshots. Staging contains root-only plaintext credentials; choose its filesystem on Hardware & Disks.</p>`,
  );
}

function bindFoundation() {
  const writable = (path) =>
    administrator() &&
    !(state.status.codeOverrides || []).some(
      (c) => c.setting === path || path.startsWith(c.setting + "."),
    );
  document.querySelectorAll("[data-record]").forEach((el) => {
    el.disabled = !writable(el.dataset.record);
    el.onchange = () => {
      const kind = el.dataset.kind;
      const value =
        kind === "number"
          ? Number(el.value)
          : ["list", "ports"].includes(kind)
            ? el.value
                .split(/[\n,]/)
                .map((v) => v.trim())
                .filter(Boolean)
                .map((v) => (kind === "ports" ? Number(v) : v))
            : el.value;
      at(state.draft, el.dataset.record)[Number(el.dataset.index)][
        el.dataset.key
      ] = value;
    };
  });
  document
    .querySelectorAll("[data-record-add],[data-record-remove]")
    .forEach((el) => {
      const path = el.dataset.recordAdd || el.dataset.recordRemove;
      el.disabled = !writable(path);
      el.onclick = () => {
        if (el.dataset.recordAdd)
          at(state.draft, path).push(JSON.parse(el.dataset.defaults));
        else at(state.draft, path).splice(Number(el.dataset.index), 1);
        shell();
      };
    });
  document.querySelectorAll("[data-serial-pick]").forEach((el) => {
    el.disabled = el.disabled || !writable("hardware.serialAliases");
    el.onclick = () => {
      const d = state.host.serialDevices[Number(el.dataset.serialPick)];
      state.draft.hardware.serialAliases.push({
        name: "serial-" + (state.draft.hardware.serialAliases.length + 1),
        vendorId: d.vendorId,
        productId: d.productId,
        serial: d.serial,
      });
      shell();
    };
  });
  const groups = document.querySelector("#backup-groups");
  if (groups) {
    groups.disabled = !writable("backup.groups");
    groups.onchange = () =>
      (state.draft.backup.groups = groups.value
        .split("\n")
        .map((line) =>
          line
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
        )
        .filter((g) => g.length));
  }
}

