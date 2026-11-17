# Incus-native rebuild execution plan
Owner: `incus-native`; ancestor: `3e3280e63d9afa4f4ff5d09f8641d90c2cb98539` (before the first Portainer-bearing implementation).
Original main: `c12dcd897dff081b1973d5039edea6da9e8ee817` preserved in `archive/pre-incus-native-main-2026-11-17`.
No phase is complete merely because its source exists: it requires VM/system tests and data-preservation evidence.

## Phase 0 — Source and contract (first, mandatory)
- [x] Create branch from pre-Portainer ancestor; create immutable named backup for old `main`.
- [x] Produce goal, structure, upgrade safeguards and per-file COPY/TRANSLATE/REMOVE manifest.
- [x] Transplant the archived main snapshot with source provenance; **legacy modules are still being translated and must be removed before release**.
- [x] Reset `main` to the original pre-Portainer SHA with a checked expected SHA, after archiving the old main. **Installation and update from main are unavailable until a qualified merge.**

## Phase 1 — Web UI first
- [x] Transplant existing portal frontend + auth/CSRF backend and add an authenticated read-only declarative source inventory (not yet a deployer).
- [ ] Native Projects navigation, local checksum preview and **opt-in UTF-8 text editing with exact-byte binary preservation** are implemented; source staging is available. Reviewed publication, deploy/logs/revisions, and removal of host-side legacy proxies remain open.
- [x] Bounded administrator-reauthenticated private staging plus separate reviewed **source-only** publication. Immutable SHA-256 revisions live outside the Nix flake with CAS drift rejection, per-publication password check, previous revisions retained and safe retry. No evaluation, image build, guest creation or Incus apply.
- [ ] Browser tests cover navigation, local edits, byte-exact uploads and source publication; full first-admin, secret authorization, corrupted-state recovery and end-to-end Incus deploy tests still required.

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
