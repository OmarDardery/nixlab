# Upstream projects & credits

NixLab integrates maintained upstream software. Its job is a small NixOS control
page, safe host configuration and updates, and backup/recovery coordination.
Guest lifecycle, consoles, networking and storage remain native Incus operations.

| Project | Responsibility | Documentation | Source |
|---|---|---|---|
| NixOS / Nix | Declarative host configuration and reproducible builds | [NixOS](https://nixos.org/manual/nixos/stable/), [Nix](https://nix.dev/manual/nix/stable/) | [Nixpkgs](https://github.com/NixOS/nixpkgs), [Nix](https://github.com/NixOS/nix) |
| Incus | Containers, VMs, images, consoles, networks, IPAM, volumes and snapshots | [Incus manual](https://linuxcontainers.org/incus/docs/main/) | [Incus](https://github.com/lxc/incus) |
| Incus UI (Canonical-derived fork) | Native browser interface to Incus | [Browser access](administration.md#incus-web-ui) | [Zabbly's maintained fork](https://github.com/zabbly/incus-ui-canonical), [Canonical UI](https://github.com/canonical/lxd-ui) |
| QEMU / Linux KVM | VM execution and hardware acceleration | [QEMU](https://www.qemu.org/docs/master/), [KVM](https://docs.kernel.org/virt/kvm/) | [QEMU](https://gitlab.com/qemu-project/qemu), [Linux](https://www.kernel.org/) |
| Cockpit | Optional host terminal, services, journal and resource inspection | [Cockpit manual](https://cockpit-project.org/documentation.html) | [Cockpit](https://github.com/cockpit-project/cockpit) |
| Portainer CE | Optional Docker/Compose administration inside the apps VM | [Portainer manual](https://docs.portainer.io/) | [Portainer](https://github.com/portainer/portainer) |
| Docker / Compose | Application containers and Compose workloads | [Docker](https://docs.docker.com/), [Compose](https://docs.docker.com/compose/) | [Moby](https://github.com/moby/moby), [Compose](https://github.com/docker/compose) |
| restic | Independent encrypted off-host backups | [restic manual](https://restic.readthedocs.io/en/stable/) | [restic](https://github.com/restic/restic) |
| sops-nix / age | Optional declarative secrets and recovery encryption | [sops-nix](https://github.com/Mic92/sops-nix), [age](https://age-encryption.org/) | [sops-nix](https://github.com/Mic92/sops-nix), [age](https://github.com/FiloSottile/age) |
| Nginx / njs | Private HTTPS forwarding and host/guest cookie isolation | [Nginx](https://nginx.org/en/docs/), [njs](https://nginx.org/en/docs/njs/) | [Nginx](https://github.com/nginx/nginx), [njs](https://github.com/nginx/njs) |
| Prometheus | Optional metrics history and alert rules | [Prometheus manual](https://prometheus.io/docs/introduction/overview/) | [Prometheus](https://github.com/prometheus/prometheus) |
| cloudflared | Optional public application ingress | [Tunnel manual](https://developers.cloudflare.com/tunnel/) | [cloudflared](https://github.com/cloudflare/cloudflared) |
| Community Scripts | Optional upstream Incus provisioning helpers | [Compatibility and ownership](helpers.md) | [Incus helpers](https://github.com/community-scripts/Incus) |

NixOS also supplies OpenSSH, systemd, NetworkManager, [dhcpcd](https://github.com/NetworkConfiguration/dhcpcd)
and nftables. Their manuals
and optional VPN references are available under **Help → Upstream projects**.
Upstream components retain their own licenses and authorship. NixLab does not
claim their implementation or imply upstream endorsement. Included fonts retain
their OFL notices under `admin/static/fonts/`.

The host console links to enabled Incus, Cockpit and Portainer endpoints using
effective host settings and the observed management address. These tools have
their own authentication: Incus client certificates, Linux accounts for Cockpit,
and Portainer accounts. Portal roles do not replace those boundaries. Disabled
tools are configured under **Host → Services**. Optional monitoring bound to
loopback does not acquire a misleading public/LAN link.
