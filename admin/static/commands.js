"use strict";

// References only. Copying a command never calls a host execution endpoint.
const projectReferences = [
  { name: "Nixlab", detail: "Installation, host changes and recovery", guide: "plain-nixos.md",
    docs: [["Project source", "https://github.com/OmarDardery/nixlab"]],
    community: [["Issues & contributions", "https://github.com/OmarDardery/nixlab/issues"]] },
  { name: "NixOS & Nix", detail: "Declarative host and pinned packages", guide: "file-modules.md",
    docs: [["NixOS manual", "https://nixos.org/manual/nixos/stable/"], ["Nix CLI", "https://nix.dev/manual/nix/stable/"]],
    community: [["Community configurations", "https://wiki.nixos.org/wiki/Configuration_Collection"]] },
  { name: "Incus", detail: "Containers, VMs, images, networks and storage", guide: "administration.md",
    docs: [["Official docs", "https://linuxcontainers.org/incus/docs/main/"]],
    community: [["Incus helper scripts", "https://github.com/community-scripts/Incus"], ["Application catalog", "https://community-scripts.org/"]] },
  { name: "Cockpit", detail: "Host terminal, services and journal", guide: "homelab.md",
    docs: [["Official docs", "https://cockpit-project.org/documentation.html"]],
    community: [["Community plugins", "https://cockpit-project.org/applications.html"]] },
  { name: "Docker & Compose", detail: "Application containers inside the apps VM", guide: "homelab.md",
    docs: [["Docker docs", "https://docs.docker.com/"], ["Compose guide", "https://docs.docker.com/compose/"]],
    community: [["Compose examples", "https://github.com/docker/awesome-compose"]] },
  { name: "Portainer", detail: "Docker dashboards, stacks and templates", guide: "homelab.md",
    docs: [["Official docs", "https://docs.portainer.io/"], ["Template format", "https://docs.portainer.io/advanced/app-templates"]],
    community: [["Community templates", "https://github.com/lissy93/portainer-templates"]] },
  { name: "restic", detail: "Optional off-host backups", guide: "recovery.md", optional: true,
    docs: [["Official docs", "https://restic.readthedocs.io/en/stable/"]],
    community: [["Community tools & wrappers", "https://github.com/restic/others"]] },
  { name: "Cloudflare Tunnel", detail: "Optional public application ingress", guide: "networking.md", optional: true,
    docs: [["Tunnel docs", "https://developers.cloudflare.com/tunnel/"]],
    community: [["cloudflared source & issues", "https://github.com/cloudflare/cloudflared"]] },
  { name: "Nginx", detail: "HTTPS reverse proxy for Portainer", guide: "homelab.md", optional: true,
    docs: [["Official docs", "https://nginx.org/en/docs/"]],
    community: [["NixOS configuration examples", "https://wiki.nixos.org/wiki/Nginx"]] },
  { name: "Tailscale", detail: "Optional private-access recipe", guide: "operations.md", optional: true,
    docs: [["Official docs", "https://tailscale.com/docs"]],
    community: [["NixOS community guide", "https://wiki.nixos.org/wiki/Tailscale"]] },
  { name: "sops-nix", detail: "Optional encrypted runtime secrets", guide: "recovery.md", optional: true,
    docs: [["Setup & reference", "https://github.com/Mic92/sops-nix"]],
    community: [["Configuration examples", "https://github.com/Mic92/dotfiles"]] },
  { name: "Prometheus", detail: "Optional metrics and local history", guide: "setup.md", optional: true,
    docs: [["Official docs", "https://prometheus.io/docs/introduction/overview/"]],
    community: [["Community exporters", "https://prometheus.io/docs/instrumenting/exporters/"]] },
  { name: "Host networking & services", detail: "SSH, NetworkManager, firewall and systemd", guide: "networking.md", optional: true,
    docs: [["NixOS options", "https://search.nixos.org/options"], ["OpenSSH", "https://www.openssh.com/manual.html"], ["NetworkManager", "https://networkmanager.dev/docs/api/latest/nmcli.html"], ["nftables", "https://wiki.nftables.org/"], ["systemd", "https://www.freedesktop.org/software/systemd/man/latest/"]],
    community: [["Networking recipes", "https://wiki.nixos.org/wiki/Networking"]] },
];

function referenceLink(label, url) {
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
}

function referenceCatalog() {
  const row = item => `<div class="reference-row"><div><h3>${esc(item.name)}</h3><p>${esc(item.detail)}</p></div><div class="reference-links">${referenceLink("Nixlab guide", "/docs/" + item.guide)}${item.docs.map(([label, url]) => referenceLink(label, url)).join("")}${item.community.map(([label, url]) => referenceLink(label, url)).join("")}</div></div>`;
  const local = [["Install", "plain-nixos"], ["Troubleshoot", "troubleshooting"], ["CLI", "administration"], ["Helpers", "helpers"], ["Web setup", "setup"], ["Backups", "recovery"], ["Migration", "migration"], ["Roadmap", "roadmap"]];
  return `<section class="reference-catalog" id="project-docs"><div class="section-label">DOCUMENTATION & COMMUNITY</div><h2>The right manual for the right layer.</h2><p>Community resources are references, not verified Nixlab integrations. Plugins, templates and scripts have their own platform requirements.</p><div class="guide-shortcuts">${local.map(([label, slug]) => referenceLink(label, "/docs/" + slug + ".md")).join("")}</div>${projectReferences.filter(item => !item.optional).map(row).join("")}<details class="more-references"><summary>Backups, secrets, ingress, metrics & host foundations</summary>${projectReferences.filter(item => item.optional).map(row).join("")}</details></section>`;
}

function commandGroups() {
  const settings = state.status.effectiveSettings || state.status.settings;
  const address = state.status.managementAddress || settings.management.address;
  const host = /^(?:\d{1,3}\.){3}\d{1,3}$/.test(address) && address.split('.').every(n => Number(n) <= 255) ? address : "HOST_IP";
  return [
    { id: "cheatsheet", title: "Everyday homelab cheatsheet", guide: "administration.md", items: [
      ["quick-doctor", "NixLab health", "Host terminal", "Check host and management endpoint readiness.", "sudo nixlab doctor"],
      ["quick-guests", "Show all guests", "Host terminal", "Instances and their current states across Incus projects.", "sudo incus list --all-projects"],
      ["quick-failures", "Failed services", "Host terminal", "Inspect failed systemd units.", "systemctl --failed"],
      ["quick-log", "Follow NixLab web logs", "Host terminal", "Investigate authentication or startup problems.", "sudo journalctl -u nixlab-ui -f"],
      ["quick-apps", "Check apps VM", "Host terminal", "Check Docker from inside the isolated guest.", "sudo incus exec apps --project lab -- docker ps"],
      ["quick-guest-logs", "Guest service logs", "Host terminal", "Inspect the selected guest without publishing its admin interfaces.", "sudo incus exec GUEST --project lab -- journalctl -b --no-pager -n 50"],
      ["quick-recovery", "Show pending install checklist", "Host terminal", "See pending guest network, device and backup commissioning steps.", "sudo nixlab install-plan"],
      ["quick-config", "Current change status", "Host terminal", "View reviewed configuration transactions.", "sudo nixlab-admin config status"],
    ] },
    { id: "updates", title: "Install & update", guide: "plain-nixos.md", items: [
      ["platform-update", "Update Nixlab", "Host terminal", "Update from main with build/impact review, activation and access-confirmation prompts. Requests sudo and resumes its own interrupted update; no automatic reboot.", "nix run --refresh github:OmarDardery/nixlab#update"],
      ["package-review", "Review a package refresh", "Host terminal", "Build a candidate with refreshed pinned inputs. This does not activate it; inspect and apply it under Changes.", "sudo nixlab-admin config review --update-inputs"],
      ["review-status", "Inspect the current transaction", "Host terminal", "Check build, activation or confirmation state before starting another change.", "sudo nixlab-admin config status"],
    ] },
    { id: "health", title: "Health, logs & SSH", guide: "troubleshooting.md", items: [
      ["doctor", "Check the management stack", "Host terminal", "Probe the actual address and enabled management interfaces.", "sudo nixlab doctor"],
      ["failed-units", "Find failed services", "Host terminal", "List failed systemd units on the host.", "systemctl --failed"],
      ["cockpit-boot", "Read Cockpit’s boot logs", "Host terminal", "Collect evidence before restarting a service.", "sudo journalctl -b -u cockpit.socket -u cockpit.service -u nixlab-management-identity.service --no-pager"],
      ["ssh", "Connect to this host", "Your laptop", "Use the operator Linux password or an enrolled client key.", "ssh operator@" + host],
      ["commissioning", "Collect commissioning evidence", "Host terminal", "Read-only probes record revision-bound results. Reboots, installation retry and restore drills remain separate procedures in the commissioning guide.", "sudo nixlab commissioning collect --mode fresh"],
    ] },
    { id: "guests", title: "Guest & Docker examples", guide: "administration.md", items: [
      ["list-guests", "List every Incus project’s guests", "Host terminal", "Read-only inventory. Guest lifecycle and consoles remain in Incus.", "sudo incus list --all-projects"],
      ["apps-shell", "Open a shell in the apps VM", "Host terminal", "Requires the applications VM to be enabled and running.", "sudo homelab exec apps"],
      ["compose-list", "List Compose projects", "Host terminal → apps VM", "Docker runs in the apps VM, not on the hypervisor.", "sudo homelab exec apps -- docker compose ls"],
      ["portainer-logs", "Read Portainer logs", "Host terminal → apps VM", "Inspect the current guest boot’s Portainer service output.", "sudo homelab exec apps -- journalctl -b -u docker-portainer.service --no-pager"],
    ] },
    { id: "helpers", title: "Community helper commands", guide: "helpers.md", items: [
      ["helper-list", "Browse the packaged helper catalog", "Host terminal", "List script IDs and compatibility metadata from the pinned sources.", "nixlab-helpers list"],
      ["helper-doctor", "Check helper prerequisites", "Host terminal", "Verify Incus, images, networks and storage for the lab project.", "sudo nixlab-helpers doctor --project lab"],
      ["helper-debian", "Example: Debian container wizard", "Host terminal", "Interactive Incus wizard; creates a guest. Helpers must be enabled. Choose its storage and network in the terminal.", "sudo nixlab-helpers run debian --project lab"],
      ["helper-catalog", "Select a community helper", "Host terminal", "Browse application choices; review upstream scripts and prerequisites before use.", "nixlab-helpers list"],
      ["helper-vaultwarden", "Example: Vaultwarden wizard", "Host terminal", "Interactive application installer. Include the resulting application data in your backup plan.", "sudo nixlab-helpers run vaultwarden --project lab"],
    ] },
  ];
}

function commandRow(item) {
  const [id, label, where, detail, command] = item;
  return `<div class="command-row"><div class="command-description"><h3>${esc(label)}</h3><span class="command-location">${esc(where)}</span><p>${esc(detail)}</p></div><div class="command-code"><pre><code id="command-${id}">${esc(command)}</code></pre><button class="small" type="button" data-copy-command="${id}" aria-label="Copy ${esc(label)} command">Copy</button></div></div>`;
}

function commandsPage() {
  return title("REFERENCE DESK", "Commands & docs", "Useful commands, examples and upstream resources. Copy a command, then run it in the indicated terminal. This page does not execute anything.")
    + `<div class="reference-jumps"><a href="#commands">Commands ↓</a><a href="#project-docs">Docs & community ↓</a></div><section class="command-library" id="commands">${commandGroups().map((group, index) => `<details class="command-group" ${index === 0 ? "open" : ""}><summary><span>${esc(group.title)}</span><span class="group-count">${group.items.length} commands</span></summary><div class="command-group-body"><p class="group-guide">${referenceLink("Read the guide", "/docs/" + group.guide)}</p>${group.items.map(commandRow).join("")}</div></details>`).join("")}</section>${homepageResources()}`;
}

function bindCommands() {
  document.querySelectorAll("[data-copy-command]").forEach(button => {
    button.onclick = async () => {
      const code = document.getElementById("command-" + button.dataset.copyCommand);
      try {
        await navigator.clipboard.writeText(code.textContent);
        button.textContent = "Copied";
        toast("Command copied. Run it in the indicated terminal.");
      } catch {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        button.textContent = "Selected";
        toast("Clipboard unavailable. The command is selected for manual copying.");
      }
      setTimeout(() => { button.textContent = "Copy"; }, 2500);
    };
  });
}
