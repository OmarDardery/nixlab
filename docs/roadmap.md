# NixLab roadmap — declarative projects on Incus

**Authoritative target:** [Target architecture](target-architecture.md). The
product is a **NixOS host + NixLab Web UI + Incus/Incus UI + Cockpit**.
Incus Compose manages compatible OCI stacks. Uploaded **Dockerfiles,
Compose files and build contexts are preserved verbatim**; Buildah/Podman
builds OCI images without a Docker daemon. **No Arcane/Portainer/apps VM**
is installed in the final product.

## Customer workflow (one control plane)

1. **Install:** a NixOS host receives NixLab, Incus/Incus UI and Cockpit.
   Enrollment is browser-first; no printed bootstrap password or Arcane setup.
2. **Create/import:** in **Projects**, add native Nix/Incus projects, a raw
   Dockerfile/Containerfile with its build context, original Compose/YAML,
   a full source archive or a pinned Git revision.
3. **Review:** NixLab stores upload bytes unchanged and generates **separate**
   Nix/Compose metadata, evaluates the typed project inventory, compares with
   Incus, validates Compose/build support and previews resource/data/outage
   impacts for exact revision approval.
4. **Build/apply:** isolated pinned Buildah/Podman builds OCI images; pinned
   Incus Compose imports/deploys stacks on Incus. Native system containers
   and VMs use NixLab's existing Incus reconciler. No host rebuild.
5. **Operate:** inspect project state, drift, files, secret assignments,
   image digest, runtime logs and backup coverage in NixLab; deep guest
   operations remain in Incus UI and host administration in Cockpit.
6. **Back up once:** a Web UI wizard writes `projects/backups.nix`. Managed
   project source, original Dockerfiles/Compose and encrypted credentials
   are included automatically; project-specific data and volumes use
   declared consistent snapshots. GitHub via SSH backs up versioned source;
   Restic + Google Drive/rclone backs up encrypted guest data. An independent
   offline encrypted key kit completes recovery.
7. **Recover:** reinstall NixOS/NixLab, import original age/Restic credentials,
   fetch source and guest state, review host-specific changes and verify
   the restored application.

## One authoritative file tree

- `/etc/nixos` — **host** platform source and machine policy.
- `/srv/nixlab/projects/flake.nix` — **one independent projects flake**.
- `/srv/nixlab/projects/PROJECT/default.nix` and sorted recursive
  `modules/**/*.nix` — typed project desired state.
- `/srv/nixlab/projects/PROJECT/source/**` — untouched uploaded Dockerfiles,
  build context, original Compose and override files. Never auto-import
  `source/*.nix` as trusted Nix modules.
- `/srv/nixlab/projects/PROJECT/secrets.enc.yaml` — SOPS ciphertext;
  `/var/lib/nixlab/keys/projects.agekey` stores the **private** age identity.
- `/srv/nixlab/state` or Incus volumes — persistent runtime data, not Git.
- `/var/lib/nixlab/revisions` — immutable reviewed/deployed source revisions;
  `/var/lib/nixlab/uploads` — untrusted quarantined files.
- `/srv/nixlab/projects/backups.nix` — one approved declarative backup policy.

## Delivery order and acceptance

| Stage | Issue | Done when |
| --- | --- | --- |
| 1. Workspace source | [#127](https://github.com/OmarDardery/nixlab/issues/127) | Recursive deterministic flake discovery, safe file preflight, immutable reviewed snapshots |
| 2. Project transactions | [#135](https://github.com/OmarDardery/nixlab/issues/135) | Authorized project-only apply and restart-safe journals, no host rebuild |
| 3. Incus ownership | [#133](https://github.com/OmarDardery/nixlab/issues/133) | One owner per resource, native guest/Compose compatibility and drift blocking |
| 4. Raw app source & OCI builds | [#139](https://github.com/OmarDardery/nixlab/issues/139) | Dockerfile/Compose files are **byte-exact**, isolated build and immutable-image cache invalidation |
| 5. Secrets | [#134](https://github.com/OmarDardery/nixlab/issues/134) | SOPS+age key create/import, project-level delivery, independent recovery kit |
| 6. Browser workflows | [#128](https://github.com/OmarDardery/nixlab/issues/128) | Upload/edit/review/build/deploy/operate projects in Web UI |
| 7. Declarative backup engine | [#129](https://github.com/OmarDardery/nixlab/issues/129) | One-time dynamic policy, comprehensive source and persistent-data coverage |
| 8. Off-host backup destinations | [#130](https://github.com/OmarDardery/nixlab/issues/130) | GitHub SSH/source + encrypted Restic/Google Drive and separate recovery key |
| 9. Final Arcane code retirement | [#126](https://github.com/OmarDardery/nixlab/issues/126) | No Arcane units/routes/settings/builds/installer; legacy guest data preserved and migration independently verified |
| 10. Qualification | [#131](https://github.com/OmarDardery/nixlab/issues/131) | Exact-version install/update, real OCI/VM/USB, multi-service data restore on second host |

Documentation PR [#132](https://github.com/OmarDardery/nixlab/pull/132)
and project evaluator PR [#136](https://github.com/OmarDardery/nixlab/pull/136)
are **merged**. This does not mark the upload UI, build adapter, project
deployment or backup automation as shipped. In-progress
[#137](https://github.com/OmarDardery/nixlab/pull/137) deprecates new Arcane
provisioning; [#138](https://github.com/OmarDardery/nixlab/pull/138) inventories
legacy workloads before migration. Neither deletes guest data or completes #126.

**Release barriers:** untrusted upload cannot execute Nix or Dockerfile
during import; no Docker daemon or browser Incus socket; no unapproved guest
stop/recreation/volume deletion; no plaintext credentials in Nix store/Git;
file edits actually force a new build (Incus Compose caches by image alias);
physical data and encrypted key material can be restored on a new machine.
Every claim requires tests and provenance at the exact released revision.

## Longer-term scope

Only after this single-host contract is qualified: advanced host networking,
optional service publishing, hardware integrations, energy policies and
multi-host orchestration. They must reuse the existing review/ownership
machinery and cannot introduce a competing declarative engine, backup system,
container daemon or management UI.
