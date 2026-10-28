# File-based administrator configuration

The hypervisor discovers NixOS modules recursively under `machine/modules/`.
Existing installations using `site-modules/` remain supported until their layout
is migrated. Names starting with `_` are disabled. Bootstrap intentionally ignores
user modules. These are trusted, privileged system configuration; keep secrets
outside the source tree.

This project stays in one Git repository. The ignored `machine/` directory holds
settings, installation metadata, preserved hardware/boot modules and user features.
There is no second repository to create or maintain.

For an existing filesystem, add `machine/modules/disks/archive.nix`:

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
checks cannot establish those runtime facts. The guarded portal/CLI build validates typed disk declarations against hardware
discovery before activation. Direct rebuilds require administrator review of these
runtime facts.

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

## Migrating an existing installation

Upgrade the platform code and activate the updated admin package first. Finish
or discard any pending configuration transaction. Use the **Migrate local configuration** action on the Configuration files page,
with administrator reauthentication, or run:

```sh
sudo nixlab-admin config migrate-layout
sudo nixlab-admin config status
```

The command validates source paths, copies only approved configuration files into
an isolated staging directory, and atomically renames it to `machine/`. It is
idempotent, refuses an unknown/pre-existing machine format, and keeps the legacy
files for recovery. A version marker selects one authoritative layout; old local
files cannot override the canonical files. No OS rebuild, guest mutation or disk
operation occurs during this directory migration. Back up `machine/` independently.

Fresh installations start with this layout. The import of the original NixOS
configuration, its relative hardware imports and stateVersion are preserved under
`machine/installed-system/`. The tracked hardware placeholder is not overwritten.

## Typed settings and ownership

All inventory fields are now Nix module options under `nixlab.settings`. Unknown
keys and invalid value types are rejected by Nix. The existing JSON schema/UI
remains a compatibility authoring interface: per-field defaults preserve its
values unless an administrator module overrides that field explicitly.

```nix
{ ... }: {
  nixlab.settings.radios.bluetooth.enable = false;
  nixlab.settings.monitoring = { history = true; retentionDays = 7; };
}
```

The UI displays the effective configuration and identifies differing file-owned
settings; corresponding scalar form controls are disabled. Structured resource
forms remain JSON-owned. Secrets are runtime references, never credential contents.
`nix eval --json .#nixlabSettings` shows evaluated settings;
`.#nixlabDesiredSettings` shows the JSON/default inventory before file overrides.
The active projection is materialized in `/etc/nixlab/effective-settings.json`;
regular status polling does not evaluate Nix.

Declare guests independently using stable names:

```nix
{ ... }: {
  nixlab.incus.instances.monitoring = {
    image = "images:REPLACE-WITH-REVIEWED-FINGERPRINT";
    type = "container";
    config = { "limits.cpu" = "2"; "limits.memory" = "1GiB"; };
  };
}
```

Default project/profile are `lab` and `base`. Projects/profiles referenced here
must already exist through platform policy or native Incus preseed options.
Duplicate project/name identities are rejected; `lab/apps` is reserved. The old
`settings.instances` list is accepted for compatibility, not a second authority
for the same guest. A changed image reference never recreates an existing guest.

## Reconciliation migration

The first reconciliation of an existing Nixlab-managed guest retains the previous
additive behavior and records only the declared fields in a versioned last-applied
marker. It does not claim ownership of unrelated runtime config or volatile keys.
Subsequent runs compare previous declaration, current runtime values and desired
values. Removed owned config keys converge; unrelated runtime keys survive.
Conflicting runtime edits fail with a field identifier rather than overwriting
them. Resolve drift explicitly in the UI/CLI or update the declaration to agree
with the intended runtime value, then review again.

Device detach or changes to type/pool/source/path require an explicit storage
migration. A shutdown-only sentinel preserves the guest definition. Removing an
entire guest declaration retains its disks and instance; it is not deletion
permission. Manual stopped state survives unrelated rebuilds, while incomplete
initialization is retried. Configuration writes carry the ETag from the same GET,
so concurrent changes return an error instead of being lost.

The configuration-model transition retains legacy compatibility. The portal also provides
guest/restore jobs, a packaged recipe catalog, account roles, recovery kits and reviewed
platform updates; see [operations](operations.md). Clustering/HA, broad third-party
catalog compatibility, multi-tenant isolation and PBS compatibility remain separate
product scopes. Physical commissioning and a restore drill remain release gates;
configuration tests cannot establish hardware reliability.
