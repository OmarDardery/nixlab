# Configuration contract: legacy baseline

This records the behavior at commit e9062ac6e713e634759b0ddb53417879278a8e06. Preserve these interfaces during migration or document and test their replacement.

## Entrypoints and inventory

`flake.nix` exports `hypervisor`, `apps`, and `bootstrap` NixOS configurations, validation configurations, `nixlabDefaults`, `nixlabSettings`, `nixlabOverview`, and installer, admin, homelab, and apps-image packages. `configuration.nix` explicitly imports platform modules. `site.nix` recursively merges attribute sets from `site-defaults.nix` and `local/settings.json`; lists and scalars are replaced. The setup UI independently checks the inventory shape and validates discovered hardware.

| Consumer | Site fields |
| --- | --- |
| Host composition | `ready`, `system`, `hostName`, `cpuVendor`, `adminKeys`, management and guest addresses, timezone |
| Incus | `apps`, `instances`, guest network, management address, storage driver/source, mounts |
| Network and radios | `management`, `guests`, `radios` |
| Storage | `storage`, `mounts`, `backup.stagingDirectory` |
| Security and administration | `adminKeys`, `setup`, management interface/address/trusted CIDRs |
| Applications and onboarding | `apps`, `guests`, `setup`, `management`, `adminKeys`, timezone, monitoring, secrets |
| Backup, tunnel, monitoring | `backup`, `tunnel`, `monitoring`, `apps.enableDemo`, secrets |
| Maintenance | `maintenance.trim`, `maintenance.optimiseStore`, timezone |

`instances` and `mounts` are lists and are replaced as a whole. Instances and secret paths are source-managed. A migration must report unsupported values rather than discard them and must avoid two competing authorities.

The installer imports a conventional `/etc/nixos` installation, copies `.nix` files, rejects symlinks, and preserves the original configuration and system generation. Referenced non-Nix assets and external flake trees are not generally imported. Preserve the installed `system.stateVersion` and working management network.

## Runtime and data

Incus preseed configures the default project, profile, bridge and pool. The instance reconciler refuses unowned instances and does not delete a guest whose declaration is removed. It merges declared config and devices into existing objects, so removed keys are not pruned; profiles are assigned from the declaration. This is not field-level reconciliation. Apps VM, Docker and Portainer are optional. Guest disks, databases, runtime Incus state and Portainer-only changes are mutable data, not recoverable from Nix configuration alone.

Mounts use existing filesystem UUIDs; automatic formatting is unsupported. An Incus backing mount must exist before dependent services start. Storage and guest network changes require an explicit migration. A rebuild must not erase data.

## Transactions and trust

The HTTPS frontend runs unprivileged and forwards structured requests to a restricted root broker. Browser requests require authentication and mutation requests require CSRF validation. Issued Incus browser certificates are administrative credentials; the SSH operator has full sudo. Secret contents remain in runtime paths rather than ordinary settings.

A transaction snapshots an approved source set, hashes it, builds and reviews the candidate, switches in a separate worker, and requires confirmation. The snapshot includes tracked files, hardware configuration, installed `.nix` files and the two allowed local JSON files; arbitrary untracked modules are not supported. Recovery saves settings, lockfile, selected secret files and TLS material, then restores the prior OS generation. It is not whole-revision recovery for arbitrary new files and cannot undo guest data or incompatible Incus database changes. Direct `nixos-rebuild switch` does not invoke the portal confirmation guard.

The backup implementation stops running guests together, exports guests and custom volumes, and attempts to restart originally running guests. Replace this only with a tested consistency policy. Backups and restore drills remain separate from system configuration.

## Validation boundary

CI evaluates configuration variants, verifies defaults and minimal services, builds selected targets, and dry-runs the complete host dependency plan. It does not establish physical guest boot, real network-loss rollback, disk failure recovery, or an end-to-end restore. Migration checks should compare effective resources and service settings rather than source text or derivation hashes.
