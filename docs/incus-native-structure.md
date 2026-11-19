# Target repository and runtime structure

Use existing names initially where they reduce migration risk; consolidate only after tests pass.

```text
/
  flake.nix, flake.lock, configuration.nix   # NixOS base host and packages
  installer/                                 # one-command bootstrap and safe upgrades
  modules/                                   # host modules, Incus, auth, network, backup
  lib/                                       # typed NixLab project and resource definitions
  admin/
    main.py                                  # Web UI API entrypoint (nonprivileged)
    controller.py                            # reviewed host and project broker entry
    project_plans.py                         # constrained typed evaluator/impact plan
    project_transactions.py                  # immutable revision, apply, recover
    static/                                  # single portal, including project management
  scripts/
    reconcile.py                             # Incus ETags/owned resource control
    project.py, project_workspace.py         # sources, validation, import/export
    backup.py, backup_plan.py                # per-project encrypted backup orchestration
  examples/project-workspace/                # example Nix native project
  tests/                                     # unit, browser, NixOS VM, Incus and recovery
  website/                                   # generated public documentation site
  docs/                                      # architecture, plans, operations, qualifications

On an installed host:
  /var/lib/nixlab/config/                   # pinned *platform* source
  /srv/nixlab/projects/<name>/               # typed Nix project intent (flake-backed)
  /srv/nixlab/project-sources/<name>/         # immutable original Docker/Compose build contexts outside Nix store
    revisions/<source-sha256>/source/         # original files, byte exact, root-owned
    current.json                              # CAS-protected selected source revision
  /var/lib/nixlab/state/                    # broker journals, audit, plans, generation map
  /var/lib/nixlab-secrets/                   # private decryption identities and runtime keys
  Incus storage pools/volumes                # persistent guest data; separately backed up
```

## Web UI information architecture
**Overview** → guests, projects, health, quick actions.
**Projects** → list/create/import; source tree/upload (byte exact); environment/secret references; build; review; deploy; logs; revisions; rollback; backup/restore.
**Project Secrets** → masked project secrets, local dotenv import, reviewed SOPS ciphertext revisions, offline key recovery and declared consumer bindings. Runtime delivery remains gated on Incus/NixOS adapter tests. [Secrets UI contract](incus-native-secrets-ui.md). No standalone vault server.

Secrets implementation boundaries (draft #153): `admin/native_secrets.py` manages age identity, reviewed ciphertext revisions and recipient-policy journals; `admin/native_secrets_recovery.py` handles the separately passphrase-encrypted age/restic/tunnel offline kit and its actual /run SOPS unlock test; `admin/native_source_guard.py` blocks plaintext/unsafe uploaded OCI sources without rewriting them; `admin/native_secrets_delivery.py` provides broker-owned per-workload volatile file staging with no Incus or guest master key access; `lib/project-guest-secrets.nix` derives ciphertext-only guest sops-nix service credentials. These files do **not** supply the missing #133 exact-version Incus Compose apply/ownership integration, live Incus persistence audit or NixOS guest key provisioning. Raw Dockerfile/Compose source remains immutable; forbidden env secrets fail closed.
**Incus** → instances, OCI/VM lifecycle, storage/volumes, networks, profiles, devices, drift and ownership.
**Host** → network, storage, services, accounts, security, updates, diagnostics.
**Recovery** → jobs, failed/partial actions, snapshots, encrypted backup status, host replacement and audit.

Main portal frontend is native code under `admin/static` and uses existing authenticated, CSRF-protected Web UI APIs and broker transactions. It never receives Incus unix socket or filesystem write privileges. Access to raw project source and secrets is gated, sized and audited.

## Avoid duplicate code paths
Do not bring back `guests/apps.nix`, `modules/docker-host.nix`, `modules/apps-onboarding.nix`, `admin/arcane.py`, or Arcane proxy/cookie code. Do not add Portainer, Dockge or Arcane as an alternate manager. Compatibility is an **offline migration utility or explicitly enabled legacy operation**, not the default installation.
