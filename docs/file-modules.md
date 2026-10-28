# File-based administrator configuration

The hypervisor discovers `.nix` NixOS modules recursively under `site-modules/`.
Names starting with `_` are disabled. Bootstrap intentionally ignores this tree.
This is the first file-based extension boundary; it is not yet the planned
flake-parts conversion a complete platform/site repository split. Modules are trusted, privileged
system configuration. Keep secrets outside the source tree.

For an existing filesystem, add `site-modules/disks/archive.nix`:

```nix
{ ... }: {
  nixlab.storage.disks.archive = {
    uuid = "REPLACE-WITH-DISCOVERED-UUID";
    fsType = "ext4";
    role = "pool";
  };
}
```

The default mount point is `/srv/nixlab/archive`; a pool declaration registers
`disk-archive` with its source at `/srv/nixlab/archive/incus`. Use `role = "data"`
for a plain mount. This never formats a device. Select the actual filesystem UUID
using `lsblk -f`, check that it is not a system disk or already mounted elsewhere,
and verify the mount point is empty before first activation. Build-time type
checks cannot establish those runtime facts. File declarations currently require
administrator review of them; they do not run the browser's hardware discovery.

Names, paths and UUIDs cannot overlap the existing UI-owned mounts. These new
disks are source-owned and not editable through the current storage form. Primary
Incus storage and backup staging continue to use the existing setup workflow.
Do not remove an in-use module: migrate pool consumers and unmount safely first.

From the configuration root:

```sh
sudo nixos-rebuild build --flake path:/etc/nixos#hypervisor
sudo nixos-rebuild switch --flake path:/etc/nixos#hypervisor
```

The explicit `path:` includes new untracked files. For the portal's existing
build/review/apply transaction, these files also enter the hashed candidate
snapshot. Its recovery record saves and restores this module tree alongside
settings and credentials. A direct rebuild does not enable portal rollback.
OS/module recovery does not reverse guest storage or database changes.

## Reviewed browser uploads and root CLI

The Configuration files page stages up to 16 Nix modules, each at most 64 KiB,
with a 128 KiB total limit. Supply a relative destination such as
`disks/archive.nix`. The review page requires the administrator password and
acknowledgment that this is privileged system configuration. Review the old and
new source before activating. This is an administrator interface, not a safe
execution environment for code received from strangers.

Files enter only the candidate until activation. Activation publishes the module
tree and switches to the exact reviewed closure. Failure/confirmation timeout
restores the previous module tree. Removing an active storage declaration remains
a migration operation; no browser deletion endpoint is provided. Source code can
contain secrets, so do not put credentials into uploads or the source review.

Root can use the same transaction API locally:

```sh
sudo nixlab-admin config review --module disks/archive.nix=./archive.nix
sudo nixlab-admin config status
sudo nixlab-admin config apply TRANSACTION_ID
sudo nixlab-admin config confirm TRANSACTION_ID
```

Use `cancel` for an unapplied candidate, or `rollback` with the transaction ID.
The CLI does not wait for builds or implicitly confirm connectivity. Use the
returned status/log to decide when to apply. No extra network service is started.

## Platform feature composition

`platform-modules/features/` is an automatically discovered tree of top-level
Nix modules. Each feature contributes deferred NixOS modules under
`nixosModules.hypervisor`, `.bootstrap` or `.applications`. `lib/platform.nix`
evaluates the feature tree using `lib.evalModules`; no additional flake dependency
is required. The flake exports these role modules for reuse. This follows the
dendritic top-level-module pattern without requiring flake-parts.

The existing `modules/` implementations remain lower-level compatibility modules.
Do not drop them into `platform-modules/` directly: wrap them as a feature's
`nixosModules.<role>.imports`. Hardware and site data remain separate, and the
bootstrap role deliberately loads only installed-base and administration features.
Discovery makes features available; optional services retain their enable flags.

This composition change does not yet split installed platform code into a separate
pinned site repository or replace all legacy inventory fields with named options.
Those are separate migrations; existing installations retain their current source
layout and settings ownership during this transition.
