# NixLab target: declarative projects, encrypted secrets and recoverable Incus data

**Decision (2026-11-17). This is a proposed implementation contract, not an
already shipped feature.** The goal is a reproducible single-host homelab in
which every managed project can be reconstructed from reviewed Nix declarations,
its SOPS-encrypted configuration and independently backed-up persistent data.

NixLab keeps exactly three management surfaces: **NixLab Web UI** for declarative
source, change review, secrets, backup and recovery; **Incus + native Incus UI**
for containers, OCI applications, VMs, projects, networks, devices and volumes;
and **Cockpit** for underlying Linux host administration. NixOS remains the
host configuration and systemd foundation. **incus-compose** is a CLI/client
adapter backed by Incus, not a Docker daemon or additional platform control
plane.

**Fully retire Arcane and the dedicated Docker applications VM from the final
architecture.** This is tracked by [#126](https://github.com/OmarDardery/nixlab/issues/126).
Current NixLab still contains both. An upgrade must preserve existing `lab/apps`
VM disks, named volumes, Compose files, credentials and data; migration is
explicit and never a destructive cleanup step.

## 1. Host configuration is not application configuration

Use one top-level **projects workspace flake**, rather than depending on
multiple independent project flakes to discover one another. Project-specific
files are recursively assembled into an aggregated typed inventory. An optional
nested flake is an explicit resource, **not** an automatically applied host module.

```text
/etc/nixos/                              # NixLab NixOS host source, boot, host policy
  flake.nix, flake.lock
  machine/modules/                      # Explicit, privileged host-only modules

/srv/nixlab/
  projects/
    flake.nix                           # Workspace entrypoint, exposes nixlabProjects
    flake.lock                          # Pinned shared project evaluator dependencies
    .sops.yaml                          # Public age recipients and SOPS creation rules
    backups.nix                         # Web UI generated, reviewed backup policy
    aiostreams/
      default.nix                       # Project entrypoint
      modules/
        containers.nix
        networking.nix
        storage.nix
      compose.yaml
      compose.incus.yaml                # Optional Incus-specific Compose override
      secrets.enc.yaml                  # SOPS ciphertext (safe for Git if reviewed)
      files/                            # Explicit project resources, never auto-executed
    klipper/
      default.nix
      modules/
        container.nix
        usb.nix
        storage.nix
      secrets.enc.yaml
    adguard/
      default.nix
      modules/container.nix
      secrets.enc.yaml
  state/
    aiostreams/                        # Optional declared host-managed persistent data
    klipper/
    adguard/
  backups-local/                        # Temporary local staging, not sole copy

/var/lib/nixlab/                         # Durable transactions/revisions/status
/var/lib/nixlab/keys/projects.agekey     # Canonical root-owned age identity, 0400
/var/lib/nixlab-secrets/                 # Protected runtime/remote/backup material
/var/lib/incus/                          # Incus-managed data in existing pools
```

The `state/` and `backups-local/` directories are optional proposed paths,
not a mandate to move existing Incus volumes. Reference volumes in their native
Incus pool. Preserve old filesystems/mounts on migration and keep images, caches,
Nix store, plaintext credentials and mutable application data **outside** the
Git-tracked declarative source.

The Web UI edits an **untrusted staged workspace** and performs bounded file
validation. Published revisions are immutable, root-controlled snapshots.
Nix expressions and flake inputs can be powerful; they are **privileged code**,
not safely sandboxed just by using `nix eval`. Never auto-run user uploads as
root, silently import them into the host flake or give them the Incus socket.
Administrator approval covers exact reviewed bytes and resulting impact.

## 2. Deterministic, typed automatic project discovery

The workspace flake exposes `nixlabProjects`, an evaluated JSON-serializable
desired-state inventory. Discovery rules:

1. At the top level, discover immediate project directories having
   `default.nix`; ignore non-project directories (`state`, backups, scripts).
2. For each project, import `default.nix` exactly once and recursively load
   `modules/**/*.nix` sorted by normalized relative path. Disable names
   prefixed `_` by documented convention. **No other `.nix` files import
   implicitly**; resources under `files/` and encrypted YAML are inert data.
3. Reject symlink escapes, path traversal, unreadable paths, unsupported file
   types, cycles, excessive depth/size, duplicate project names, duplicate
   Incus resource names and cross-project storage/network ownership conflicts.
4. Evaluate a typed Nix module schema and apply global defaults. Expose the
   validated inventory, origin fingerprints, resource ownership and secret
   **references only**. Never evaluate secret *values* or put them in the Nix store.
5. Record flake input lock hashes and reviewed source revision. A dynamically
   discovered new directory must appear in the next evaluation and be visible
   in the Web UI; it is **not auto-deployed** without review.

```sh
nix eval --json path:/srv/nixlab/projects#nixlabProjects
```

The explicit `path:` reference can include newly created files without Git
staging; source files in this mode become inputs to Nix/store evaluation, so
**only ciphertext and public configuration** may appear in the workspace.
The source must be snapshotted for approval to avoid eval-versus-apply races.

Illustrative project declaration (**not an implemented module**):

```nix
# /srv/nixlab/projects/aiostreams/default.nix
{ ... }: {
  nixlab.project = {
    name = "aiostreams";
    enable = true;
    backend = "incus-compose";
    resources = { cpus = 2; memory = "2GiB"; };
    secrets.file = ./secrets.enc.yaml;
    backup = { enable = true; consistency = "stop"; };
  };
}
```

Use **independent project revision deployments**, not `nix profile add`.
A Nix user profile manages package outputs and is not an Incus service/volume/
device reconciler. `nix build path:...#...` may produce reviewed artifacts;
a separate NixLab broker operation executes the Incus changes. Project
updates **never** implicitly run `nixos-rebuild`.

## 3. Incus ownership and deployment

Default **one NixLab project ↔ one Incus project**, using independent names,
profiles, volumes and networks where Incus supports project isolation. The
Incus project feature flags/restrictions need explicit initialization and
qualification: a project name by itself does not confer complete security
isolation. Keep native Incus edits possible and refuse drift on source-owned
fields. Existing `lab` resources are not auto-adopted.

Each managed resource has exactly **one** deployment owner:

| Owner | Backend | May change |
|---|---|---|
| NixLab native reconciler | `incus-native` | Declared system containers, KVM VMs, resources, devices, profiles and storage |
| Incus Compose | `incus-compose` | Declared OCI services, project-local Compose networks and volumes |
| Incus operator | `native-unmanaged` | Anything not explicitly adopted into the above |

A single project can contain resource references and status for a native guest
**or** a Compose stack; it must not let both reconcilers mutate the same
instance/volume. For complex mixed projects, model separate ownership domains
and explicit references rather than shared mutation authority.

Incus Compose provides project isolation, named volumes, Compose overrides,
health dependencies and file-backed secrets, but implements only a **subset**
of Docker Compose. Its health behavior uses a helper sidecar; network/image
copying requires a properly configured HTTPS Incus endpoint. Test the **exact**
Incus LTS and Incus Compose versions, image and persistent-volume behavior,
build requirements, network APIs, authentication and failures before release.

### Project apply pipeline

1. Stage and validate all source, referenced artifacts and typed inventory.
2. Compare desired resources with live Incus state/ETags and last-applied owner.
3. Show explicit effects: create/change/stop/restart, data migration, outage,
   secret rotation and unresolved ownership/drift conflicts.
4. Require reauthentication and approve a fixed candidate revision using the
   **existing** NixLab guarded root broker, not browser-provided raw commands.
5. Apply only whitelisted operations in dependency order, verify intended health
   and persist exact source and observed deployment revision.
6. On errors, report partial work and resume safely. Configuration rollback
   does **not** roll back database/volume mutations.

Reapply with no changes should be a no-op. Missing declarations must never
implicitly destroy existing instances or volumes; migration, adoption, host
network/device remapping and destructive operations need separate approval.
Maintain the existing physical/USB reconnect and data-loss qualification gates.

## 4. SOPS + age secret lifecycle

The install Web UI offers two mutually exclusive choices:

- **New installation:** generate one cryptographically random age identity at
  `/var/lib/nixlab/keys/projects.agekey`, atomically, root-owned mode 0400.
  Record only the **public** recipient in `.sops.yaml`.
- **Restore installation:** import and verify the previously saved age identity
  **before** decrypting/activating restored project files.

Never regenerate the private key on reboot, package/OS upgrade, installer retry,
key read failure or a corrupt file. Support additional public recipients and
explicit reviewable recipient rotation.

Each project declares a `secrets.enc.yaml` containing **SOPS-encrypted**
credential and environment values. For nonsecret environment settings, ordinary
typed Nix/Compose fields remain appropriate.

The Web UI should allow administrator-only add/change/remove of masked values,
without offering an unrestricted plaintext dump. Broker encrypts and saves
ciphertext atomically. Handle key/recipient errors as failures, not opportunities
to overwrite existing secrets. Do not put plaintext in Git, Nix store,
`environment.*`/readable `user.*` Incus config, plans, API JSON, process
arguments or unredacted logs.

Decrypted secrets should exist only in protected per-service runtime staging
(`/run`/tmpfs, systemd credentials or tightly scoped root-controlled files);
the host age private key **never** enters guests. NixOS system-container services
may use sops-nix/systemd credentials where appropriate. OCI/Compose services
may use its file-based secrets feature after qualifying its actual copy/ownership,
persistence and deletion behavior. A Compose file's `secrets:` entry is not
proof of runtime tmpfs isolation.

### Independently stored recovery kit

Produce an administrator-passphrase-encrypted portable kit containing the age
identity, restic unlock credentials, remote backup tokens/SSH deployment
material needed for recovery, manifest/schema and fingerprints. The kit is an
**explicitly sensitive** artifact, encrypted with a reviewed strong KDF and
format, saved in a **separate trust/failure domain** from the restic repository.
Confirm the kit can actually be unlocked and decrypt a test project. A
GitHub SOPS file or Google Drive restic repository is not a substitute for its
own decryption key. Never emit the kit passphrase or private material in logs.

## 5. One-time declarative backup policy

Web UI collects policy once, writes/reviews `projects/backups.nix`, and
automatically discovers managed projects added later. Source and mutable data
are distinct protection classes.

```nix
# Illustrative API, NOT currently a NixLab module
{
  nixlab.backups = {
    enable = true;
    schedule = "daily";

    configuration.github = {
      enable = true;
      repository = "git@github.com:USER/nixlab-config.git";
      sshKeySecret = "github-deploy-key";
    };

    data.restic = {
      enable = true;
      repository = "rclone:gdrive:nixlab-backups";
      passwordSecret = "restic-password";
    };

    retention = { daily = 7; weekly = 4; monthly = 6; };
    includeProjects = "all";
  };
}
```

One-time means no manual list maintenance: the policy selects `all` for
project **configuration**, and explicit per-project backup declarations
determine **mutable data** coverage and consistency. New project discovery
must not silently claim it is backed up until its data volumes and any
host bind mounts are identified. Include all managed `flake.nix`,
`flake.lock`, `default.nix`, recursive modules, Compose/override files,
SOPS ciphertext, `.sops.yaml`, metadata and backup policy. Exclude all
plaintext credentials, age private keys, SSH private keys, OAuth tokens,
Nix store, caches and OS installation files from Git.

| Backup destination | What it protects | What it does **not** protect |
|---|---|---|
| Private GitHub repository via dedicated deploy key | Non-secret Nix/Compose/metadata revisions and SOPS ciphertext | VM/OCI instance disks, Incus volumes, passwords/private decryption keys |
| Google Drive via rclone-backed encrypted restic | Completed consistent guest/volume/bind mount exports and optionally project snapshots | Key material without an independent recovery kit |
| Local Incus snapshots or staging | Fast local recovery point/immutable staging | Off-host disaster recovery by itself |
| Independently stored encrypted recovery kit | Key/credential material to unlock source+data | Application state until combined with restic |

Prefer **reusing** NixLab's existing grouped Incus exports, atomic generations,
consistency/downtime and restic verification. Avoid a second backup engine,
persistent database or scheduler. To honor **project changes do not rebuild
the host**, install a stable host backup runner/timer once and let its privileged
runtime read/evaluate the latest approved project policy and decide whether a
scheduled run is due. Do not make every `backups.nix` edit a NixOS host
`services.restic.backups` re-evaluation. Any timer tuning must be reconciled
without conflicting with NixOS-owned unit files.

GitHub deploy-key onboarding must show only the public key, require the
administrator to add it with correct repository write access, verify SSH
host-key identity and use the least possible privileges. GitHub remains an
optional source checkpoint, not a database backup. Google Drive onboarding
should use a qualified rclone remote and protected OAuth refresh material
outside Git and Nix; restic repository `rclone:<remote>:<path>` syntax is
supported. Users need a reliable reauthorization path if Google credentials
expire or are revoked.

### Backup state machine and evidence

1. **Capture** the exact approved project source revision, SOPS ciphertext,
   manifests and expected project/volume/secret coverage under a lock.
2. **Preflight** permissions, quota, remote auth, free storage, data consistency
   prerequisites and independent recovery-key availability.
3. **Quiesce/export** grouped Incus instances/volumes or service-specific dumps
   per declared policy. Always attempt restart after interruption.
4. **Transfer** Git source separately from encrypted restic data generations.
   Never publish an incomplete generation as complete.
5. **Verify** project hashes, destination repository, actual included resources
   and guest recovery; distinguish metadata/full repository checks and a real
   isolated restore.
6. **Retain/monitor** at least two complete local recovery generations plus
   configured off-host retention; track success/failure for each destination.

A successful Git push cannot mask a failed restic backup. Missing/offline or
excluded workloads remain **partially covered**, never healthy/full. Default
source discovery cannot infer database consistency; require per-project
declarations and visible warnings.

## 6. NixLab Web UI

Web UI sections: **Projects, Backups, Host settings and Recovery**. Projects
are editable/declarative, but deployment is always a reviewed action.

- **Project list**: enabled backend (Native Incus/NixOS Guest/Incus Compose),
  Incus project/instance identities, actual running status, drift and backup
  coverage, with unknown/stale shown explicitly.
- **Project editor**: raw Nix `default.nix`/recursive modules and associated
  files, Compose, resource bindings and immutable revision diff; safe templates
  for native guest, OCI app, USB/serial passthrough, storage and networking.
- **Secrets editor**: masked write-only updates and controlled rotation;
  clear distinction between encrypted source and runtime injection outcome.
- **Deployment**: eval/build errors, impact plan, explicit approval, apply,
  health, dependencies, last successful revision and recoverable partial state.
- **Backups and restore**: one-time provider setup, generated `backups.nix`
  preview, project/data coverage, local/Git/off-host status, restore drills and
  independent recovery-kit export/import.
- **Deep host/guest operations**: links to Cockpit and native Incus UI, rather
  than duplicating those complete interfaces in NixLab.

All browser operations use the existing authenticated NixLab backend, short
transactions, role checks/CSRF and root-only broker, never an Incus socket or
unbounded shell passed from the browser.

## 7. Fresh-host disaster recovery

Recovery from an independent copy must not need functioning previous NixLab
control plane, GitHub SSH service or the failed host itself.

1. Install ordinary NixOS, then initialize NixLab in **recovery mode**, with
   no automatic project/Incus application activation.
2. Import the independently stored encrypted recovery kit, unlock and verify
   original age identity + restic access before attempted project decryption.
3. Retrieve the projects workspace from GitHub or restic with exact
   `flake.lock`, encrypted secrets and `backups.nix`.
4. Restore Incus instances/volumes (or recreate from approved desired state
   and restore actual application data) into explicitly mapped storage.
5. Evaluate source in isolation, decrypt check, review duplicate identities,
   compatibility, source revision, networking/storage paths and USB devices.
6. Apply approved plan, inject only each project's secrets, start dependent
   services, verify actual health and evidence from the restored data.
7. Persist a restore report; do not conflate host install success, project
   configuration evaluation, backup transport and successful app recovery.

## 8. Implementation order and hard gates

1. [#127](https://github.com/OmarDardery/nixlab/issues/127):
   top-level workspace flake, recursive typed project discovery and immutable sources.
2. [#135](https://github.com/OmarDardery/nixlab/issues/135):
   project-only review/apply/recovery transactions without host rebuild.
3. [#133](https://github.com/OmarDardery/nixlab/issues/133):
   exclusive project-scoped Native Incus/Incus Compose reconciliation.
4. [#134](https://github.com/OmarDardery/nixlab/issues/134):
   install/import age identity, SOPS project secrets and scoped delivery.
5. [#128](https://github.com/OmarDardery/nixlab/issues/128):
   Web UI project/Nix/Compose/secrets/deployment workflows.
6. [#129](https://github.com/OmarDardery/nixlab/issues/129):
   one-time backup wizard, source/data discovery and existing restic integration;
   [#130](https://github.com/OmarDardery/nixlab/issues/130):
   GitHub deploy keys and Google Drive rclone storage.
7. [#126](https://github.com/OmarDardery/nixlab/issues/126):
   final Arcane retirement after **pre-cutover** proven project, data and key
   backup/restore trials; preserve legacy workloads until explicit migration.
8. [#131](https://github.com/OmarDardery/nixlab/issues/131):
   final exact-revision Arcane-free new-install, physical-host and independent
   restore qualification **after** the runtime removal.

Acceptance requires: new project appears on next `nix eval`; project-only
update does not rebuild host; idempotent reapply; drift refusal; secret-free
Nix store/logs/plans; persistent key surviving retries; separate-host decrypt
and full data restore (database-backed multi-service workload); no destructive
partial deploy or silent Arcane volume deletion; exact-version CI, reboot,
live container/VM and hardware-USB evidence.

The product goal is a **declarative controller for Incus projects with ordinary
portable Nix source**, not another Docker/VM engine. Source, encrypted secrets,
persistent data and unlock keys have distinct owners and recovery lifecycles.
