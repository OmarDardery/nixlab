# NixLab — Incus-native rebuild (work in progress)

**This branch is not installable as a supported release. Do not deploy it to a production host or run an update from reset `main`.**

NixLab aims to provide a single Web UI for a fully Incus-native NixOS homelab: Incus projects, OCI containers, system containers and VMs, privileged reviewed transactions, encrypted secrets, backups, restoration, and safe raw Dockerfile/Compose source import **without Portainer, Arcane, or a Docker daemon**.

The pre-Portainer ancestor is `3e3280e6`; the old maintained code and deployment history are preserved at [`archive/pre-incus-native-main-2026-11-17`](https://github.com/OmarDardery/nixlab/tree/archive/pre-incus-native-main-2026-11-17). The default `main` branch was reset to the ancestor by maintainer request and **currently contains no functional installer**.

## Source of truth
- [Target architecture](docs/incus-native-architecture.md)
- [Repository and runtime layout](docs/incus-native-structure.md)
- [Phased execution and safety gates](docs/incus-native-plan.md)
- [Every legacy file: copy, translate or remove](docs/incus-native-file-inventory.md)
- [Draft integration PR #142](https://github.com/OmarDardery/nixlab/pull/142)

## Implementation status
The old source has been imported for selective translation, **not approved for production**. The portal has Incus-native project navigation, read-only observed project lists, an authenticated read-only broker view of declarative project metadata, and browser-local SHA-256 source preflight. No uploaded file is currently persisted, built or deployed. Most legacy backend and module code remains to be removed or translated. CI and physical-host qualification are release blockers.

Existing Incus guests, VM disks, databases and external volumes are never destroyed merely because Git branches or configuration modules change. Perform a verified off-host backup and explicit migration/restore drill before retiring the legacy applications VM on an existing installation.
