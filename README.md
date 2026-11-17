# nixlab

### A declarative NixOS + Incus homelab.

Nixlab turns an installed NixOS machine into a repeatable home server: manage
containers and virtual machines, run applications, and keep host changes documented.
It brings together established tools rather than inventing another virtualization engine.

**For:** self-hosters who want declarative infrastructure with a browser-based way in.
**Status:** [0.1.0-beta.1 candidate](docs/beta.md); physical qualification and beta publication remain pending.

> **Target product (2026-11-17):** NixLab is a **NixOS host + NixLab Web UI + Incus/Incus UI + Cockpit**. Upload **existing Dockerfiles, Containerfiles and Compose files unchanged** (including nested build context and `.dockerignore`), or define native NixOS guest/USB projects in the same independent projects flake. NixLab previews source and Incus changes; isolated **Buildah/Podman** builds OCI images, and **Incus Compose** runs compatible stacks directly on Incus **without a Docker daemon**. SOPS + age and a one-time source/data backup policy make projects recoverable. **Arcane and its Docker apps VM are excluded from the final product.**
>
> [Target architecture: projects, secrets and backups](docs/target-architecture.md) · [Arcane retirement #126](https://github.com/OmarDardery/nixlab/issues/126) · [Project management #127–#128](https://github.com/OmarDardery/nixlab/issues/127) · [Original Dockerfile/Compose uploads #139](https://github.com/OmarDardery/nixlab/issues/139) · [Backups #129–#130](https://github.com/OmarDardery/nixlab/issues/129)
>
> **Migration status:** Current main still contains the legacy Arcane/apps-VM implementation. This decision is a planned migration, **not** a claim that the runtime or existing machines have been changed. Existing VM disks and data must not be automatically removed.

| You want to… | Nixlab brings… |
|---|---|
| Get started without reconstructing a weekend of shell history | A resumable, single-command installer |
| Run isolated workloads | Incus containers and KVM virtual machines |
| Bring existing Dockerfiles and Compose projects (target) | Upload original files unchanged, build with isolated Buildah/Podman and deploy OCI via Incus Compose; edit and review alongside project Nix |
| Understand the next change | A host portal, documentation, and explicit recovery boundaries |

The installed host foundation uses NixOS, Incus and Cockpit. Uploading,
building and deploying original Dockerfile/Compose sources from the NixLab
Web UI is **planned**, not yet a released feature. Legacy workloads remain
preserved under Incus while the replacement is being qualified.

[Upstream credits](docs/projects.md) · [Website & documentation](https://nixlab.dardery.work/) · [Interactive homelab setup](docs/home-server-scenario.md) · [Troubleshooting](docs/troubleshooting.md) · [GitHub Pages / domain setup](docs/website.md)

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
configures Incus, Cockpit and the NixLab portal. **Fresh installs no longer
provision the old applications VM.** Retries detect active work and
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
`https://HOST_IP:9440/`). Its sidebar links to enabled Incus UI and
Cockpit, plus a copy-only commands cheatsheet. Credentials never
traverse plaintext HTTP.

The portal handles host settings, reviewed file uploads and source upgrades, Ethernet/hardware prerequisites,
independent backups/recovery, accounts and offline guides. Incus handles guest operations, consoles, networks and snapshots. Optional recipes
include laptop lid policy, SMART health, private SSH access and monitoring.
The Changes page previews affected services/guests and configuration ownership.
`sudo nixlab-admin config plan` returns the same JSON; runtime drift refreshes the
review before activation, and unresolved migrations/conflicts remain blocked.
See the [ownership contract](docs/ownership.md), [helper integration](docs/helpers.md), [hardware support](docs/hardware.md) and [commissioning checklist](docs/commissioning.md).

This is a single-host beta candidate. CI gates include source tests, real NixOS portal/UNIX
permissions, password SSH, authenticated Cockpit terminals and DHCP/reboot startup checks. Physical-host installation,
actual workloads, network-loss recovery and a restore drill remain commissioning requirements.
Use `sudo nixlab commissioning collect --mode fresh` to record bounded probes;
the [commissioning guide](docs/commissioning.md) separates these from manual trials.
The [release qualification matrix](docs/qualification.md) lists the current evidence
boundary. Missing, stale or simulated evidence cannot qualify a physical target.

Update an existing NixLab host with one command:

```sh
nix run --refresh github:OmarDardery/nixlab#update
```

It requests sudo, pins main to an exact revision, builds the update and asks before
activation and access confirmation. Re-run it after a disconnect to resume. It
retains machine configuration and uses the existing rollback guard; it never
reboots automatically. Keep independent backups before updating.

## Platform stack and planned projects layer

| Layer | Choice | Why |
|---|---|---|
| Host | NixOS 26.05, pinned Nixpkgs + lock file | Reproducible OS generations and rollback |
| Guests | Incus LTS with KVM/QEMU | One API for Linux system containers and full VMs |
| Virtualization UI | Packaged `incus-ui-canonical` | Established Incus-compatible fork; uses the authenticated Incus API |
| OCI apps (target) | Incus Compose on Incus | Compatible unchanged uploaded Compose projects; one container platform |
| OCI image building (target) | Restricted Buildah/Podman worker | Original Dockerfiles, no Docker daemon or host build privileges |
| Declarative projects UI (target) | NixLab Web UI with a separate projects flake | Upload/edit source, review revisions, build, deploy, secrets and backups |
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
installer/                   # single-command installation, safe retry and source updates
admin/                       # HTTPS UI, local root broker, transaction/recovery engine
machine/settings.json        # generated on the host; ignored by Git
machine/modules/             # administrator modules, discovered automatically
machine/installed-system/    # preserved hardware/boot configuration
installed-system/            # preserved local Nix modules; ignored by Git
scripts/                     # packaged CLI, project actions, reconciliation, backup
tests/                       # safety tests and non-deployable evaluation fixtures
docs/                        # research, commissioning, operation and recovery
```

## Start here

The installation prepares NixLab, Incus and Cockpit. New application
projects will use original Dockerfile/Compose uploads and the separate
Nix workspace when #139 and #128 are implemented. Older `lab/apps`
VMs remain operator data and are not stopped or deleted during updates.
See the [target roadmap](docs/roadmap.md) and [migration guide](docs/migration.md).
Laptop power policy is a separate explicit setting.

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
sudo homelab exec GUEST -- systemctl --failed
sudo homelab backup GUEST
sudo nixlab-admin legacy-apps inventory  # Optional, read-only audit of an older apps VM
```

Numeric selectors are explicit aliases in the client's JSON config, not inferred from
Proxmox IDs. `backup` is an on-demand Incus export; scheduled restic backups additionally
stop guests and protect custom volumes. See the consistency distinctions in the recovery guide.

The installer enables the host foundation with Incus/Cockpit; it does not
provision a Docker/Arcane VM on fresh installations. `--no-apps` is retained
only as a compatibility flag. Optional Prometheus history remains off by default.
The headless host cannot suspend or hibernate. The setup UI serves its own
static assets and the port-80 entry redirects to its authenticated HTTPS portal.
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
listed in `lib/platform.nix`; mutable guest data still needs independent backups.

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
