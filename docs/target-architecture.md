# Target architecture: NixLab = NixOS + Incus + Cockpit + Web UI

**Product decision (2026-11-17). Planned architecture, not current implementation.**
NixLab makes a homelab reproducible and recoverable without shipping an
additional application orchestrator. Its only permanent management surfaces are:

1. **NixLab Web UI** — privileged, reviewed Nix configuration, project files,
   declarative deployments, backup policy and recovery evidence.
2. **Incus and its native UI/API** — guest/OCI/VM lifecycle, Incus projects,
   networks, devices, storage and snapshots. **incus-compose** is an optional
   adapter on Incus, not a new daemon/control plane.
3. **Cockpit** — Linux host services, terminal, files and diagnostics.
4. **NixOS** remains the declarative system host and systemd/timer authority,
   not a separate user-facing application control plane.

**Arcane, Docker inside an apps VM, the dedicated apps VM, Arcane reverse proxy,
credential flows, application stack ownership, and Arcane CI fixtures are
removed from the *target*.** They still exist in current source until migration
[#126](https://github.com/OmarDardery/nixlab/issues/126) is implemented.
Do not claim the target is shipped. An upgrade must never automatically destroy
existing operator-owned `lab/apps` guests, disks, named volumes or credentials.

## Separation of operating system and lab projects

```text
/etc/nixos/                       # Installed NixLab OS, boot/hardware and host policy
  flake.nix, flake.lock
  machine/modules/               # Host-only, privileged Nix modules

/srv/nixlab/projects/             # Independently versioned/restorable lab declarations
  aiostreams/
    flake.nix                     # Optional project-local flake
    flake.lock
    modules/*.nix                 # Optional project settings (not host NixOS imports)
    compose.yaml                  # Incus Compose multi-service application definition
    compose.incus.yaml            # Optional Incus-specific override
    project.json                  # Non-secret ownership/deployment metadata
  klipper/
    flake.nix
    flake.lock
    modules/*.nix
    project.json
  backup-policy.nix               # Generated, reviewed, declarative backup intent

/var/lib/nixlab/                  # Root-owned runtime/deployment transaction status
/var/lib/nixlab-secrets/          # Root-owned credentials, keys, recovery material
/var/lib/incus/                   # Actual guest disks, Incus volumes and snapshots
/srv/nixlab/data/                 # Optional explicitly owned bind mounts
```

These are proposed default paths, not an implemented migration or filesystem
layout. Respect existing mounts and quotas; never move data implicitly. Store
large/persistent application data in Incus volumes or managed host datasets,
**not** in the project Git source directory or the Nix store.

Projects are independent Nix flakes (optional) or source-owned declarative
Compose definitions. The broker evaluates/builds a reviewed project revision
and applies only its whitelisted Incus operation plan; it does **not** import
project Nix files as host modules by default. An explicit host configuration
change still uses NixLab's guarded `nixos-rebuild` transaction.

**Do not use `nix profile add` for deployments**: a profile adds package
outputs to a user profile; it does not instantiate Incus projects, reconcile
devices/volumes, start services or run persistent backups. Prefer isolated
`nix build path:/srv/nixlab/projects/NAME#...` for project artifacts
and a bounded NixLab project apply operation that records source revision,
desired/observed state, and rollback/recovery outcome. Existing data is retained
when a project declaration disappears. Project modules are privileged code;
review them as such, protect file paths, require administrator reauthentication,
validate generated plans and prohibit silent root shell execution from the UI.

No shared host/guest Nix store, second Nix daemon or persistent project database
is needed. Reuse existing root broker, systemd oneshots/timers, durable JSON
state and Incus native APIs.

## NixLab projects and Incus Compose UI

[Incus Compose](https://incus-compose.org/) translates ordinary multi-service
Compose definitions into separate Incus-managed OCI instances, networks and
volumes. The web UI should manage project source, not replace native Incus UI:

- Create/import a project; edit Nix files, `flake.lock`, `compose.yaml` and
  `compose.incus.yaml`, validate, preview changes and reauthenticate to apply.
- Build/pull immutable images, deploy/restart/stop, show actual services, logs,
  resource usage, backup coverage and consistency warnings.
- Use Incus UI for advanced device, console, VM, storage and snapshot controls;
  detect and refuse drift from declared Incus-owned fields.
- No automatic `down --volumes`, `down --project`, guest destruction, privilege
  escalation, network exposure or adoption of native resources.
- Qualify the exact incus-compose/Incus combination, OCI network behavior,
  volumes, bind mounts, GPU/USB attachments, build backend and image digests
  before enabling it for production.

An external `cloudflared` host service can remain optional NixOS policy; it
does not justify a Docker/Arcane VM. Management endpoints remain private.

## One-time declarative backup setup

The Web UI wizard collects policy options **once**, writes a **reviewable Nix
policy file** in the independent project root, and activates a scheduled
systemd/restic workflow. It should not require users to edit a timer every time
they add a project. A default such as `includeProjects = "all-managed"`
automatically discovers the next managed project under the root (with a manifest
of what was included, skipped or failed). Projects can opt out explicitly.

The policy should have typed fields equivalent to:

```nix
# Illustrative desired policy; options are NOT implemented yet.
{
  version = 1;
  includeProjects = "all-managed";
  source = {
    include = [ "flake.nix" "flake.lock" "modules" "compose.yaml"
                "compose.incus.yaml" "project.json" ];
  };
  data = {
    includeManagedIncusVolumes = true;
    includeDeclaredBindMounts = true;
    consistency = "stop-or-quiesce";
  };
  schedule = "daily";
  retention = { daily = 7; weekly = 4; monthly = 6; };
  destinations = {
    github = { enable = true; purpose = "configuration-only"; };
    googleDrive = { enable = true; purpose = "encrypted-data"; };
  };
}
```

**This is a proposed schema, not a runnable current NixOS module.** The
generator writes paths/identifiers/references, not passwords. Treat the
reviewed policy revision as durable recovery input.

Keep four separate backup classes:

| Class | What is included | Preferred backend |
|---|---|---|
| Declarative source | Every managed project flake, lock, Nix modules, Compose files, non-secret metadata and backup policy | Private GitHub Git repo via scoped SSH deploy key; optional encrypted off-host copy |
| Mutable data | Selected Incus instance exports, named/custom volumes, declared bind mounts and app-consistent dumps | Existing NixLab staged exports and encrypted restic, delivered off-host |
| Protected recovery | Decryption keys, secret identity and critical credentials only on explicit authorization | Separate age-encrypted recovery kit with independent key custody |
| Host operating system | NixLab install source, hardware config and NixOS generations | **Not part of project backup**; recover/reinstall NixLab separately |

A local Incus snapshot or `incus-compose backup` restore point is **not** an
off-host backup: an SSD failure can lose the project and its local backup project
together. Incus Compose named volumes have project backup support; bind mounts
need explicit separate coverage. Host restic exports may use their existing safe
cold-backup consistency machinery and data restore process. Never upload an
incomplete generation and label it successful.

### Remote onboarding

**GitHub:** create/use a dedicated private config repository, generate a
dedicated scoped SSH deploy key under root-owned credentials, show **only the
public key** for GitHub enrollment, pin SSH host identity and commit non-secret
project source revisions. GitHub does not hold VM disk images, restic archives,
large databases or plaintext secrets. A backup's Git commit is a *source
checkpoint*, not evidence that guest data was backed up.

**Google Drive:** use an authenticated rclone-backed restic repository or other
qualified encrypted object transfer, with OAuth/refresh material and restic
password outside Nix store, Git, logs, process arguments, URLs and public
diagnostics. Keep source/backup policy copies recoverable without a live host.
Detect rate/quota/auth failures, support credential rotation and verify a real
restore from the independent destination. Do not report a backup green solely
because a local snapshot or remote directory exists.

**Recovery:** start with a fresh ordinary NixOS installation, install NixLab,
restore the project source/lock files, restore independent credentials securely,
evaluate planned revisions, then import actual Incus volumes/guests. Confirm
network/device remapping and test application data before declaring success.
Backup verification, transport success, restore and real application health are
distinct statuses.

## Implementation and release gates

1. [#127](https://github.com/OmarDardery/nixlab/issues/127) — independent project root, Nix evaluation and transactional file/revision ownership.
2. [#128](https://github.com/OmarDardery/nixlab/issues/128) — Incus/Incus Compose project Web UI and migration destination.
3. [#129](https://github.com/OmarDardery/nixlab/issues/129) — generated declarative backup policy, all-managed discovery and restores.
4. [#130](https://github.com/OmarDardery/nixlab/issues/130) — GitHub SSH source sync and encrypted Google Drive data transfer.
5. [#126](https://github.com/OmarDardery/nixlab/issues/126) — finish Arcane/Docker apps-VM retirement **only after** protected migration and recovery are available.
6. [#131](https://github.com/OmarDardery/nixlab/issues/131) — exact-revision, physical, legacy-upgrade and independent-restore qualification.

This target supersedes contrary Arcane, Docker-apps and Portainer statements
in earlier roadmap text. Existing archived code, docs and release evidence
continue to describe the present/legacy implementation until each migration
stage actually lands; neither documentation changes nor a green docs CI run
qualifies live data migration.
