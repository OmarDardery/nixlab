# nixlab

A declarative NixOS bare-metal host using Incus for system containers and KVM VMs,
with Docker Compose isolated inside a NixOS applications VM.

**Recommended installation:** boot the [installer ISO](docs/iso.md), connect to the Internet,
and run `sudo nixlab-setup`. The guided console installer reviews one selected disk,
requires typed erase confirmation, installs NixOS and starts the private web setup on first boot.
An [existing conventional NixOS installation](docs/setup.md) can use the conversion installer instead.

The portal handles host settings, reviewed file uploads and source upgrades, Ethernet/hardware prerequisites,
independent backups/recovery, accounts and offline guides. Incus handles guest operations, consoles, networks and snapshots. Optional recipes
include laptop lid policy, SMART health, private SSH access, monitoring and the applications VM.
See the [ownership contract](docs/ownership.md), [helper integration](docs/helpers.md), [hardware support](docs/hardware.md) and [commissioning checklist](docs/commissioning.md).

This is a single-host release candidate. CI gates include source tests, real NixOS portal/UNIX
permissions, and BIOS/UEFI media boot checks. Hardware installation, first-boot conversion,
actual workloads, network-loss recovery and a restore drill remain commissioning requirements.

## Selected stack

| Layer | Choice | Why |
|---|---|---|
| Host | NixOS 26.05, pinned Nixpkgs + lock file | Reproducible OS generations and rollback |
| Guests | Incus LTS with KVM/QEMU | One API for Linux system containers and full VMs |
| Virtualization UI | Packaged `incus-ui-canonical` | Established Incus-compatible fork; uses the authenticated Incus API |
| Applications (opt-in) | Docker/Compose in the `apps` NixOS VM | Separate kernel and Docker firewall from the hypervisor |
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
machine/settings.json        # generated on the host; ignored by Git
machine/modules/             # administrator modules, discovered automatically
machine/installed-system/    # preserved hardware/boot configuration
installed-system/            # preserved local Nix modules; ignored by Git
scripts/                     # packaged CLI, project actions, reconciliation, backup
examples/demo/               # functioning web app + PostgreSQL + Redis Compose stack
guests/apps.nix              # reproducibly built Incus NixOS VM image
tests/                       # safety tests and non-deployable evaluation fixtures
docs/                        # research, commissioning, operation and recovery
```

## Start here

For the installer and browser-first workflow, start with [the quick start](docs/start.md).
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

Lightweight defaults: Docker/Portainer and local Prometheus history are opt-in. The setup UI
serves its own static assets; only the optional application portal adds a one-worker Nginx proxy.
Settings remain declared through Nix and can be exported from the UI. See the
[resource and ownership policy](docs/setup.md#lightweight-defaults-and-declarative-ownership).

The homepage provides management/resource links and configured public application endpoints.
See the [quality-of-life roadmap](docs/roadmap.md) for prioritized proposals and acceptance criteria.

### Configuration migration

New installations keep machine-specific files in `machine/`, separate from the
tracked platform source. No second GitHub repository is required. Existing
`local/` and `site-modules/` installations continue to work; after upgrading,
`sudo nixlab-admin config migrate-layout` selects the new directory atomically.
Finish/discard any pending transaction first. Legacy files remain available for
recovery, but are no longer authoritative after migration.

See [file configuration](docs/file-modules.md) for typed settings, named guests,
disks, reviewed uploads and the migration procedure. Feature composition is
covered in `platform-modules/`; mutable guest data still needs independent backups.

### Bootable installer ISO

Build `nix build .#packages.x86_64-linux.installer-iso -o result-iso`, or download
the `nixlab-installer-x86_64` CI artifact after its build completes. The guided installer prepares one explicitly confirmed boot disk and starts Nixlab's
web setup on first boot. The standard NixOS manual installation path remains available. See [ISO installation](docs/iso.md) for exact steps and
validation limits. No disks are formatted automatically.

### Focused host foundation

Use **Incus** for guest creation, lifecycle, consoles, limits, snapshots, managed
networks and volumes. Nixlab supplies reviewed Ethernet bridges/VLANs, host
firewall integration, stable USB serial prerequisites and recoverable host changes.
The portal does not duplicate those guest editors.

`nixlab-helpers` packages the upstream Community Scripts Incus wizard with both
source repositories pinned. Run it from SSH/the console; the portal only links
and reports compatibility. No application, including Klipper, is installed by
default. Current qualification targets remain explicitly unverified until their
live install/restore evidence is recorded.

Backups publish complete generations, recover each stopped guest group immediately,
and preserve two complete local generations. Shared writable volumes join their
consumers. Maintenance exclusions remain visible and do not refresh full-host
backup freshness. Legacy archives and CLI/API commands remain supported.
