# Architecture decisions and research

Research date: 2026-10-27. Nixpkgs is pinned to
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
| Projects, profiles, pools, bridge and server bind address | Native `virtualisation.incus.preseed`; reapplies changed declarations; never removes omitted resources |
| Named instance desired state | Ownership-aware reconciler; compares before updating; refuses unowned name collisions or type changes; never deletes |
| Instance image creation | Nix-built apps VM image imported automatically; image alias updates do not replace disks |
| Config/device overlays | Declared keys overwrite matching keys; other keys survive. Removing an overlay key does not remove its previous Incus value; remove explicitly after review |
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

No live migration, GPU/USB passthrough, TPM enrollment, public admin UIs or Proxmox Backup
Server-compatible backup format is claimed. Guest image import is initialization, not guest
configuration deployment. Update an existing NixOS VM through `nixos-rebuild` over SSH/Incus.
