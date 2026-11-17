# NixLab target architecture: Incus + Nix + original application source

**Product decision · 2026-11-17.** This document defines the **target**, not
features already delivered. NixLab is a web-first **declarative, recoverable
single-host homelab controller**. A user should be able to install NixLab,
create or upload an application project in the browser, approve a preview,
operate the workload with Incus, configure encrypted secrets/backups once, and
recreate the lab on another NixOS machine without reconstructing configuration.

## 1. What NixLab is — and is not

Exactly **three user management surfaces**:

| Surface | Sole responsibility |
| --- | --- |
| **NixLab Web UI** | Projects/files, source revisions and change review, Nix evaluation, OCI builds, scoped secrets, managed deployment, backup/restore status |
| **Incus + Incus UI** | System/OCI containers, VMs, Incus projects, runtime lifecycle, networks, volumes, devices, snapshots and deep guest operations |
| **Cockpit** | General Linux host administration and diagnostics |

**NixOS** declaratively configures the **host platform**; **Incus Compose**
is a pinned CLI/library adapter that executes compatible Compose projects
on **Incus**, not a fourth management service. **Buildah or Podman** builds OCI
images from original Dockerfiles in a restricted build worker. **No Docker
Engine, Docker daemon, Docker socket, Portainer, Arcane, Arcane applications VM,
or second management database** belongs in the final runtime.

The current implementation still has legacy Arcane paths. Issue
[#126](https://github.com/OmarDardery/nixlab/issues/126) removes all active
dependencies and migration-specific coupling once the replacement works.
**Never delete, stop, overwrite or silently adopt existing `lab/apps` VM,
its disks, Compose files, Docker volumes or credentials.** Legacy state can
remain operator-owned during migration and is removed only by separate,
explicit user action after a verified off-host restore. Removing code and
deleting existing customer data are **different operations**.

## 2. One stable platform; independent project source

```text
/etc/nixos/                          # NixOS host / NixLab platform only
  flake.nix
  flake.lock
  machine/...

/srv/nixlab/
  projects/                          # Portable reviewed workspace source
    flake.nix                        # ONE project-workspace flake
    flake.lock                       # Exact evaluator/dependency revisions
    .sops.yaml                       # Public age recipients only
    backups.nix                      # One-time Web UI generated policy
    aiostreams/
      default.nix                    # NixLab typed project declaration
      modules/
        ownership.nix
        network.nix
        storage.nix
      source/                        # ORIGINAL uploaded application files
        compose.yaml
        Dockerfile
        .dockerignore
        src/
        public/
      secrets.enc.yaml               # SOPS ciphertext, never plain .env
    klipper/
      default.nix
      modules/
        guest.nix
        usb.nix
        volume.nix
    another-app/
      default.nix
      source/
        docker-compose.yml
        frontend/Dockerfile
        backend/Dockerfile
        frontend/...
        backend/...
  state/                              # Optional explicitly bound host app data
  backups-local/                      # Transient complete backup generations

/var/lib/nixlab/
  revisions/                          # Immutable approved source snapshots
  jobs/                               # Existing durable broker transaction state
  keys/projects.agekey                # Root-owned private age key, mode 0400
  uploads/                            # Quarantined unapproved source staging
/var/lib/incus/                      # Native Incus storage pools, guest/OCI data
```

`projects/flake.nix` exposes `nixlabProjects` and discovers immediate
project directories containing `default.nix`. It imports exactly those
entrypoints plus deterministic, sorted recursive `modules/**/*.nix`; no
arbitrary file in `source/` is imported as a Nix module. Nix declarations
use a typed schema for desired Incus resources, build inputs, backup claims,
secret references and stable project identity. Paths/symlinks and ownership
claims are checked, and identical input produces identical inventory.

Local evaluation uses `nix eval --json path:/srv/nixlab/projects#nixlabProjects`
to include newly uploaded files without requiring an initial Git commit.
**Nix expressions are privileged programs**, not safe merely because evaluated:
source is validated and evaluated in a restricted environment; an exact
immutable snapshot is reviewed before the privileged broker applies a plan.
Never put plaintext secrets, private keys, credentials or mutable state in
flake paths or the Nix store.

**Project edit/build/apply must not run `nixos-rebuild`.** The platform
installs a stable project broker, backup runner and Incus client tooling once.
Updating the host is a distinct reviewed operation. `nix profile add` only
manages package profiles and is *not* the projects deployment mechanism.

## 3. Import and use Dockerfiles and Compose files **literally as supplied**

The browser supports:

- **Upload one Dockerfile or Containerfile**, then upload any needed build
  context files/folders, or edit raw text without automatic rewriting.
- **Upload a whole project ZIP/TAR** with nested build contexts and
  `.dockerignore`; preserve each accepted file's original bytes and path.
- **Upload any Compose name**: `compose.yaml`, `compose.yml`,
  `docker-compose.yml` or `docker-compose.yaml`, plus `compose.incus.yaml`
  if supplied. Optional Git source import is subject to revision pinning.
- **Create a project from Nix** with no OCI image build: native NixOS guest,
  USB/serial passthrough, volumes and explicit Incus resources.

The Web UI displays a source tree, original file preview, SHA-256 per file,
uploaded revision, source diff, build context and selected Compose entrypoint.
**Do not rewrite or normalize uploaded Dockerfile/Compose bytes or paths.**
A generated NixLab file is separate from every uploaded file and is reviewed
with its own provenance. No automatic `dockerfile -> Nix` conversion.

**Existing Compose with `build:`:** run the uploaded Compose file directly
from its preserved directory using a pinned Incus Compose adapter. Resolve
relative `build.context`, `build.dockerfile`, volumes and `.dockerignore`
against the frozen project source, not the original upload location. If
Incus-specific options are needed, propose a separate generated override or
ask the user to upload `compose.incus.yaml`. If the original already contains
that file, preserve it; never overwrite it with generated NixLab content.

**Dockerfile without Compose:** create a separate, editable, reviewable
`compose.nixlab.yaml` **in the frozen project deployment directory**, pointing
at the uploaded build context and exact Dockerfile name. The generated
manifest supplies service name, non-secret port/volume/limits and a stable
project-scoped OCI image identity. The original Dockerfile remains untouched.

Example upload:
```text
aiostreams/source/
  compose.yaml                    # Uploaded bytes unchanged
  backend/Dockerfile              # Uploaded bytes unchanged
  backend/.dockerignore
  backend/src/main.go
```

Example **separately generated** manifest when ONLY a Dockerfile was uploaded:
```yaml
services:
  app:
    image: localhost/nixlab-myproject-app:revision-EXAMPLE
    build:
      context: .
      dockerfile: Dockerfile
```

The exact image alias, ports and secrets must be derived from the reviewed
project revision; this is an **illustration**, not something NixLab already
generates. NixLab must never insert generated files into or rewrite uploaded
sources without explicitly distinguishing them.

### Build and runtime pipeline

1. **Upload** → quarantine and verify archive size/decompression bounds,
   file type, symlink/hardlink/path traversal, no absolute host paths or unsafe
   follow-on build mounts, file counts/depth and missing context references.
   Enforce permissions, CSRF, upload limits and project scope. An uploaded
   Dockerfile can run arbitrary build commands: uploading is **never** building.
2. **Validate** → stage the byte-exact files; run pure schema and
   `incus-compose config --quiet` compatibility check without launching
   services. Report unsupported Compose/BuildKit/secret features; **do not**
   pretend every Docker Compose feature is supported.
3. **Review** → compute source/context hash and typed desired Incus plan;
   display user uploads separately from generated Nix/Compose overrides.
   The candidate is immutable before approval and its revision/ownership
   digest is checked again at apply.
4. **Build** → use a version-pinned **Buildah/Podman** builder under an
   isolated, disposable, resource-limited worker or guest. No Docker daemon,
   host filesystem, host user credentials or direct Incus socket in untrusted
   build context. Apply network/registry policy, time/disk/CPU caps, and
   prohibit privileged builder escape routes. Build failures preserve the
   current deployed revision and volumes.
5. **Import/deploy** → pinned Incus Compose copies the built OCI image into
   the Incus project. Use an explicit project/service/revision-scoped image
   alias and pinned build/source hash. Do **not** rely on Incus Compose's
   alias-only shared cache to notice changed Dockerfiles or build contexts.
   Rebuild using `--build` or a changed immutable alias. Record built image
   digest and the source that produced it. Recreating an OCI instance is an
   explicit outage/impact, not application-data rollback.
6. **Observe** → record actual health, status and deployment revision,
   visible without browser access to the Incus socket. Incus owns runtime
   operation. Built-image provenance and any unsupported feature are visible.

Upstream Incus Compose **already supports local `build:`** with an external
builder; it does not itself implement a Dockerfile builder.
The current upstream build spec supports `context`, `dockerfile`,
`dockerfile_inline`, `args`, `pull`, `target` and one target platform.
Several advanced Compose build options remain unsupported, including build
`secrets`, `ssh` and `additional_contexts`: report these explicitly. Its
default image cache is keyed by alias, **not source content**. Furthermore
`up --build` can recreate service instances, so data must live in declared
persistent volumes or be consistently backed up before application.
See [Incus Compose builds](https://incus-compose.org/builds),
[compatibility](https://incus-compose.org/compose-compatibility) and
[getting started](https://incus-compose.org/getting-started).

**Plaintext `.env` is not a portable secret backup.** If the uploaded project
contains credentials, NixLab must reject publication with a precise explanation
and offer secure SOPS import. Encrypted SOPS source is preserved and safe to
version; decrypted environment/credential files are generated only in
protected runtime locations, for narrowly scoped guests. Do not claim to
run an incompatible source literally: preserve it unchanged **and block
deployment** until the user approves separate adjustments or supplies a
compatible build.

## 4. Runtime ownership: one desired-state owner per resource

One NixLab project maps to one Incus project by default, with explicit
Incus project restriction/feature validation and reviewed creation/adoption.

| Deployment type | Owner | What is managed |
| --- | --- | --- |
| Native system container or VM | NixLab Incus reconciler | Declarative instances, hardware/USB/serial devices, profiles, storage references |
| OCI and multi-service Compose | **Incus Compose adapter** | Images, OCI instances, networks and persistent volumes owned by that stack |
| Unmanaged Incus | Incus operator | Everything not explicitly adopted by review |

NixLab is a controller and **not** a competing container engine. Exactly
one controller mutates each instance/volume; owner claims are stored with
last-applied revision. Manual changes to owned fields cause drift warnings
and block silent overwrites. `up` cannot imply `down --project`; removing
a declaration never implicitly deletes guest disks, persistent volumes or
orphaned databases. Migration, adoption, storage replacement, deletion and
downtime need separate explicit approval and recovery qualification.

Reviewed transactions: validate → snapshot immutable source → evaluate typed
inventory → compare last-applied vs live Incus (ETags when available) → impact
preview → authenticate exact revision → apply in safe dependency order →
verify health → persist exact deployed revision, or report partial failure.
Never claim application-data rollback on a Nix revision rollback.

## 5. SOPS + age: every project secret is independently recoverable

- On fresh install generate **one** random private age identity exactly once
  at `/var/lib/nixlab/keys/projects.agekey` (root 0400), or restore/import
  the **existing** identity. It must not regenerate on restart or failure.
- A project has `secrets.enc.yaml`. Web UI offers masked create/change/delete
  and validates SOPS ciphertext; only the public recipient belongs in
  `.sops.yaml`. Plaintext secret values must never enter Nix store, Git,
  process arguments, plans, API response, diagnostics or logs.
- At apply time provide only a project's assigned decrypted secrets through
  protected runtime files or systemd credentials. The master age key stays
  on the host. OCI service secrets use qualified Incus Compose **file-backed
  secret** behavior; not every Docker/Compose secret feature is supported.
- Generate a portable **passphrase-encrypted offline recovery kit** holding
  the original age key, Restic unlock material and required remote access
  credentials. It is intentionally stored **separately** from GitHub/Restic
  repositories, and must pass a test unlock/decryption. No circular dependency
  on credentials contained only in the repository they unlock.

## 6. Backups configured once; complete sources **and** app state

The Web UI wizard generates **one** reviewed
`/srv/nixlab/projects/backups.nix`. Defaults include all existing/future
managed source projects automatically, while persistent data requires explicit
coverage and consistency requirements. A stable host backup runner consumes
the approved policy without rebuilding NixOS on every project update.

| Protected material | Destination | Scope |
| --- | --- | --- |
| Portable source | Private **GitHub** via dedicated SSH deploy key | Workspace root flake/lock, every project Nix file, original Dockerfiles, `.dockerignore`, build context, Compose and uploaded overrides, nonsecret metadata, `backups.nix`, SOPS ciphertext |
| Actual application data | **Encrypted Restic** with Google Drive via rclone or other supported provider | Incus guest exports, named/custom volumes, host bind mounts, database-consistent dumps |
| Recovery credentials | **Separately stored encrypted recovery kit** | Original age identity, Restic repository passwords and remote credentials |

Never push plaintext env values, private keys, unencrypted uploaded secrets,
guest disks or `/nix/store` to GitHub. Size limits/large binary build
contexts are flagged during upload and Git destination setup; use restic
for explicitly selected large non-Git data, preserving a reviewed manifest
and a reproducible path to the exact build source. A successful Git push
does **not** imply a recoverable guest-data backup.

Reuse NixLab's existing Incus exports, grouped consistency handling, Restic
retention/verification and recovery checks. Do not install a second backup
engine, database or conflicting timer. Each scheduled run captures the exact
approved source revision and data coverage, verifies off-host upload, retains
two complete local generations, and reports skipped/failed resources as
**partially protected**. Verify *actual isolated restore*, not just hashes.

## 7. Single Web UI: four sections, one deployment contract

- **Projects:** create via Nix native guest, Dockerfile/Containerfile upload,
  original Compose upload, archive import or pinned Git; directory/file editor,
  unchanged-original diff, resource claims, image build, service/Incus state,
  logs, volumes, health, USB and source revision/impact preview.
- **Backups:** one-time GitHub SSH and Restic/Google Drive enrollment, generated
  `backups.nix` review, data-coverage check, retention, backup history and
  restore drills. All new projects appear automatically in source coverage.
- **Host settings:** NixOS host updates and platform ports, safe network,
  firewall and host-specific storage policy; links to Cockpit and Incus UI.
  Host changes are not mixed into a project-only apply transaction.
- **Recovery:** import original age identity and encrypted recovery kit,
  fetch project source, restore actual Incus volumes/guests, review host-specific
  differences and verify app state before declaring recovery complete.

No raw host shell or unbounded privileged filesystem operations from the
browser. All mutations pass through the existing authenticated, CSRF-protected
root broker with exact-revision approval and resource ownership checks.

## 8. Empty-machine restore and safe Arcane retirement

**Fresh NixOS → install NixLab in recovery mode → import independent key kit
→ fetch exact source/lock revision → restore named volumes and guest data from
encrypted off-host storage → validate compatibility/host-specific ports, disks
and USB mappings → review/apply Incus resources → decrypt only assigned
runtime secrets → verify application health and real persistent data.**

For an existing legacy deployment:
1. Record a **read-only inventory** of `lab/apps`, its Compose files,
   secrets, Docker named volumes, bind mounts and database dependencies.
2. Preserve the running VM/guest data while creating new Incus project source.
   Perform application-consistent exports and independent backup/restore drills.
3. Import the **original** application files and secret values via the new
   project-source/SOPS workflows; keep source bytes unchanged and avoid
   concurrent writers to the same database.
4. Test fresh Incus replacement before a reviewed cutover. Leave old resources
   stopped **only by an explicit operator action**, recoverable until data
   reconciliation and restore evidence are satisfactory.
5. Remove obsolete NixLab Arcane services, proxy, files, credentials,
   image closure, settings, UI routes, installer prompts, tests and packaging;
   for upgrade, the **guest itself remains operator-owned until explicit
   deletion**. Never remove its disk merely because an option disappeared.
6. Certify clean **new install and old-host update**: no Arcane/Docker daemon,
   installer selection, listeners, image or runtime dependencies, and no
   unexpected guest shutdown/deletion. Confirm restore on an empty host.

## 9. Prioritized implementation issues

**Already merged foundation:** architecture specification PR
[#132](https://github.com/OmarDardery/nixlab/pull/132), read-only workspace
evaluator PR [#136](https://github.com/OmarDardery/nixlab/pull/136).
The rest of the features remain implementation work.

| Order | Issue / outcome | Gate |
| --- | --- | --- |
| 1 | [#127](https://github.com/OmarDardery/nixlab/issues/127) — finish workspace bootstrap, exact source snapshots and typed inventory | Approved project source |
| 2 | [#135](https://github.com/OmarDardery/nixlab/issues/135) — review/apply, durable jobs, no host rebuild | Immutable revision broker |
| 3 | [#133](https://github.com/OmarDardery/nixlab/issues/133) — project-scoped Incus and Incus Compose ownership | No runtime double ownership |
| 4 | [#139](https://github.com/OmarDardery/nixlab/issues/139) — byte-exact Dockerfile/Compose import + isolated OCI build | Unchanged source actually builds |
| 5 | [#134](https://github.com/OmarDardery/nixlab/issues/134) — age identity, project SOPS and offline recovery kit | All secrets recoverable |
| 6 | [#128](https://github.com/OmarDardery/nixlab/issues/128) — complete project Web UI; upload + editor + plan + build status | One browser front-end |
| 7 | [#129](https://github.com/OmarDardery/nixlab/issues/129) — reviewed, auto-discovering backup policy | Source and state protected |
| 8 | [#130](https://github.com/OmarDardery/nixlab/issues/130) — GitHub SSH + rclone/Google Drive destinations | Independent off-host copies |
| 9 | [#126](https://github.com/OmarDardery/nixlab/issues/126) — remove all Arcane platform code **after** a demonstrated replacement and pre-cutover restore | Preserve existing VM data |
| 10 | [#131](https://github.com/OmarDardery/nixlab/issues/131) — final exact-revision Arcane-free install, upgrade, VM/OCI/hardware and independent recovery proof | Release qualification |

Deprecation and read-only migration inventory may ship before the replacement
[#137](https://github.com/OmarDardery/nixlab/pull/137),
[#138](https://github.com/OmarDardery/nixlab/pull/138); neither is final
runtime removal. All work must keep the source of truth in this document and
the linked issue acceptance criteria; no additional control plane, extra
declarative flake loader, Docker daemon or redundant backup scheduler.

## Release acceptance

A new user can install NixLab without Arcane; upload an original Dockerfile
and context or nested Compose project unchanged; build it via an isolated
OCI builder; review and apply it to Incus **without Docker daemon or host
rebuild**; edit a file and get a fresh build rather than stale alias cache;
manage a native NixOS USB guest and a multi-service persistent OCI app from
the same project UI; keep ciphertext/age identity safe; configure automatic
source/data backups once; and rebuild the application with its original
source, encrypted secrets and actual data on a second NixOS host.

**No guest or disk is ever implicitly deleted because a project or Arcane
feature is disabled.** Exact-version Nix evaluation, browser, installer,
source-build, rollback, Incus runtime, power/reboot, USB and independent
restore tests must pass before claiming the final milestone.
