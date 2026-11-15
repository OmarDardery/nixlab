# Architecture decisions and research

## Maintainer contracts

The table records the current owners and the regression coverage to preserve while
changing them. Planned changes are not claims that a feature already exists.

| Boundary | Current owner | Regression coverage |
|---|---|---|
| Settings defaults, types and Nix module overrides | `site-defaults.nix`, `site.nix`, `lib/settings-migrations.nix`, `lib/settings.nix`, `modules/settings.nix`; Python validates interactive input | `tests/settings-checks.nix`, `tests/test_setup.py` |
| Host review, activation and rollback | `admin/controller.py` Manager and `admin/worker.py`; installer and update commands call them | `tests/test_update_command.py`, `tests/test_installer_workflow.py`, `tests/test_impact.py` |
| Host network and management exposure | `modules/networking.nix` and individual service modules; firewall, binding and application checks are currently separate | `tests/management-vm.nix`, `tests/test_management.py` |
| Management identity and TLS | `admin/runtime.py` and the management identity service in `modules/admin-ui.nix` | `tests/management-vm.nix`, `tests/test_installer_workflow.py` |
| Reserved Incus infrastructure and explicitly managed instances | `scripts/infrastructure.py`, `scripts/reconcile.py`; native objects remain Incus-owned | `tests/test_platform.py`, `tests/test_impact.py` |
| Docker applications | Portainer or the operator's declared Compose source in the apps VM | `tests/apps-integration.nix`, `tests/guest-deployments-kvm.nix` |
| Independent backup and restore evidence | `scripts/backup.py`, `admin/operations.py`, `admin/recovery.py` | `tests/test_backup_plan.py`, `tests/test_platform.py` |
| Dependencies and qualification | `flake.lock` pins Nix inputs; `scripts/release.py` validates release evidence | `tests/test_release.py`, `.github/workflows/release.yml` |

### Frozen compatibility surface (CR-01)

`tests/fixtures/architecture-contract.json` is the **versioned public-surface
inventory**, with assertions in `tests/test_compatibility_surface.py`. It
characterizes existing interfaces; it is not a specification that unsafe defaults
or implementation details must be retained. Running the source-only tests does
not replace the evaluated NixOS/Incus and rollback integration tests.

| Boundary | Canonical source / data owner | Public/stable surface | Failure or migration gate |
|---|---|---|---|
| Host settings, defaults, and legacy schema | `site-defaults.nix`, `lib/settings-migrations.nix`, `lib/settings.nix`, `site.nix`; Python only validates request inputs | `machine/settings.json` (layout v1), legacy `local/settings.json`, `schemaVersion`, file-module overrides | #49; default/list/override/legacy tests and reject unknown future versions |
| Host mutation | `admin/controller.py` `Manager` + `admin/worker.py` | Review, build, impact plan, apply, confirm, rollback, cancel; `active.json`, `pending.json`, `last-confirmed.json`, `jobs/` | #50/#52; preserved previous generation, live access check, interrupt and stopped-worker tests |
| Entry points and HTTPS API | `installer/package.nix`, `admin/package.nix`, `admin/main.py` | `nix run .#install`, `nix run .#update`, `nixlab-admin` and authenticated `/api/*` paths/response fields | #50/#55/#62; public alias/route/shape regression before renaming; sensitive output is **not** protected compatibility |
| Listener/exposure policy | `modules/settings.nix`, `modules/networking.nix`, `modules/admin-ui.nix`, service-owned listeners | Management HTTPS, Cockpit, Incus, optional app proxy and explicit trusted CIDRs | #51; collision/bind/scope tests, management access/rollback. Present fragmented policy is a gap |
| Identity and certificate lifecycle | `modules/admin-ui.nix` management identity; `admin/runtime.py` certificate and password primitives; Linux PAM for Cockpit, Incus trust for UI, Portainer DB for apps | Existing portal session and separate Cockpit/Incus/Portainer identity; `nixlab-management-identity.service` | #57/#61–#68; never freeze credential printing, blanket trust or weak onboarding |
| Incus declared infrastructure | Native Incus preseed + `scripts/infrastructure.py`; reconciled instance fields via `scripts/reconcile.py` | Reserved project/profile/network/pool, last-applied state, ETag/ownership; **native** guests survive source changes | #47/#9; drift and changed-precondition refusal, no deletion of unowned guest state |
| Apps VM and Compose | Incus owns apps VM/disk; Docker/Portainer or user's Compose repository owns containers/stacks and Portainer DB | Persistent guest disks, volumes and specialist UI | #10/#16/#58; updates cannot claim data rollback or overwrite existing specialist identity |
| Backup and restore evidence | `scripts/backup.py`, `admin/operations.py`, `admin/recovery.py` and restic | Staged manifest, independent archive, integrity result and **actual restore proof** are distinct | #11/#12/#53; incomplete stage, exclusions and interruption never count as verified restore |
| Monitoring and alerts | `modules/monitoring.nix`, `modules/notifications.nix` | Prometheus/optional historical metrics, failure notification | #53; Alertmanager is the planned normal router, **not currently assumed implemented** |
| Nix/OCI dependencies | Single `flake.lock`; OCI image/template references currently live in their owning services | `nixosModules`, `nixosConfigurations`, `packages`, `apps`, `checks` | #54/#58; one lock and planned reviewed OCI manifest, no second dependency authority |
| Release evidence | `scripts/release.py`, CI workflows, commissioning/recovery reports | `VERSION`, checks/qualification gates, source revision | #56/#8; machine-readable release contract planned, physical evidence remains distinct |

**Compatibility policy.** Changes to the manifest's flake paths, CLI aliases,
authenticated portal routes/response fields, state paths, machine format, or
systemd unit identities require an explicit compatibility adapter or migration,
a tested rollback and an intentional inventory update. New endpoints/options
can be added without forcing all clients to change. The independent state
formats (`machine/format.json` and settings `schemaVersion`) must not be
conflated. No test should preserve installer auto-confirmation, plain-text
password disclosure, overly broad exposure, or an unverified health/restore
claim just because those behaviors once existed.

**Failure traceability (S02–S09).** Legacy/modern snapshots, list overlay and
future-layout refusal: `test_architecture_contract.py`,
`test_compatibility_surface.py`, `test_setup.py`,
`tests/settings-checks.nix`. Worker interruption and action locks:
`test_worker_inspection.py`, `test_installer_workflow.py`,
`test_update_command.py`, `test_bootstrap_transaction.py`,
`tests/bootstrap-transaction-vm.nix`. Incus ETags/native preservation and
partial inventory: `test_impact.py`, `test_guest_network.py`,
`tests/foundation-vm.nix`. Secret cleanup and recoverability:
`test_secret_cleanup.py`, `test_backup_plan.py`,
`tests/management-vm.nix`. Release evidence:
`test_release.py`, `.github/workflows/validate.yml`.
The current installed system or guest-data recovery is **never** inferred from
a source-only passing test; on-host commissioning is a separate qualification.

Settings JSON uses `schemaVersion = 1`; `machine/format.json` independently
versions the directory layout. `lib/settings-migrations.nix` is the only migration
implementation. The broker reads normalized desired settings through the selected
source’s pure `site.nix`; host-module overrides are captured separately. A failure webhook exists, but Alertmanager
routing and a centralized OCI image manifest are not implemented. The existing broad access defaults remain known security gaps, not compatibility
guarantees. Direct installation requires explicit access confirmation.

Initial wizard onboarding also uses Manager/worker, with a fixed `bootstrap`
transaction role. It owns no guest resources and leaves settings unconfigured;
full host transactions use `hypervisor`. Role selection is reserved to the local
initial installer, never accepted from portal request bodies, and existing jobs
without role metadata retain their hypervisor meaning. Both use the same source
digest, impact gate, exact built closure, pending deadline and recovery manifest.

Keep public `nix run ...#install` and `#update` entrypoints, the `nixlab-admin`
CLI, existing machine settings/modules, transaction records, and previous system
generation recovery usable across refactors. A change to a public API, state path,
or data interpretation needs a documented migration, rollback path and regression
test; unknown inventory must never be treated as an empty or healthy lab. Tests
must distinguish host rollback from guest and application data recovery.

Research date: 2026-10-27.

The host foundation update (2026-10-29) narrows the portal to host installation,
maintenance, physical networking, hardware prerequisites and independent recovery.
Incus owns guest lifecycle, resources, devices, networks and volumes; see the
[ownership contract](ownership.md). Community Scripts use the pinned upstream
Incus backend through a local CLI, with no portal script execution.

The [installer/portal architecture](setup.md) adds a preserved-installation bootstrap, typed JSON
Nix input, separate unprivileged HTTPS process/local root broker, reviewed rebuild transactions,
UUID-backed mounts, modular radios and a persistent rollback guard. In installer mode the
existing apps VM is also deployed through Incus, and a TLS-verified Portainer proxy provides
a LAN portal without guest SSH setup. Manual mode retains the original direct/SSH workflow.
 The lock file pins Nixpkgs to
`718f670e845328e9c2cecdd832835712b2cbd065` on `nixos-26.05`.
26.05 is the current stable release; 26.11 is not selected as a stable release in October.

## Virtualization

KVM is Linux's hardware virtualization subsystem, exposed through `/dev/kvm`.
QEMU provides the virtual machine process and devices and uses KVM for accelerated
execution. A bare-metal Linux/KVM deployment is commonly classified as a Type 1
hypervisor architecture because the kernel controls the physical hardware; it still
has a general-purpose host userspace, unlike appliance-style ESXi. Enabling KVM
alone supplies neither inventory, storage orchestration, authentication, backups nor a UI.

The pinned native module selects Incus LTS 7.0.2 (version verified through Nix evaluation).
Incus is the management layer. It runs Linux system containers sharing the host kernel,
and VMs with their own kernel through QEMU/KVM. It supplies profiles, projects, images,
networks, volumes, snapshots, exec, console and a REST API. The KVM unit checks the
API with an actual ioctl, not merely a CPU flag. Firmware VT-x/AMD-V must be enabled.

| Candidate | Assessment | Decision |
|---|---|---|
| Incus | Maintained upstream, unified containers/VMs, native NixOS module | Primary manager |
| libvirt + Cockpit Machines | Good VM tooling; does not unify Incus system containers; Machines manages libvirt | No competing libvirtd |
| microvm.nix | Excellent tightly declared lightweight guests; smaller interactive management surface | Useful specialist alternative, not this platform |
| Proxmox VE | Strong integrated backup/cluster/HA ecosystem; Debian-based host | Migration source, not compatible with NixOS host requirements |
| IncusOS | Maintained dedicated appliance, Debian/mkosi-based | Strong alternative if NixOS host reproducibility is not required |
| Kubernetes | Adds orchestration and operational complexity without a requirement | Omitted |

Sources: [Incus](https://github.com/lxc/incus),
[installation/NixOS integration](https://linuxcontainers.org/incus/docs/main/installing/),
[NixOS Incus module](https://github.com/NixOS/nixpkgs/blob/718f670e845328e9c2cecdd832835712b2cbd065/nixos/modules/virtualisation/incus.nix),
[IncusOS](https://github.com/lxc/incus-os),
[Cockpit Machines](https://github.com/cockpit-project/cockpit-machines).

## Web interfaces and maintenance

| Interface | Maintenance evidence / scope | Decision |
|---|---|---|
| `incus-ui-canonical` | Zabbly-maintained Canonical UI fork; stable Nixpkgs package uses tested `incus-*` tags (0.19.9 at pinned revision) | Stable primary virtualization UI |
| New first-party Incus UI | Upstream announced an aligned first-party UI on 2026-10-20; not the same as the mature packaged fork | Reassess when released and packaged on the chosen stable channel |
| ixui | Latest upstream release `v26.10.0`, 2026-10-06; author still explicitly calls it pre-alpha | Optional experiment, never a required dependency |
| Operations Center | Maintained Linux Containers multi-server/cluster management project | Not required for one host; separate deployment/security boundary if needed later |
| Cockpit | Useful host logs/services/storage view; Machines uses libvirt, not Incus | Optional host UI only; omitted to minimize privileged services |

The packaged Incus UI provides image-based creation, lifecycle controls, resource and
NIC/device editing, console/terminal, networks, storage and snapshot operations.
Exact button coverage depends on the packaged version; the native CLI/API remains the
reference for exports/imports and advanced configuration. It is not a complete
Proxmox-style host service/log viewer: persistent journal + SSH + Prometheus provide that.
Browser TLS client certificate enrollment is required. No anonymous admin endpoint exists.

Sources: [packaged UI](https://github.com/zabbly/incus-ui-canonical),
[Nixpkgs UI derivation](https://github.com/NixOS/nixpkgs/blob/718f670e845328e9c2cecdd832835712b2cbd065/pkgs/by-name/in/incus-ui-canonical/package.nix),
[ixui release](https://github.com/xlmnxp/ixui/releases/tag/v26.10.0),
[upstream status discussion](https://discuss.linuxcontainers.org/t/introducing-ixui-a-proxmox-esxi-style-web-ui-for-incus/27118),
[Operations Center](https://github.com/lxc/operations-center).

## Compose management

| Candidate | Strengths | Limits | Decision |
|---|---|---|---|
| Portainer CE | Compose stacks, Git source, UI editor, logs/console, per-container stats, local authentication | Database is mutable; some access/automation features are Business Edition; external CLI stacks have limited editing | Primary UI, verified 2.45.3 LTS release 2026-10-21 |
| Dockge | Simple on-disk Compose projects and responsive editor/logs | Latest release 1.5.0 dated 2025-03-30; narrower Git/permissions/monitoring surface, console disabled by default for security | Good smaller alternative; not installed alongside Portainer |
| Komodo | Active repository (pushed 2026-10-23), Git-backed deployments, procedures, resource management | Core + periphery + database add components and privileged credentials | Consider when multi-host build/deploy workflows justify it |

One Compose project groups the web app, PostgreSQL and Redis. Named volumes survive
`down`; helpers never pass `--volumes`. UI-created stacks support editing and deployment.
The included `demo` is owned by Nix/CLI; it appears as an external stack in Portainer and
must be edited in Git. Do not let two reconcilers manage the same stack. For a UI-owned
stack use Portainer's Git stack workflow with a different project name, then back up its
Portainer state and application data. Portainer backups alone exclude application volumes.

Sources: [Portainer release](https://github.com/portainer/portainer/releases/tag/2.45.3),
[Git/Compose stack creation](https://docs.portainer.io/user/docker/stacks/add),
[Portainer backup scope](https://docs.portainer.io/faqs/getting-started/what-does-portainers-backup-include),
[Dockge](https://github.com/louislam/dockge), [Dockge releases](https://github.com/louislam/dockge/releases),
[Komodo](https://github.com/moghtech/komodo).

## Docker placement

- Host Docker is efficient but gives application workloads host-root-equivalent socket
  access and introduces host FORWARD/firewall interactions with Incus.
- Nested Docker in an unprivileged Incus container saves memory but shares the host
  kernel and needs nesting/storage/syscall compatibility. It is a supported specialist choice,
  not the reliability/isolation default.
- The dedicated VM has a memory/disk overhead and requires its own OS rebuilds. It gives
  Docker a separate kernel and firewall. This is the default. VM management is through
  Incus; Docker management is through Portainer/Compose inside that VM.

[Incus firewall/Docker guidance](https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/)
and [Docker firewall behavior](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
explicitly describe the FORWARD-policy conflict. Neither firewall nor Docker's rules are disabled.

## Declarative boundaries

| State | Owner and behavior |
|---|---|
| Host/guest services, packages, firewall, helper programs | NixOS modules; pinned inputs, OS generations |
| Reserved projects, profiles, pools, bridge and server bind address | Native `virtualisation.incus.preseed`; source-named infrastructure is checked for native drift before applying; omitted resources are never removed |
| Named instance desired state | Ownership-aware reconciler; compares before updating; refuses unowned name collisions or type changes; never deletes |
| Instance image creation | Nix-built apps VM image imported automatically; image alias updates do not replace disks |
| Config/device overlays | Three-way last-applied tracking removes previously managed keys when runtime has not diverged; unowned keys survive. Whole-device removal/backing changes require migration |
| Profiles on owned instances | Authoritative ordered list; changes may affect live guests |
| Guest processes, disks, snapshots, custom volumes, UI-created instances | Mutable Incus runtime state; exports/backup required |
| Portainer users, endpoints, stacks, Git credentials | Mutable `/srv/portainer` state; bootstrap admin secret is used only on first creation |
| Compose definitions | Demo file is immutable Nix source; additional CLI projects can be checked-out Git files under `/srv/compose` |
| Cloudflare tunnel/account/DNS creation | External control-plane initialization; locally managed ingress is Nix-authoritative afterward |
| Passwords, tunnel JSON, age private keys, restic credentials | Runtime secret files/sops-nix; never Nix string secrets |

Terranix + the maintained [Incus OpenTofu/Terraform provider](https://github.com/lxc/terraform-provider-incus)
can manage instances, profiles and volumes declaratively. It introduces another state file,
provider upgrades and plan/apply lifecycle; [an Incus/Terranix example](https://github.com/hyshka/incus-terranix-example)
illustrates the integration. This platform uses the native preseed module and a small, tested,
non-deleting instance reconciler instead. No native stable NixOS `virtualisation.incus.instances`
option is assumed.

## Limits that remain

No HA/fencing/shared-storage cluster is configured. A single host and its power/network are
single points of failure. No automatic disk formatting, disk replacement, router changes,
certificate enrollment or cloud-account creation is hidden in a rebuild. Directory-backed
container storage cannot promise hard per-container quotas; use ZFS for that requirement.
Offline scheduled backups impose downtime and need staging space. Registry-provided
PostgreSQL, Redis and Portainer require reviewed digest pinning for byte-level reproducibility.
Default guest IPv6 is disabled until a real routed prefix/firewall plan exists.

Generic USB/serial prerequisites and stable aliases are provided; assignment stays in Incus.
No live migration, GPU/PCI qualification, TPM enrollment, public admin UIs or Proxmox Backup
Server-compatible backup format is claimed. Guest image import is initialization, not guest
configuration deployment. Update an existing NixOS VM through `nixos-rebuild` over SSH/Incus.

## Resource policy

The default deployment is an Incus host without an idle Docker VM or Prometheus history database.
Both are opt-in through declared settings. The setup portal serves its own static assets using
its existing authenticated Python HTTP service. Only the optional Portainer portal needs Nginx,
limited to one worker and providing verified TLS/WebSocket proxying. See [setup](setup.md#lightweight-defaults-and-declarative-ownership)
for service ownership, monitoring trade-offs and state that must be backed up separately.

## HTTPS request limits

The existing portal HTTPS server admits at most 32 worker threads. TLS handshakes
run in those workers with a 10-second socket timeout; HTTP reads/writes use a
20-second inactivity timeout. A stalled handshake cannot block the accept loop.
This is bounded concurrency, not a guarantee of availability under sustained load.

The stdlib header parser has a 16 KiB aggregate header budget, in addition to its
request-line and header-count limits. Management requests require exactly one
Host header. JSON POSTs require one decimal Content-Length between 1 and 262144
bytes; duplicate lengths and Transfer-Encoding are rejected before body reads.
Malformed, incomplete or unreadable bodies close the connection so leftover bytes
cannot become another request. Transport/JSON decode errors and unsupported-method
responses do not reflect the submitted content. Domain validation errors retain
their existing behavior and require the separate credential-output audit.

`tests/test_http_limits.py` checks framing and bounded reads. The real HTTPS
fixture in `tests/test_setup.py` checks rejection/closure, existing auth/CSRF and
hostname rules, safe method errors, and recovery after 100 stalled TLS clients.
HTTP timeouts are inactivity bounds, not an absolute request deadline. Management
firewall policy, TLS trust and private-network deployment remain necessary; these
tests do not qualify public Internet exposure or complete the broader #74 audit.

## Durable worker observations

Configuration, guest operations and deployments keep separate state machines and
recovery actions. They share `runtime.job_directory`, `runtime.tail_log` and
`runtime.worker_active` for validated job IDs, bounded log reads and bounded local
systemd observation. Worker inspection accepts only the existing NixLab worker
unit names and times out after ten seconds. Active, inactive/failed and missing
units are distinguished from a failed inspection. A timeout, missing systemctl or
permission/transport error reports unavailable state without marking the durable
job failed or interrupted; raw stderr is never returned. The caller retains its
own stopped-worker recovery rules and mutation locking.

The shared job reader bounds record size, opens the job directory and record
without following aliases, and accepts only regular JSON objects owned by the
privileged caller. Missing optional records keep their legacy defaults. Invalid
records produce a fixed error with no record contents or filesystem details.
Atomic writes sync file data and permissions before replacement, then sync the
directory. Job formats, paths and recovery decisions still belong to each
domain.
