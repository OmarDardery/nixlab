# NixLab Incus-native — architecture contract
Status: target contract (implementation is not yet certified). Source branch: `incus-native`, rooted at pre-Portainer commit `3e3280e63d9afa4f4ff5d09f8641d90c2cb98539`.

## Single goal
NixLab is a **NixOS host plus Incus-native homelab control plane**, administrable from a single NixLab Web UI. There is **no Portainer, Arcane, Docker daemon, legacy applications VM, or separate container control plane** in a new installation. Incus owns instances, OCI workloads, networks, storage, device passthrough and state; the NixLab broker mediates all privileged mutations and persistent project intent.

## User journey
1. `sudo nix run github:OmarDardery/nixlab#install` on supported NixOS. The installer configures only base services needed for onboarding. It does not print generated passwords or require SSH keys.
2. Open the local Web UI, create the first administrator using one-time **console approval**, configure disks/network/Incus, and review all operations with reauthentication and a preview of consequences.
3. Create a project in the Web UI using typed Nix resources or **upload original Dockerfile, Compose YAML, build contexts, environment templates, and related files byte-for-byte**. Preserve their original paths and bytes. Editing is opt-in; generated wiring lives separately.
4. Review an immutable project revision including source hashes, image builds, Incus resource diffs, secret references, persistence and network access. Approve; observe asynchronous status, health, audit logs, and safe retry.
5. Restore an independent host from declarative configuration and **off-host encrypted data backups**. A source-only rollback never claims to recover disks or databases.

## Runtime and security boundaries
- Host: NixOS, `incusd`, NixLab Web UI + existing authenticated root broker, Cockpit (optional), Incus UI (optional). No host-level Docker socket.
- Projects: `/srv/nixlab/projects/<project>/`, each with a typed, separately evaluated and versioned configuration. Project deployment **never runs `nixos-rebuild`**.
- Original application sources: `/srv/nixlab/project-sources/<project>/revisions/<digest>/source/` holds immutable, root-owned Dockerfile/Compose/build-context bytes, **outside** the separately evaluated Nix flake and Nix store. A reauthenticated source-only review moves the `current.json` revision reference; an independent resource/secret/build review is still required for deployment.
- Workloads: Incus projects/instances/OCI containers/VMs with explicit resources, profiles, networks, volumes, owner labels, dependency order, ETag protection and health checks.
- OCI builds: isolated, resource-limited, unprivileged or dedicated builder using pinned Buildah/Podman tooling. Build output is an OCI artifact verified by digest before import to Incus. No user Dockerfile runs during upload, parsing or preview. Unsupported BuildKit/Compose features fail closed; no silent semantic rewrites.
- Compose: preserve original YAML and relative paths, render a **separate** audited native Incus deployment plan. Support an explicitly documented Compose subset, with exact unsupported-feature errors rather than a hidden Docker engine.
- Secrets: SOPS/age encrypted at rest, **private identities outside the Git tree and public Nix store**. Never commit uploaded plaintext `.env` or expose secrets in plan, API, logs, backups or derivations. Secret delivery has a scoped failure state and audit.
- Concurrency and failure: reuse existing broker, global lock, durable journal and source snapshot/ETag checks. An interrupted worker is recoverable; no unreviewed disk deletion, guest adoption or restart.
- Host-owned instances and legacy workloads have distinct owners. Adoption/migration is explicit and rollback-safe.

## Architecture invariants
**Incus is the only application runtime. The NixLab broker is the only privileged project change path. Web UI is the only setup requirement after launch. Every deployed revision is source-attributable, auditable, rebuildable, and separately restorable.**

## Transitional safety
Current production `main` is archived as `archive/pre-incus-native-main-2026-11-17` before any ref rewrite. The archive is **source/history only, not a live Incus volume backup**. Existing machines must not auto-adopt/delete an applications VM. No legacy data is removed until verified export/import and an independent restore drill.
