# nixlab

### Give the old laptop a suspicious amount of responsibility.

Nixlab turns an installed NixOS machine into a repeatable home server: manage
containers and virtual machines, run applications, and keep host changes documented.
It brings together established tools rather than inventing another virtualization engine.

**For:** self-hosters who want declarative infrastructure with a browser-based way in.
**Status:** single-host release candidate; physical-host commissioning remains required.

| You want to… | Nixlab brings… |
|---|---|
| Get started without reconstructing a weekend of shell history | A resumable, single-command installer |
| Run isolated workloads | Incus containers and KVM virtual machines |
| Keep application stacks familiar | Docker Compose in a separate applications VM |
| Understand the next change | A host portal, documentation, and explicit recovery boundaries |

A declarative NixOS bare-metal host using Incus for system containers and KVM VMs,
with Docker Compose isolated inside a NixOS applications VM.

[Website & documentation](https://nixlab.dardery.work/) · [Interactive homelab setup](docs/home-server-scenario.md) · [Troubleshooting](docs/troubleshooting.md) · [GitHub Pages / domain setup](docs/website.md)

**Start from a plain, installed NixOS server** (no desktop needed):

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/nixlab#install
```

On a fresh interactive terminal, it first reviews workloads and access; the
versioned `--answers` and `--plan-only` modes also support unattended setup.
The interview configures infrastructure capabilities rather than named applications.
Guest, device, DNS and Compose provisioning remain independent reviewed workflows.

It preserves installed users, filesystems, boot configuration and addressing, then
builds Incus, Cockpit and the Portainer apps VM. Retries detect active work and
resume automatically. SSH password login is enabled for non-root Linux accounts;
no SSH key is required. A stable operator password is created if needed.
Success includes endpoint checks, actual assigned URLs and verified credentials.
Existing Incus requires explicit adoption and completed backups.
See [plain NixOS installation and recovery](docs/plain-nixos.md) for options,
supported adoption boundaries and the installation issue regression checklist.

Start with an ordinary minimal NixOS installation, working networking and hardware
virtualization enabled in firmware.

The portal is also reachable from trusted LAN clients at `http://HOST_IP/` on port 80,
which redirects immediately to the authenticated HTTPS host console (default
`https://HOST_IP:9440/`). Its sidebar links directly to enabled Incus UI,
Cockpit, and Portainer, plus a copy-only commands cheatsheet. Credentials never
traverse plaintext HTTP.

The portal handles host settings, reviewed file uploads and source upgrades, Ethernet/hardware prerequisites,
independent backups/recovery, accounts and offline guides. Incus handles guest operations, consoles, networks and snapshots. Optional recipes
include laptop lid policy, SMART health, private SSH access, monitoring and the applications VM.
The Changes page previews affected services/guests and configuration ownership.
`sudo nixlab-admin config plan` returns the same JSON; runtime drift refreshes the
review before activation, and unresolved migrations/conflicts remain blocked.
See the [ownership contract](docs/ownership.md), [helper integration](docs/helpers.md), [hardware support](docs/hardware.md) and [commissioning checklist](docs/commissioning.md).

This is a single-host release candidate. CI gates include source tests, real NixOS portal/UNIX
permissions, password SSH, authenticated Cockpit terminals and DHCP/reboot startup checks. Physical-host installation,
actual workloads, network-loss recovery and a restore drill remain commissioning requirements.
Use `sudo nixlab commissioning collect --mode fresh` to record bounded probes;
the [commissioning guide](docs/commissioning.md) separates these from manual trials.
The [release qualification matrix](docs/qualification.md) lists the current evidence
boundary. Missing, stale or simulated evidence cannot qualify a physical target.

## Selected stack

| Layer | Choice | Why |
|---|---|---|
| Host | NixOS 26.05, pinned Nixpkgs + lock file | Reproducible OS generations and rollback |
| Guests | Incus LTS with KVM/QEMU | One API for Linux system containers and full VMs |
| Virtualization UI | Packaged `incus-ui-canonical` | Established Incus-compatible fork; uses the authenticated Incus API |
| Applications (opt-in) | Docker/Compose in the `apps` NixOS VM | Separate kernel and Docker firewall from the hypervisor |
| Application UI | Portainer CE 2.45.3 LTS | Compose stacks, Git deployment, editor, logs, console, resource views |
| Host administration | Cockpit (opt-in), SSH, native Incus CLI, packaged `homelab` | Host terminal, services and journal; guest consoles stay in Incus |
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
installer/                   # single-command installation, safe retry and source updates
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

The command above installs the Cockpit + Incus + Portainer stack. Existing portal
users can also use **Integrations** and the [homelab guide](docs/homelab.md). It stages Cockpit on 9090,
Portainer on 9443 and lid-closed operation without remapping existing disks or guests.
Existing hosts retain Portainer port 9441 until explicitly changed.

For the installer and browser-first workflow, start with [the quick start](docs/start.md).
The following guides also cover the advanced/manual deployment path.

1. Read [architecture decisions](docs/architecture.md).
2. Collect the inventory and follow [deployment](docs/deployment.md).
3. Review [networking](docs/networking.md) and [security, storage and backups](docs/recovery.md).
4. Use [administration](docs/administration.md) for UI enrollment and actual helper commands.
5. Follow the staged [Proxmox migration](docs/migration.md); do not overwrite the only working host.

```console
sudo homelab list
sudo nixlab doctor
sudo nixlab enroll-key /path/to/laptop.pub
sudo nixlab portainer reset-password
sudo homelab exec apps -- uname -a
sudo homelab exec GUEST -- systemctl --failed
sudo homelab exec-many apps,web -- df -h
sudo homelab action apps health
sudo homelab restart apps
sudo homelab deploy demo
sudo homelab logs demo
sudo homelab backup apps
```

Numeric selectors are explicit aliases in the client's JSON config, not inferred from
Proxmox IDs. `backup` is an on-demand Incus export; scheduled restic backups additionally
stop guests and protect custom volumes. See the consistency distinctions in the recovery guide.

The installer enables Cockpit and Docker/Portainer; use `--no-apps` to omit the VM.
Local Prometheus history and the demo workload remain off. Advanced module defaults
keep Docker/Portainer opt-in. The headless host cannot suspend or hibernate. The setup UI
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

### Focused host foundation

Use **Incus** for guest creation, lifecycle, consoles, limits, snapshots, managed
networks and volumes. Nixlab supplies reviewed Ethernet bridges/VLANs, host
firewall integration, stable USB serial prerequisites and recoverable host changes.
The portal does not duplicate those guest editors.

Opt-in [guest deployments](docs/guest-deployments.md) update existing source-owned
NixOS guests in dependency order, with health gates and resumable journals. Host
access confirmation and guest/application health remain separate.

`nixlab-helpers` packages the upstream Community Scripts Incus wizard with both
source repositories pinned. Run it from SSH/the console; the portal only links
and reports compatibility. No named third-party application is installed by default. Current qualification targets remain explicitly unverified until their
live install/restore evidence is recorded.

Backups publish complete generations, recover each stopped guest group immediately,
and preserve two complete local generations. Shared writable volumes join their
consumers. Maintenance exclusions remain visible and do not refresh full-host
backup freshness. Legacy archives and CLI/API commands remain supported.
