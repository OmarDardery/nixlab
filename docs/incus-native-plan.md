# Incus-native rebuild execution plan
Owner: `incus-native`; ancestor: `3e3280e63d9afa4f4ff5d09f8641d90c2cb98539` (before the first Portainer-bearing implementation).
Original main: `c12dcd897dff081b1973d5039edea6da9e8ee817` preserved in `archive/pre-incus-native-main-2026-11-17`.
No phase is complete merely because its source exists: it requires VM/system tests and data-preservation evidence.

## Phase 0 — Source and contract (first, mandatory)
- [x] Create branch from pre-Portainer ancestor; create immutable named backup for old `main`.
- [x] Produce goal, structure, upgrade safeguards and per-file COPY/TRANSLATE/REMOVE manifest.
- [ ] Restore only relevant current-source modules with provenance; remove obsolete app VM and Arcane code.
- [ ] After baseline docs exist and old main is backed up, reset `main` with a checked expected SHA (destructive repository reference change requested by maintainer). Document update outage while this branch is incomplete.

## Phase 1 — Web UI first
- [ ] Transplant existing portal frontend + auth/CSRF backend, preserving Web UI design/accessibility.
- [ ] Remove Arcane UI/cards/redirects/proxy; introduce projects list/import/file tree (byte exact), review/deploy/log/recovery statuses.
- [ ] Project uploads are bounded and are **not** executed or stored in the Nix store when plaintext secrets are present.
- [ ] Browser and permission tests cover first admin account, no credentials printed during setup, upload drift, authorization and secret redaction.

## Phase 2 — NixOS/Incus host baseline
- [ ] Port flake, module graph and installer from archived `main`; exclude old Docker VM entirely.
- [ ] Preserve NixOS installation, Incus UI, Cockpit, networks, storage, backup, KVM and USB passthrough.
- [ ] Clean fresh host installs with `#install`; existing hosts require explicit migration path.
- [ ] Validate actual NixOS VM and physical-host release gate, not solely syntax.

## Phase 3 — Project engine, native resources, OCI/Compose
- [ ] Independent project flake and constrained Nix evaluator (no host secrets/Incus access; no writable host Nix store).
- [ ] Reviewed source+resource plan, exact ETags, durable async apply/retry, crash recovery, no-op, health and ownership.
- [ ] Incus project/instance/OCI, profile, network, storage and device adapters with dependency graph.
- [ ] Byte-exact Dockerfile/Compose import with build context and paths, builder sandbox, OCI digest pinning and documented Compose subset.
- [ ] SOPS/age secret reference handling and scoped delivery/rollback failure semantics.

## Phase 4 — Backups, existing-host migration, qualification
- [ ] Encrypted, restorable off-host backup for each project's data plus safe remote nonsecret source checkpoint.
- [ ] Read-only legacy VM inventory; export/import all named volumes, images/config and service state. Existing legacy apps VM is **never silently destroyed**.
- [ ] Independent host replacement test using source, keys and off-host backup, including stateful applications and Klipper-like USB guests.
- [ ] Installer, Web UI, CLI, Incus, OCI, destructive action guard, rollback and recovery CI all green.
- [ ] Only after complete parity: merge `incus-native` into reset `main`, close fulfilled issues, publish release notes and cutover instructions.

## Integration rules
Incremental commits, no generated credentials, no hidden Docker daemon, no silent guest creation/deletion, and no claims that green unit tests alone prove backup restore or USB access. Keep old `main` reachable by the archive branch for debugging and data migration.
