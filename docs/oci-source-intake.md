# OCI source intake and build-boundary qualification (#139)

This document describes the **source-only** milestone and its next security gates. It does not claim Dockerfile execution, OCI import or Incus Compose deployment is complete.

## Supported source intake

- Upload multiple original files, a folder, or **one explicitly selected** ZIP, TAR, TAR.GZ or TGZ archive from the Projects browser UI. Copy original file bytes and nested relative names without rewriting YAML, Dockerfiles or `.dockerignore`.
- Source is stored in root-owned staging, published into immutable revision directories at `/srv/nixlab/project-sources/<project>/revisions/<sha256>/source/`, **never in the world-readable Nix store**. The existing separate reauthenticated review/publish contract applies.
- Direct and unpacked source quotas: at most **256 files**, **64 MiB per file**, **128 MiB total**, **12 path components**; archives at most **64 MiB compressed** and 1024 entries including directories. Realistic binary assets are supported up to these limits without silent truncation.
- ZIP/TAR extraction is data-only, bounded on archive metadata and actual bytes, refuses duplicate paths, traversal, absolute paths, backslashes, unsupported characters, symlinks, hardlinks, sparse entries, devices, FIFO, encrypted ZIP members, `.env` files, private keys, `.git`, and other unsafe names. The extracted manifest, not the compressed-file name, becomes the SHA-256 revision identity. A failed extraction leaves no published project or Incus mutation.
- Published source inspection returns exact file path/hash/size metadata. A Dockerfile-only source produces a separate, read-only generated Compose launch preview without adding a Compose file to the uploaded revision. The plan's `buildContextHash` and proposed image identity depend on the entire source revision, **including every context file**, not only the Dockerfile. An existing Compose source is preserved with its original paths and requires pinned parser/compatibility validation.
- High-confidence BuildKit-only features are flagged as unsupported, but source remains unchanged. The preview is **not** an approval to build or deploy.

## Next required gates before any build or deployment

1. Run the **pinned** `incus-compose config --quiet` validator on a disposable, read-only context in an unprivileged sandbox. Forbid access to the host Nix store secrets, Incus socket, Git deploy key, /etc credentials, block devices and user guest volumes. Show unsupported Compose/BuildKit constructs; never silently translate. Preserve `compose.incus.yaml` and produce a *separate* NixLab override only.
2. A non-root, resource-capped **Buildah/Podman builder worker** receives only the frozen and reverified nonsecret context. Network registry egress must be opt-in/allowlisted. It exports an OCI image artifact and provenance digest; no Docker daemon, direct Incus socket or root host mounts enter the sandbox. On timeout/error, keep previous images/guests untouched.
3. The trusted broker verifies the artifact digest and imports it into the exact Incus project using a source-qualified alias, with ETag and ownership checks from #133. **Never run `incus-compose up --build`** as privileged action. Instead invoke pinned `incus-compose up --no-build` with separate image overrides; review every recreate/outage and protect named volumes.
4. #134 must deliver original Compose `.env` and `env_file:` secret values from SOPS only into ephemeral, root-controlled runtime paths with cleanup and no Buildah access. Refuse deployment on missing or unverified keys/secret references.
5. Full two-service Compose with nested relative Dockerfile/context, persistent named volume, source-only no-op, changed context rebuild, tampered source denial, unauthorized mounts, failed build rollback, and fresh-host restored app tests must all pass before closing #139.

## Scope

NixLab is proceeding with a **clean-slate** installation. There is **no legacy Docker or Portainer/Arcane data migration to perform**. Do not create migration shims or quietly delete unknown host state.

This milestone does not install a builder, alter Incus, permit Dockerfile execution, decrypt secrets or rebuild the host OS. Dockerfile/Compose imports remain inert until every required release gate is qualified.
