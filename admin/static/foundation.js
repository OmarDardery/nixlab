"use strict";

function hostOverview() {
  const s = state.status.effectiveSettings || state.status.settings;
  const health = operationView.health;
  const failedServices = (health?.services || []).filter((unit) => unit.ActiveState === "failed");
  const healthKnown = (health?.services || []).some((unit) => unit.LoadState === "loaded");
  return (
    title(
      "NIXOS HOST FOUNDATION",
      "A dependable home for Incus.",
      "Nixlab prepares and recovers the host. Use Incus for guests, consoles, devices, networks and snapshots.",
    ) +
    `<div class="grid">${homepageTools()}${card("Host readiness", `<p>Backup: ${esc(health?.backup?.health || "unknown")} · ${state.status.rebootRequired ? "Reboot required" : "No reboot pending"}</p><p>${failedServices.length ? esc(failedServices.length) + " service(s) need attention" : healthKnown ? "No failed services reported" : "Service health unavailable"}${health?.rootStorage ? " · " + esc(formatBytes(health.rootStorage.availableBytes)) + " root storage available" : ""}</p><p>${state.host.kvm ? "Hardware virtualization is available." : "Hardware virtualization needs attention."}</p><p>${esc(state.host.architecture)} · ${esc(state.host.cpuVendor)} · ${state.host.uefi ? "UEFI" : "BIOS"}</p><p>Active management: <code>${esc(s.management.interface)} · ${esc(s.management.address)}</code></p><p>Configuration: ${state.status.job ? esc(state.status.job.status) : "No pending change"}</p><button data-go="review">Inspect host changes</button>`)}${card("One owner for each layer", '<p>Incus manages virtualization. NixOS owns physical networking, drivers, mounts and host services. Helper scripts own application installation; application tools own their configuration.</p><p>Ordinary Incus/helper-created guests are never adopted by a host rebuild. Platform-declared infrastructure stays source-managed.</p><a href="/docs/ownership.md">Read the ownership contract</a>')}${card("Recoverability", '<p>Keep verified independent backups. Configuration rollback does not restore guest disks or application data.</p><button data-go="operations">Backups & recovery</button>')}</div>`
  );
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
      "Connect the host, then use Incus.",
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
      "Prepare hardware without taking over guests.",
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
      '<p>An alias such as <code>/dev/nixlab/serial-device</code> identifies one selected device. In Incus, add a unix-char device with that source, an appropriate guest path, <code>required=false</code>, and the application’s guest UID/GID and mode.</p><p>Do not pass all of /dev or make the container privileged. Reconnect and reboot behavior must be tested with the actual device. Klipper and similar services are examples, not bundled applications.</p><a href="/docs/hardware.md">Attachment and hardware qualification guide</a>',
    ) +
    storage()
  );
}

function integrations() {
  const info = state.host.helpers || { sources: {}, applications: [] };
  return (
    title(
      "UPSTREAM INTEGRATIONS",
      "Use the tools that already do the job.",
      "Incus owns guests. Community Scripts provisions applications through its native Incus backend; Nixlab supplies a pinned host runner.",
    ) +
    homepageTools() +
    card(
      "Community Scripts",
      `${toggle("Install the packaged helper runner", "helpers.enable")}<p>Run on the host through SSH or its console:</p><pre class="log">sudo nixlab-helpers doctor\nsudo nixlab-helpers list\nsudo nixlab-helpers run debian --project lab</pre><p>The upstream interactive wizard selects resources, storage and network. Its guests remain Incus-managed. Nothing on this page executes a script.</p><a href="https://community-scripts.org/" target="_blank" rel="noopener noreferrer">Browse upstream applications ↗</a><p>Proxmox-specific host/VM scripts requiring pct, qm or /etc/pve are unsupported. Upstream Incus scripts are available; Nixlab verification is recorded separately.</p><div class="table-wrap"><table><thead><tr><th>Qualification target</th><th>Compatibility</th><th>Notes</th></tr></thead><tbody>${info.applications.map((item) => `<tr><td>${esc(item.title)}</td><td>${item.status === "verified" ? "Verified on Nixlab" : "Upstream-supported; unverified here"}</td><td>${esc(item.notes)}</td></tr>`).join("")}</tbody></table></div><details><summary>Pinned upstream revisions</summary><pre class="log">${esc(JSON.stringify(info.sources, null, 2))}</pre></details><a href="/docs/helpers.md">Runner, prerequisites and compatibility</a>`,
    ) +
    apps() +
    homepageResources()
  );
}

function backupSettings() {
  return card(
    "Scheduled off-host backup",
    `${toggle("Enable independent restic backups", "backup.enable")}<div class="fields">${field("Repository", "backup.repository", { full: true })}${field("Schedule", "backup.schedule", { hint: "systemd calendar, for example daily or *-*-* 03:00:00" })}${field("Free staging reserve, MiB", "backup.reserveMiB", { type: "number" })}${secret("Repository password", "resticPassword")}${secret("Cloud credential environment as JSON", "resticEnvironment", "Runtime credentials stay outside the Nix store.", true)}${field("Maintenance exclusions: project/guest", "backup.exclude", { list: true, type: "textarea", full: true })}<label class="field full"><span>Additional consistency groups</span><textarea id="backup-groups" placeholder="lab/database, lab/frontend">${esc((state.draft.backup.groups || []).map((g) => g.join(", ")).join("\n"))}</textarea><small>One group per line. Shared writable volumes automatically join their consumers.</small></label></div>${toggle("Initialize a new repository", "backup.initialize")}<p>Guests stop one group at a time and immediately recover their original state. Incus guests tagged <code>user.nixlab.backup=manual</code> are excluded with their consistency group. Coverage exclusions stay visible and do not refresh the full-host success indicator.</p><p class="note">Allow space for two retained complete generations plus a full new export and overhead. The free reserve is a minimum check, not an export-size estimate. Restic keeps 7 daily, 4 weekly and 6 monthly snapshots. Staging contains root-only plaintext credentials; choose its filesystem on Hardware & Disks.</p>`,
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
