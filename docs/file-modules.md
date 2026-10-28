# File-based administrator configuration

The hypervisor discovers `.nix` NixOS modules recursively under `site-modules/`.
Names starting with `_` are disabled. Bootstrap intentionally ignores this tree.
This is the first file-based extension boundary; it is not yet the planned
flake-parts conversion or a browser-upload API. Modules are trusted, privileged
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
