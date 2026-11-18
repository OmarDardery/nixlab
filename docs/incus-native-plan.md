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
- [x] Guarded Nix source-only transaction and independent typed evaluator: reviewed file edits, exact SHA-256 candidate/plan hashes, isolated DynamicUser Nix evaluation without writable host store/daemon, source publish/rollback, recovery and CLI/HTTPS controls. The evaluator checks NixLab's pinned schema, not arbitrary top-level flake outputs; broader inputs and resource kinds remain gated.
- [x] Reviewed source+existing-owned Incus resource plan, exact ETags, durable async apply/retry, crash recovery, no-op, health and ownership. Live guest mutation and journal failpoint are included in foundation VM tests; arbitrary new-resource creation remains #133.
- [ ] Incus project/instance/OCI, profile, network, storage and device adapters with dependency graph.
- [ ] Byte-exact Dockerfile/Compose import with build context and paths, builder sandbox, OCI digest pinning and documented Compose subset.
- [ ] SOPS/age secret reference handling and scoped delivery/rollback failure semantics.

### Independent project transaction scope

PR #149 ports the source-transaction work from abandoned PR #141 onto `incus-native`. It supports review, publication, interrupted source recovery, and a narrow, independently journaled native deploy for **pre-existing explicitly NixLab-owned** guest CPU/memory changes. Unapproved Nix input never reaches a privileged evaluator, OCI builder or guest. The broker refuses instance creation/deletion, Compose ownership, volume mutation, USB/mount changes, runtime SOPS injection and automatic adoption until #133/#139/#134 land. Source-only rollback does not restore guest data, nor undo an already applied guest limit.

A root-owned `/srv/nixlab` parent is declared by the NixOS module. Before the first project review, an administrator can **initialize or restore** `/srv/nixlab/projects` from a previously pinned `flake.nix` and `flake.lock` in the Web UI or via `sudo nixlab-admin projects initialize --flake-path … --lock-path …`. Initialization never executes the provided flake, fetches network inputs, or rebuilds NixOS. Preserve both source files in Git for subsequent host restores. Manual typed workspace evaluation may use `nix eval` against its pinned `nixlabProjects` output, while privileged review always uses the trusted installed schema in the isolated evaluator to prevent user-supplied flake code from obtaining host privileges. Project transaction approval does not perform `nixos-rebuild`.

### Immutable uploaded source binding (PR #150)

A published Dockerfile/Compose revision under private `/srv/nixlab/project-sources/<project>/revisions/<sha256>/` can be **referenced** from a reviewed typed project Nix transaction by `sourceRevisions: { "<project>": "<sha256>" }` in the broker API or the Projects editor. The broker checks that the project exists and is enabled, checks the current revision, validates the immutable private manifest and every original source byte, and commits only nonsecret revision metadata into the exact `planHash`. On publish/deploy it rechecks hashes and the current pointer: rotated or tampered revisions require a new review. Application files never enter the world-readable Nix flake source store.

Binding does **not** build or deploy a Dockerfile/Compose application. The broker explicitly refuses to mark a bound OCI source deployed until #139's isolated builder and #133's dependency-ordered Incus adapter are qualified. Existing-owned Incus CPU/memory changes remain available for transactions **without** OCI build requirements. Runtime secret decryption/injection remains #134.

The foundation NixOS/Incus VM integration exercises review, source publish, real Incus write, an injected crash after write but before a verified journal, explicit resume that verifies instead of rewriting, preservation of guest data, and an independently reviewed forward restore. CI qualification does not imply physical USB or stateful application backup/restore coverage.

## Phase 4 — Backups, existing-host migration, qualification
- [ ] Encrypted, restorable off-host backup for each project's data plus safe remote nonsecret source checkpoint.
- [ ] Read-only legacy VM inventory; export/import all named volumes, images/config and service state. Existing legacy apps VM is **never silently destroyed**.
- [ ] Independent host replacement test using source, keys and off-host backup, including stateful applications and Klipper-like USB guests.
- [ ] Installer, Web UI, CLI, Incus, OCI, destructive action guard, rollback and recovery CI all green.
- [ ] Only after complete parity: merge `incus-native` into reset `main`, close fulfilled issues, publish release notes and cutover instructions.

## Integration rules
Incremental commits, no generated credentials, no hidden Docker daemon, no silent guest creation/deletion, and no claims that green unit tests alone prove backup restore or USB access. Keep old `main` reachable by the archive branch for debugging and data migration.
