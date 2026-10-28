# Release scope and future extensions

The current release is a single-host NixOS/Incus appliance for a homelab laptop or
server. Implementation and target-hardware acceptance are tracked separately.
See [operations](operations.md), [installation](iso.md) and the
[commissioning checklist](commissioning.md).

## Implemented release workflows

| Area | Delivered behavior |
|---|---|
| Installation | Bootable minimal ISO; guided, explicitly confirmed single-disk installer; BIOS/UEFI configuration; manual advanced path; preserved network profiles; installed-system first-boot setup |
| Configuration | Typed inventory, recursively discovered machine modules, reviewed uploads, immutable builds and confirmation/recovery; one project repository |
| Updates | Reviewed full-SHA platform upgrades, machine-state preservation, file/input impact records and source/OS rollback; package refresh and explicit reboot policy |
| Daily operations | On-demand guest summary and ownership/drift, allowlisted lifecycle/snapshot/export jobs and operation history; Incus UI for deep administration |
| Recovery | New-name stopped restores, checksum validation, autostart suppression, address/shared-volume release checks, off-host archive registration and durable restart intent |
| Backups | Offline guest/custom-volume exports plus configuration/runtime identity kits; independent encrypted restic destination and explicit retention |
| Access | Administrator/operator/viewer portal roles, reauthentication, session revocation, existing root broker boundary, separate native Incus and SSH permissions |
| Optional features | File-discovered catalog for laptop policy, SMART, private Tailscale SSH, history, applications VM and demo; runtime-credential failure webhooks |
| Support | Packaged local guides, release identifiers, portable configuration/encrypted-secret kits and bounded diagnostic report |

Automated gates include source/security tests, NixOS configuration and selected OS
builds, a booted portal/UNIX-permission integration test, and BIOS/UEFI live-media
checks with a disposable disk in dry-run mode. Actual guided installation and
first-boot conversion on the target, guest boot, radio/firmware behavior, interrupted
activation and a verified off-host workload restore remain acceptance testing.

## Separate future product scopes

These are not hidden commissioning steps or capabilities claimed by this release:

- Multi-host clustering/HA, quorum, fencing and shared-storage failover qualification.
- Multi-tenant isolation, an Incus/OpenFGA authorization integration and cross-service SSO.
- A broad third-party application catalog or automatic compatibility with arbitrary
  Proxmox/community scripts. Current helper links are references; packaged recipes
  never download or execute those scripts.
- Graphical RAID/LUKS/ZFS provisioning, online pool migration/shrinking and automatic
  Proxmox disk/network conversion. Native/manual migration remains explicit.
- PBS compatibility, arbitrary database-consistency plugins and automatic data rollback.

Configuration changes remain reviewed Nix intent. Mutable disks, databases,
certificates, snapshots and runtime identities require independent protection.
Do not equate OS rollback, a successful backup job or an image build with recoverability.
