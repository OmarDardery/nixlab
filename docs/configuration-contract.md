# Configuration contract

Updated for the focused host foundation, 2026-10-29. The
[ownership contract](ownership.md) defines the boundary with Incus.

## Entrypoints and inventory

`flake.nix` exports `hypervisor`, `apps`, and `bootstrap` NixOS configurations, validation configurations, `nixlabDefaults`, `nixlabSettings`, `nixlabOverview`, and installer, admin, homelab, helpers, and apps-image packages. `configuration.nix` imports platform modules. `site.nix` merges defaults with machine settings; legacy `local/settings.json` is supported for migration. Attribute sets merge recursively; lists and scalars are replaced. The portal independently checks inventory shape and discovered hardware.

| Consumer | Site fields |
| --- | --- |
| Host composition | `ready`, `system`, `hostName`, `cpuVendor`, `adminKeys`, management and guest addresses, timezone |
| Incus | `apps`, `instances`, guest network, management address, storage driver/source, mounts |
| Network and radios | `management`, `guests`, `radios`, `connectivity` |
| Hardware prerequisites and helper runner | `hardware.serialAliases`, `helpers.enable` |
| Storage | `storage`, `mounts`, `backup.stagingDirectory` |
| Security and administration | `adminKeys`, `setup`, management interface/address/trusted CIDRs |
| Applications and onboarding | `apps`, `guests`, `setup`, `management`, `adminKeys`, timezone, monitoring, secrets |
| Backup, tunnel, monitoring | `backup`, `tunnel`, `monitoring`, `apps.enableDemo`, secrets |
| Maintenance | `maintenance.trim`, `maintenance.optimiseStore`, timezone |

`instances`, `mounts`, bridge/VLAN declarations and serial aliases are lists replaced as a whole. `backup` adds `schedule`, `groups`, `exclude` and `reserveMiB`. Instances and secret paths are source-managed. A migration must report unsupported values rather than discard them and must avoid two competing authorities.

The installer imports a conventional `/etc/nixos` installation, copies `.nix` files, rejects symlinks, and preserves the original configuration and system generation. Referenced non-Nix assets and external flake trees are not generally imported. Preserve the installed `system.stateVersion` and working management network.

## Runtime and data

Incus preseed configures reserved infrastructure. A drift guard refuses native edits to source-owned infrastructure; ordinary Incus objects remain native-owned. The instance reconciler refuses unowned instances and does not delete a guest whose declaration is removed. It merges declared config and devices into existing objects, so removed keys are not pruned; profiles are assigned from the declaration. Apps VM, Docker and Portainer are optional. Guest disks, databases, runtime Incus state and Portainer-only changes are mutable data, not recoverable from Nix configuration alone.

Mounts use existing filesystem UUIDs; automatic formatting is unsupported. An Incus backing mount must exist before dependent services start. Storage and guest network changes require an explicit migration. A rebuild must not erase data.

## Transactions and trust

The HTTPS frontend runs unprivileged and forwards structured requests to a restricted root broker. Browser requests require authentication and mutation requests require CSRF validation. Issued Incus browser certificates are administrative credentials; the SSH operator has full sudo. Secret contents remain in runtime paths rather than ordinary settings.

A transaction snapshots an approved source set, hashes it, builds and reviews the candidate, switches in a separate worker, and requires confirmation. The snapshot includes tracked/manifest source plus reviewed machine settings, modules and installed `.nix` files. Platform source and machine state are published and recovered together. Arbitrary files outside the approved tree are excluded. Recovery also preserves selected secret files and TLS material and restores the prior OS generation. It cannot undo guest data or incompatible Incus database changes. Direct `nixos-rebuild switch` does not invoke the portal confirmation guard.

Scheduled backup stops/exports/recovers one consistency group at a time. Shared writable volumes join consumers, including projects sharing the default volume namespace. Explicit groups extend that boundary. Excluded/manual guests skip their whole group and produce partial coverage. A completed generation publishes atomically after exports, checksums and recovery kits, retains two completed local generations, and leaves legacy archives untouched. Restic transfers completed generations independently; interrupted staging is excluded. Backups and restore drills remain separate from system configuration.

## Validation boundary

CI evaluates configuration variants, verifies defaults and minimal services, builds packages/host artifacts and runs portal and Incus-container VM tests. A configured gate is not evidence of a successful run. It does not establish physical USB/NIC behavior, real network-loss rollback, disk failure recovery or an end-to-end installed-host restore. Migration checks compare effective resources and service settings rather than derivation hashes. See [validation evidence](validation.md).
