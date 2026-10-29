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

## Host-loss restore

1. Install NixOS safely, restore the reviewed repository revision, generate hardware configuration
   for the replacement host, and verify the network/storage inventory. Do not restore test fixtures.
2. Restore age identities and backup access credentials from offline recovery material. Recreate
   the non-destructive host storage layout or explicitly initialize replacement disks only after review.
3. Temporarily set `apps.enable = false` and `instances = []` in the inventory so a fresh blank VM
   cannot claim the name before you import the backup. Keep the same native pool/project/profile names.
4. Rebuild the host, initialize/import the independent restic repository as documented by restic,
   inspect snapshots, run `restic check` and restore the chosen snapshot into a root-only staging path.
   With the runtime environment/password set, use `restic snapshots`, then
   `restic restore SNAPSHOT --target /root/nixlab-restore`.
5. Read `latest.json` and inspect `generations/<generation>/inventory.json` (or the legacy flat inventory). Import custom volume archives into their original pool/project first,
   using `incus storage volume import POOL ARCHIVE --project PROJECT`. Create any additional
   projects/pools/networks from the inventory before import. Do not overwrite an existing volume.
6. Import each instance archive with `incus import ARCHIVE --project PROJECT` into the correctly
   named pool. Restore pools/volume associations first; name collisions must be resolved explicitly.
7. Restore/verify guest secret files, Portainer state, IP reservations, application configs and
   external datasets. Preserve old host server TLS material only through a reviewed secure process,
   otherwise re-enroll and independently re-verify clients. Never copy a live Incus database blindly.
8. Re-enable desired instances in Nix. Imported owned instances retain their marker; reconcile will
   compare/update them. Verify limits/NICs before starting, especially in a recovery test network.
9. Start guests, verify DB/application state and metrics, reconnect ingress last, and run a new backup.

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
