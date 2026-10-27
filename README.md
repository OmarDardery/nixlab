# nixlab

A declarative NixOS bare-metal host using Incus for system containers and KVM VMs,
with Docker Compose isolated inside a NixOS applications VM.

**Recommended installation:** run the [bootstrap installer](install.sh) on a fresh conventional
NixOS host, open its printed HTTPS address, and finish the [web setup wizard](docs/setup.md).
The installer preserves the actual hardware, bootloader, users and state version. The portal
configures networking, existing data disks, Wi-Fi/Bluetooth, applications and runtime secrets;
it builds/reviews a candidate before switching, with a persistent rollback/confirmation guard.

Implementation remains in hardware commissioning: source checks/builds and HTTPS tests pass,
but no physical machine was converted in this workspace. Read [validation results](docs/validation.md)
and the supported starting configurations in the [setup guide](docs/setup.md). Manual Git/Nix
configuration remains available and intentionally refuses activation with an incomplete inventory.

## Selected stack

| Layer | Choice | Why |
|---|---|---|
| Host | NixOS 26.05, pinned Nixpkgs + lock file | Reproducible OS generations and rollback |
| Guests | Incus LTS with KVM/QEMU | One API for Linux system containers and full VMs |
| Virtualization UI | Packaged `incus-ui-canonical` | Established Incus-compatible fork; uses the authenticated Incus API |
| Applications | Docker/Compose in the `apps` NixOS VM | Separate kernel and Docker firewall from the hypervisor |
| Application UI | Portainer CE 2.45.3 LTS | Compose stacks, Git deployment, editor, logs, console, resource views |
| Administration | SSH, native Incus remote CLI, packaged `homelab` | Real command/API calls with output and exit codes |
| Network | Private Incus bridge + NixOS nftables/NAT | Per-guest IPs, DHCP/reservations, controlled LAN reachability |
| Storage | Non-formatting directory pool baseline; existing ZFS dataset option | Safe initial deployment; ZFS preferred for enforced container quotas and efficient snapshots |
| Public ingress | Native NixOS cloudflared module | Application hostnames only; no management ports published |
| Secrets | sops-nix integration, or pre-provisioned root-owned runtime files | No plaintext credentials in Git or the Nix store |
| Recovery | Offline Incus exports + PostgreSQL dump + off-host restic | Independent, encrypted backups and explicit restore procedure |
| Observability | Incus UI, node exporter, local Prometheus, persistent journal | Guest/host metrics, history, service and backup health |

This is a single-host platform, not a drop-in Proxmox replacement. NixOS rollback
restores host code and services, not guest disks, databases, firmware or Incus schema.
Existing VMs are never rebuilt from their image on host rebuild. Registry image tags
remain mutable until replaced with digests. Read the [architecture and trade-offs](docs/architecture.md)
before deployment.

## Layout

```text
configuration.nix             # bare-metal entry point
hardware-configuration.nix    # replace with actual generated hardware/boot config
site.nix                     # explicit inventory and feature flags
flake.nix / flake.lock        # pinned host, VM, image, helper and validation outputs
modules/                     # Incus, firewall, storage, secrets, backups, monitoring
modules/docker-host.nix       # imported in apps VM only
install.sh                   # Nix-backed bootstrap launcher
installer/                   # preserved-installation bootstrap and packaged installer
admin/                       # HTTPS UI, local root broker, transaction/recovery engine
local/settings.json          # generated on the host; ignored by Git
installed-system/            # preserved local Nix modules; ignored by Git
scripts/                     # packaged CLI, project actions, reconciliation, backup
examples/demo/               # functioning web app + PostgreSQL + Redis Compose stack
guests/apps.nix              # reproducibly built Incus NixOS VM image
tests/                       # safety tests and non-deployable evaluation fixtures
docs/                        # research, commissioning, operation and recovery
```

## Start here

For the installer and browser-first workflow, start with [web setup](docs/setup.md).
The following guides also cover the advanced/manual deployment path.

1. Read [architecture decisions](docs/architecture.md).
2. Collect the inventory and follow [deployment](docs/deployment.md).
3. Review [networking](docs/networking.md) and [security, storage and backups](docs/recovery.md).
4. Use [administration](docs/administration.md) for UI enrollment and actual helper commands.
5. Follow the staged [Proxmox migration](docs/migration.md); do not overwrite the only working host.

```console
sudo homelab list
sudo homelab exec apps -- uname -a
sudo homelab exec 105 -- systemctl status klipper
sudo homelab exec-many apps,adguard -- df -h
sudo homelab action apps health
sudo homelab restart apps
sudo homelab deploy demo
sudo homelab logs demo
sudo homelab backup apps
```

Numeric selectors are explicit aliases in the client's JSON config, not inferred from
Proxmox IDs. `backup` is an on-demand Incus export; scheduled restic backups additionally
stop guests and protect custom volumes. See the consistency distinctions in the recovery guide.
