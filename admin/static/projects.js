"use strict";

// Shared native-tool identity, credits and documentation. No remote assets load.
const projectRepository = "https://github.com/OmarDardery/nixlab";
const projectReferences = [
  { name: "Nixlab", source: "https://github.com/OmarDardery/nixlab", detail: "Installation, host changes and recovery", guide: "plain-nixos.md",
    docs: [["Project source", "https://github.com/OmarDardery/nixlab"]],
    community: [["Issues & contributions", "https://github.com/OmarDardery/nixlab/issues"]] },
  { name: "NixOS & Nix", source: "https://github.com/NixOS/nixpkgs", detail: "Declarative host and pinned packages", guide: "file-modules.md",
    docs: [["NixOS manual", "https://nixos.org/manual/nixos/stable/"], ["Nix CLI", "https://nix.dev/manual/nix/stable/"]],
    community: [["Community configurations", "https://wiki.nixos.org/wiki/Configuration_Collection"]] },
  { name: "QEMU / KVM", source: "https://gitlab.com/qemu-project/qemu", detail: "Virtual machine execution and Linux hardware acceleration", guide: "architecture.md", docs: [["QEMU docs", "https://www.qemu.org/docs/master/"]], community: [] },
  { name: "Incus", source: "https://github.com/lxc/incus", detail: "Containers, VMs, images, networks and storage", guide: "administration.md",
    docs: [["Official docs", "https://linuxcontainers.org/incus/docs/main/"]],
    community: [["Incus helper scripts", "https://github.com/community-scripts/Incus"], ["Application catalog", "https://community-scripts.org/"]] },
  { name: "Cockpit", source: "https://github.com/cockpit-project/cockpit", detail: "Host terminal, services and journal", guide: "homelab.md",
    docs: [["Official docs", "https://cockpit-project.org/documentation.html"]],
    community: [["Community plugins", "https://cockpit-project.org/applications.html"]] },
  { name: "Docker & Compose", source: "https://github.com/docker/compose", detail: "Application containers inside the apps VM", guide: "homelab.md",
    docs: [["Docker docs", "https://docs.docker.com/"], ["Compose guide", "https://docs.docker.com/compose/"]],
    community: [["Compose examples", "https://github.com/docker/awesome-compose"]] },
  { name: "Portainer", source: "https://github.com/portainer/portainer", detail: "Docker dashboards, stacks and templates", guide: "homelab.md",
    docs: [["Official docs", "https://docs.portainer.io/"], ["Template format", "https://docs.portainer.io/advanced/app-templates"]],
    community: [["Community templates", "https://github.com/lissy93/portainer-templates"]] },
  { name: "restic", source: "https://github.com/restic/restic", detail: "Optional off-host backups", guide: "recovery.md", optional: true,
    docs: [["Official docs", "https://restic.readthedocs.io/en/stable/"]],
    community: [["Community tools & wrappers", "https://github.com/restic/others"]] },
  { name: "Cloudflare Tunnel", source: "https://github.com/cloudflare/cloudflared", detail: "Optional public application ingress", guide: "networking.md", optional: true,
    docs: [["Tunnel docs", "https://developers.cloudflare.com/tunnel/"]],
    community: [["cloudflared source & issues", "https://github.com/cloudflare/cloudflared"]] },
  { name: "Nginx", source: "https://github.com/nginx/nginx", detail: "HTTPS reverse proxy for Portainer", guide: "homelab.md", optional: true,
    docs: [["Official docs", "https://nginx.org/en/docs/"], ["njs session isolation", "https://nginx.org/en/docs/njs/"]],
    community: [["NixOS configuration examples", "https://wiki.nixos.org/wiki/Nginx"]] },
  { name: "Tailscale", source: "https://github.com/tailscale/tailscale", detail: "Optional private-access recipe", guide: "operations.md", optional: true,
    docs: [["Official docs", "https://tailscale.com/docs"]],
    community: [["NixOS community guide", "https://wiki.nixos.org/wiki/Tailscale"]] },
  { name: "sops-nix", source: "https://github.com/Mic92/sops-nix", detail: "Optional encrypted runtime secrets", guide: "recovery.md", optional: true,
    docs: [["Setup & reference", "https://github.com/Mic92/sops-nix"]],
    community: [["Configuration examples", "https://github.com/Mic92/dotfiles"]] },
  { name: "Prometheus", source: "https://github.com/prometheus/prometheus", detail: "Optional metrics and local history", guide: "setup.md", optional: true,
    docs: [["Official docs", "https://prometheus.io/docs/introduction/overview/"]],
    community: [["Community exporters", "https://prometheus.io/docs/instrumenting/exporters/"]] },
  { name: "Host networking & services", detail: "SSH, NetworkManager, firewall and systemd", guide: "networking.md", optional: true,
    docs: [["NixOS options", "https://search.nixos.org/options"], ["OpenSSH", "https://www.openssh.com/manual.html"], ["NetworkManager", "https://networkmanager.dev/docs/api/latest/nmcli.html"], ["nftables", "https://wiki.nftables.org/"], ["systemd", "https://www.freedesktop.org/software/systemd/man/latest/"]],
    community: [["Networking recipes", "https://wiki.nixos.org/wiki/Networking"]] },
];

function managementServices() {
  const active = state.status.effectiveSettings || state.status.settings;
  const host = state.status.managementAddress || active.management.address;
  const rows = [
    ["Incus", 8443, true],
    ["Cockpit", active.cockpit?.port, active.cockpit?.enable],
    ["Portainer", active.apps?.portainerPort, active.apps?.enable],
  ];
  return rows.map(([name, port, enabled]) => {
    const project = projectReferences.find(item => item.name === name);
    let url = null;
    const ip = typeof host === "string" && !/[\s/@?#\\]/.test(host) &&
      (host.includes(":") || host.split(".").length === 4 && host.split(".").every(n => /^(0|[1-9]\d{0,2})$/.test(n) && Number(n) <= 255));
    if (state.status.setupComplete && enabled && ip && Number.isInteger(port) && port > 0 && port < 65536) {
      try {
        const authority = host.includes(":") ? `[${host}]` : host;
        url = new URL(`https://${authority}:${port}`).href.replace(/\/$/, "");
      } catch { /* Invalid/unobserved endpoints are not clickable. */ }
    }
    return {...project, port, enabled, url};
  });
}
