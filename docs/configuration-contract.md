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

Incus preseed configures reserved infrastructure. A drift guard refuses native edits to source-owned infrastructure; ordinary Incus objects remain native-owned. The instance reconciler refuses unowned instances and does not delete a guest whose declaration is removed. Last-applied tracking removes previously managed config/device keys only without conflicting runtime drift; unowned keys survive. Whole-device removal/backing changes require migration, and profiles are assigned from the declaration. Apps VM, Docker and Portainer are optional. Guest disks, databases, runtime Incus state and Portainer-only changes are mutable data, not recoverable from Nix configuration alone.

Mounts use existing filesystem UUIDs; automatic formatting is unsupported. An Incus backing mount must exist before dependent services start. Storage and guest network changes require an explicit migration. A rebuild must not erase data.

## Transactions and trust

The HTTPS frontend runs unprivileged and forwards structured requests to a restricted root broker. Browser requests require authentication and mutation requests require CSRF validation. Issued Incus browser certificates are administrative credentials; the SSH operator has full sudo. Secret contents remain in runtime paths rather than ordinary settings.

A transaction snapshots an approved source set, hashes it, builds and reviews the candidate, switches in a separate worker, and requires confirmation. The snapshot includes tracked/manifest source plus reviewed machine settings, modules and installed `.nix` files. Platform source and machine state are published and recovered together. Arbitrary files outside the approved tree are excluded. Recovery also preserves selected secret files and TLS material and restores the prior OS generation. It cannot undo guest data or incompatible Incus database changes. Direct `nixos-rebuild switch` does not invoke the portal confirmation guard.

The evaluated preseed and `nixlab.incus.desiredInstances` input are shared with the
impact planner. Each new candidate binds a root-only runtime snapshot and public
versioned plan to its transaction. Apply rechecks in both the broker and worker;
drift refreshes the plan and refuses that attempt, while migration/conflict/unknown
observation blockers cannot be retried past. See [ownership](ownership.md#preview-the-change).

Scheduled backup stops/exports/recovers one consistency group at a time. Shared writable volumes join consumers, including projects sharing the default volume namespace. Explicit groups extend that boundary. Excluded/manual guests skip their whole group and produce partial coverage. A completed generation publishes atomically after exports, checksums and recovery kits, retains two completed local generations, and leaves legacy archives untouched. Restic transfers completed generations independently; interrupted staging is excluded. Backups and restore drills remain separate from system configuration.

## Validation boundary

CI evaluates configuration variants, verifies defaults and minimal services, builds packages/host artifacts and runs portal and Incus-container VM tests. A configured gate is not evidence of a successful run. It does not establish physical USB/NIC behavior, real network-loss rollback, disk failure recovery or an end-to-end installed-host restore. Migration checks compare effective resources and service settings rather than derivation hashes. See [validation evidence](validation.md).

## Settings schema and projections

`schemaVersion` is source-owned and currently 1. Missing version means legacy
schema 0. Pure `lib/settings-migrations.nix` preserves the legacy rule that a
ready host without an explicit `monitoring.collection` collects metrics; explicit
false stays false. Current versions are unchanged, migration is idempotent, and
unknown future versions fail before review/build. Directory-layout version 1 in
`machine/format.json` is unrelated.

The broker evaluates the selected source's `site.nix` with bounded local
`nix-instantiate --eval --strict --json`; it does not fetch flake inputs or clone
migration rules in Python. Nix must be available. Failure never falls back to a
Python interpretation or exposes raw evaluator output. The existing
`nixlabDesiredSettings` flake projection and internal `nixlab.desiredSettings`
represent normalized source intent before host-module overrides. The build worker
captures desired and effective settings from the same evaluation and binds the
candidate to its existing source digest. Overrides retain their existing Nix
module precedence. A legacy candidate source lacking the desired projection is
reported as unavailable rather than inferred from effective settings.

Reading does not rewrite JSON. Review stages normalized settings in the candidate;
only guarded activation publishes them. The portal review and CLI update show a
schema transition when applicable; status exposes stored/desired schema versions.
No secret values belong in this input or either projection.

Installer retries read the selected planning source's `site.nix` through the same
Nix reader. They must not merge fresh defaults directly over legacy JSON: that
bypasses compatibility rules such as collection on an already-ready legacy host.
New adoption starts from explicit fresh defaults. Installer source publication and
its recovery boundaries are tracked separately from the pure migration contract.
