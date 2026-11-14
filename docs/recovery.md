# Security, storage, snapshots and recovery

## Storage policy

| Backend | Benefits | Limits |
|---|---|---|
| ZFS | Efficient snapshots/clones, checksums, compression, datasets and enforced quotas | Needs deliberate disk/pool layout, RAM planning, scrub/recovery operations, compatible kernel |
| Btrfs | Efficient snapshots/clones and compression, native NixOS support | Requires suitable existing filesystem; redundancy/repair behavior needs planning |
| Directory on ext4/XFS | Portable, no format/create-pool action, simple baseline | File-copy snapshots, slower clones/exports; hard container quotas are not guaranteed |
| LVM-thin | Efficient block provisioning/snapshots | Thin-pool exhaustion and separate filesystem/metadata management |

The shipped baseline creates an Incus **directory** pool under its dedicated state tree.
It does not create a host filesystem, consume a raw device, or promise redundancy.
Persistent data lives under `/var/lib/incus`, application volumes inside the VM's persistent
disk, Compose definitions under `/srv/compose`, Portainer state under `/srv/portainer`.
Nix source/store and runtime state have separate lifecycles.

For ZFS, first review physical disk identity/contents, backups and a recovery plan. Obtain
explicit operator confirmation before any first-time destructive disk/pool initialization.
No `zpool create`, partitioning, `mkfs`, force/wipe flag or disko destroy command runs on rebuild.
Initialize storage through your separately reviewed installer/storage procedure. Then create
or identify an **empty child dataset**, e.g. a chosen `POOL/incus`, and set:

```nix
storage = {
  driver = "zfs";
  source = "EXISTING-POOL/EXISTING-EMPTY-DATASET";
  confirmedExistingZfsDataset = true;
  hostId = "YOUR-UNIQUE-8-HEX-DIGIT-ID";
};
```

Use a real dataset and host ID. A preflight guard rejects raw device paths, pool roots and
missing datasets. The NixOS ZFS package/modules and scheduled scrub are enabled, but this
is not an automatic storage conversion. Switching an existing Incus pool's driver/source
is not an in-place migration: create a separate pool and migrate verified exports/volumes.
The preseed never deletes the old pool on omission. Host ZFS import/root configuration is
part of your hardware-specific installation.

Btrfs/LVM are evaluated alternatives, not supported values of this repository's pool selector.
Add a reviewed module/backend validation before choosing one. Never label an untested storage
layout as a drop-in configuration change.

## Snapshots versus backups

The `base` profile schedules daily Incus snapshots with seven-day expiration. These are
fast recovery points on the same storage. Live snapshots can be crash-consistent; they do
not replace database dumps and do not survive loss of the source disk/pool.

```console
sudo incus snapshot create apps manual-before-update --project lab
sudo incus snapshot list apps --project lab
```

Restoring a snapshot overwrites current guest state. Stop the guest, take/export a current
backup if possible, then explicitly restore the named snapshot and start it. Review the
installed `incus snapshot restore --help` syntax before applying a rollback. Test application
schema compatibility too; OS rollback does not undo a database migration.

## Independent scheduled backups

Set `site.backup.enable = true` only with an independent repository, runtime credentials
and enough staging disk. Default schedule is approximately 03:00 in the configured timezone with up to
ten minutes jitter; missed runs execute after boot. `initialize` is false until explicitly
approved for the chosen remote target. Repository initialization is not disk formatting,
but it still needs the correct remote path, access and retained recovery password.

The service:

1. Takes the shared Incus mutation lock and recovers pending guest restarts from a prior interrupted export.
2. Enumerates projects, instances, pools and custom volumes. Unsupported guest states fail explicitly.
3. Runs the demo PostgreSQL `pg_dump --format=custom` hook when the demo is enabled. Redis is a cache;
   it deliberately has no durable data. Other live-consistency hooks must be implemented per application.
4. Records restart intent on persistent disk, then gracefully stops each group's originally running guests.
   No forced shutdown is used. Each consistency group is stopped, exported with its
   custom volumes, and recovered before the next group begins. Shared writable volumes
   join their consumers into one group.
5. Publishes a complete generation only after all selected guest/volume exports and recovery kits succeed. Exports include
   instance configuration and snapshots. Inventory metadata is retained for reconstruction.
6. Restarts only guests that were previously running, including after an export error. A systemd
   `ExecStopPost` and boot recovery unit retry journaled restarts if the exporter was killed.
7. Runs restic against the completed exports and retains 7 daily, 4 weekly and 6 monthly snapshots.
   A success timestamp is written only after the restic service's successful command path.

This is a deliberate **offline backup** strategy with downtime for each selected running guest
during its group's export. Large exports can make that group's outage long.
Staging needs room for two retained complete generations plus a full new generation and overhead.
The free-space reserve is a minimum preflight check, not an export-size estimate; an export
that fills staging fails and recovers its guests without replacing prior complete generations.
Exports are root-readable plaintext on local disk; use encrypted host storage where required.
Only the off-host restic repository is encrypted by restic. Keep its password/private access
credentials separately from the failed host.

Do not attach guest custom volumes to concurrent host writers during backup. External databases,
NFS data, remote block storage, passthrough disks and application dependencies outside Incus
are not made consistent by stopping guests; include them in an explicit application backup plan.
The source root disk remains a single failure domain. Test exported volume coverage after adding
any new storage driver, content type or project-sharing configuration.

`homelab backup NAME` is a convenient native export to the configured client destination. It
does **not** stop a running guest or export separately attached custom volumes and therefore
has live/crash-consistency limitations. For the full scheduled path:

```console
sudo systemctl start restic-backups-nixlab
sudo journalctl -u restic-backups-nixlab -u nixlab-backup-failed --since today
sudo systemctl list-timers '*restic*'
sudo cat /var/lib/nixlab/restart-pending.json
```

A network/credential/prune error means the job is failed. Existing good restic snapshots are
retained by restic semantics; do not treat a local export or a fresh success timestamp from an
old run as proof that the current backup completed. Prometheus alerts on failed units and
backup age. External alert delivery requires your real Alertmanager/notification destination.

## Database restore drill

First test in a separate project/guest and avoid duplicate LAN addresses, DNS registrations,
webhooks or public ingress. The full offline VM export includes PostgreSQL's cleanly stopped
volume as well as the logical dump. The logical dump can restore into a compatible fresh PG
service independently:

```console
# Inside apps VM, with the intended demo PostgreSQL started and an explicitly chosen dump:
cd /srv/compose/demo
docker compose stop web
docker compose exec -T postgres pg_restore -U demo -d demo --clean --if-exists < /path/to/verified-demo.dump
docker compose start web
```

This overwrites database objects. Take a current backup first and test on an isolated copy.
Use the same Compose digest override files if present. A schema-changing application upgrade
may require a matching older application version. Test `/health` and expected persistent data.
An Immich deployment needs its own PostgreSQL version/extensions and upload/library backup;
do not substitute this demo hook for Immich's documented backup workflow.

Configured recovery credentials and SOPS links must live under trusted root-owned
parent directories; writable non-sticky or unprivileged-owned parents make
coverage incomplete. Move affected credentials to root-controlled storage and
review their configured paths. Portainer reset metadata is optional on fresh
bootstrap but must be included safely once it exists. This preflight refuses
backup before guest shutdown.

## Host-loss restore

### Guided, checkpointed imports

The replacement-host CLI restores a complete version-2 generation into a fresh
Incus project, using a pre-existing reviewed local storage pool. It stages source
and credentials for review, imports separate custom volumes and stopped guests,
then removes network/passthrough access before validation. It never formats disks,
activates archived Nix modules, overwrites runtime credentials, starts a guest or
reconnects production addresses. This is a local-backend recovery increment;
privileged/raw guests, unmanaged NICs, passthrough and missing attached data stop
with a required separate review. Source material is never modified or deleted.
The planner accepts existing dir, ZFS, Btrfs and LVM pools; automated recovery
coverage currently exercises only dir. Other backends, VM recovery and application
consistency require separate qualification before relying on them.

1. Install fresh NixOS on the replacement machine with console recovery available.
   Preserve its own boot/hardware configuration. Use the normal single-command
   NixLab installer, initially selecting containers only and no scheduled backups
   or public ingress. Do not import old guest declarations yet.
2. Before enabling recovered services, add this reviewed machine module to the
   installed source (`/var/lib/nixlab/config/machine/modules/recovery.nix` on the
   modern layout), then build/review/activate through the usual host workflow:

   ```nix
   { ... }: { nixlab.recovery.enable = true; }
   ```

   This disables instance reconciliation, apps onboarding, backup/restart timers
   and configured Cloudflare ingress. Verify the resulting marker and units:

   ```sh
   test "$(cat /etc/nixlab/recovery-mode)" = 1
   systemctl is-enabled nixlab-instances.service nixlab-apps-onboarding.service
   ip -br link
   ls -l /dev/disk/by-uuid
   incus storage list
   ```

   Disabled/not-found units are expected here. Review any additional timers,
   tunnels or jobs introduced by custom modules separately. Preserve working SSH
   and management addressing while reviewing NIC/disk replacements.
3. On the replacement host, obtain restic credentials from **independent** storage
   and restore the chosen off-host snapshot to a root-only directory. Locate its
   complete `generations/GENERATION` directory containing `inventory.json` and
   configuration, secret, instance and custom-volume archives. `restic check`
   alone is not a restore. Do not put decryption credentials only inside the
   repository they unlock. From a root shell with repository/password credentials
   loaded through protected runtime files, replace `SNAPSHOT` with the reviewed ID:

   ```sh
   restic snapshots
   restic check
   install -d -m 0700 /root/offhost
   restic restore SNAPSHOT --target /root/offhost
   ```

   Restic preserves original paths below the target; use the located generation's
   full path in the commands below. Keep the restored material root-owned and its
   generation directory mode 0700.
4. Select an existing local Incus pool on reviewed replacement storage. Do not
   point at an unmounted disk path, erase a device or reuse production storage.
   Write a root-owned mapping file, substituting actual discovered identifiers:

   ```json
   {"pool":"replacement","interfaces":{"old-uplink":"eno1"},"disks":{"OLD-UUID":"NEW-UUID"}}
   ```

   The pool receives recovered data; it must already exist. Supported backends
   accepted by the planner are dir, ZFS, Btrfs and LVM with portable Incus exports;
   only dir has automated restore evidence. NIC/disk mappings must
   refer to existing replacement hardware. They are review records, not network,
   filesystem or boot mutations; adapt old source against the replacement's own
   configuration manually. Empty mappings are allowed when no hardware remap is
   needed. The initial increment maps all recovered objects to one selected pool.
5. Plan, inspect and explicitly confirm the imports:

   ```sh
   sudo nixlab-admin recovery plan /root/offhost/generations/GENERATION --mappings /root/recovery-mappings.json
   sudo nixlab-admin recovery status
   sudo nixlab-admin recovery resume --confirm
   ```

   Planning hashes every archive, checks both recovery kits and records source
   identity and destination pool configuration. No Incus resource is changed by
   planning. The new journal is `/var/lib/nixlab-admin/replacement-recovery.json`;
   source and credentials are staged under its reported recovery ID at
   `/var/lib/nixlab-admin/recovery-ID/{config,secrets}`. These files stay root-only.
   Compare source files and restore reviewed credentials individually, with their
   required owners/modes; never copy old hardware configuration or secrets over
   the replacement wholesale. Metadata/trust/network definitions remain available
   in the original inventory for review; old listening addresses are not applied.
6. Resume using the same command after a controller interruption or reboot.
   Changed checksums, pool configuration or recovered objects stop execution.
   An interruption after volume import but before ownership was recorded leaves
   an **uncertain import**, not permission to overwrite it. Inspect that stopped
   target, verify it against the archive, and only then explicitly set its
   `user.nixlab.recovery-id` to the journal ID using native Incus before resuming.
   If it cannot be verified, retain it and investigate; do not delete user data to
   make retry pass. No existing unmarked object is automatically adopted.
7. Validate restored data using Incus exec/console on the isolated targets.
   Guests have no NICs/profiles, autostart or snapshot schedule. Their imported
   root disks and separate recovered custom volumes remain attached. Start and
   stop selected guests explicitly for the drill, check application records,
   Portainer state/credentials and Compose definitions, then stop them again.
   Runtime jobs inside a guest still need review before any connectivity is added.
   Archived snapshots retain their original configuration; review isolation before
   restoring one. Do not claim success merely because imports succeeded.
8. Review and perform cutover through **native Incus**. Recreate only reviewed
   projects, profiles and networks from the inventory. Keep the replacement's
   own pool source, NIC names, addresses and disk UUIDs. Move verified custom
   volumes to their reviewed original namespaces, and move stopped guests to
   their original project/name. Detach/re-attach custom volumes as required for
   cross-project moves; never attach a surviving production writer's volume.
   Check installed `incus move --help` and `incus storage volume move --help` for
   backend restrictions. Preserve the recovery ID and imported instance UUID.
   Review desired declarations against these restored objects, including explicit
   ownership adoption if managing them through NixLab. Do not enable onboarding
   against unreviewed credentials or an old guest OS target.
9. Release the automation guard only after the application-data drill and reviewed
   source inventory match the restored identities:

   ```sh
   sudo nixlab-admin recovery release --confirm --data-verified
   ```

   Every desired guest that could be created must be among the recovered original
   identities, present and stopped with its recorded UUID/recovery ID. Legacy or
   partial coverage additionally requires `--acknowledge-exclusions` and is recorded
   as selected-data evidence. Release records an **operator attestation**, backup
   time and recovery duration; it never starts guests or changes networking.
   Only then remove the recovery-mode module through a reviewed host activation.
   An unfinished/corrupt journal continues to block reconciliation, deployment,
   backups and portal guest mutations even if the mode module is removed early.

   For release evidence, export the redacted result rather than the operational
   status (which intentionally includes private workload/hardware mappings):

   ```sh
   sudo nixlab-admin recovery export-report > recovery.json
   ```

   Inspect it before sharing. It contains source/input identity, coverage,
   attestation time and measured duration, with no guest names, disk UUIDs,
   addresses, credentials or mapping paths. Incomplete recovery stays unverified.

If interrupted, rollback means retaining the quarantine and original backup while
returning to the known replacement host configuration. There is no automatic
delete/overwrite cleanup. The CLI does not claim arbitrary backend, device,
custom-module secret or application-consistency coverage. Its two-node CI drill
uses synthetic configuration kits; an independently supplied real off-host
generation, mapped hardware and application checks are still mandatory for #11
and release qualification.

The instance manifest assists reconstruction; it is not an automatic restore of every possible
Incus server object. UI-created non-default networks/profiles, remote storage and external account
resources need their own exported definitions/IaC. Back up the configuration Git repository and
secret recovery material independently; do not rely on a file copied into the failed disk.
The provided backup does not raw-copy a live Incus database.

## Security upkeep

Review pinned Nixpkgs/sops inputs, Portainer release notes and Compose image digests regularly.
Use a branch/PR and `nix flake update`, build/evaluate, then deploy during a maintenance window.
No unattended flake update or reboot changes the hypervisor behind your back. Kernel updates
require a planned reboot. Incus database/API upgrades may make an old daemon generation
incompatible; consult release notes before downgrading. NixOS rollback is not a database backup.

Trusted Incus socket/certificates, Docker socket, passwordless sudo and Portainer admin are all
privileged control planes. Keep them private, use distinct identities, revoke lost keys, and
protect backups because they include guest secrets. The default firewall restricts management
by interface/source; it is not a substitute for authentication or physical network security.

## Current local layout

New backups use `.incomplete/` for unpublished work and `generations/<id>/` for
complete archives, recovery kits and their inventory/checksums. `latest.json`
selects the latest published generation. Restic excludes `.incomplete/` and
preserves the complete generation directories. A later failed export cannot
replace members of the previous complete set. Two complete local generations are
kept; legacy flat archives remain readable until explicitly retired.

Stopped guests remain stopped; running guests recover after their own group,
including after cancellation/failure. Shared writable volumes require their
consumers to stop together. Excluded groups are explicitly omitted and do not
advance the full-host success timestamp. Exports do not protect external host
writers or devices; apply workload-specific maintenance policies.

## Coverage and staging preflight

Before stopping guests, scheduled backup checks required configured recovery
credentials and estimates the **new** generation in addition to retained files.
Observed Incus usage and previous archive sizes are estimates, with 25% headroom
and 16 MiB per archive; unavailable current usage is listed as unknown even if a
previous archive size exists. Recovery kits add their bounded maximum. Known
insufficient capacity refuses before guest shutdown. Estimates do not guarantee
snapshot size, compression ratio or space under concurrent writes.

Guest disks and separately exported custom volumes are distinguished from host
bind mounts, raw attached disks and other external sources. A read-only attachment
still needs its source data to be recoverable. Uncovered or excluded data keeps
coverage partial and does not refresh the full-host success timestamp. The root-only
`inventory.json` retains `coverageDetails`, `capacity` and actual `archiveBytes`;
`/var/lib/nixlab/backup-status.json` retains the most recent preflight/result.
Existing version-2 inventories remain readable. Older inventories without these
fields have no evidence for this additional coverage assessment.

```sh
sudo nixlab-admin recovery coverage
sudo cat /var/lib/nixlab/backup-status.json
```

The credential check reports identifiers, never secret contents. Configured SOPS
symlinks are resolved to checked regular root-owned targets before archiving;
missing or writable-by-others required targets refuse scheduled backup. Keep
restic decryption/access credentials independently available: a copy **inside**
the repository cannot unlock that repository after total host loss.

A completed export is not an off-host transfer, `restic check` is not an application
restore, and these new records initially say `restore: unverified`. Prove actual
Portainer state, Compose definitions, Docker volume records and any external data
through an isolated restore drill. Incus server identity/administrator enrollment,
external writers, arbitrary custom module secrets and application-specific
consistency still require review; this change alone does not close #12 or qualify
a whole-host release. No new application integration or retention policy is added.
