# Migration from Proxmox VE

Migrate one workload at a time. Keep the original Proxmox host and verified independent
backups available until restore drills and application tests pass. Never install NixOS over
the sole copy of VM/container data. There is no automatic in-place Proxmox-to-NixOS conversion.

## Inventory and backup

Record VMID, meaningful name, OS, vCPUs/RAM, disks and custom mounts, NIC/MAC/static leases,
VLANs, firewall rules, autostart dependencies, DNS, secrets, USB/GPU passthrough and service owners.
Include application-specific consistency backups, not just `vzdump` archives. Verify disk
capacity and expected downtime. Keep workload IDs as optional helper aliases; Incus names/projects
are the authoritative selectors.

Verify each actual service independently rather than assuming old numeric
guest IDs are current. Applications may require database-consistent backups,
dedicated data volumes, stable USB devices, custom permissions or guest
networking. Incus does not inherit Proxmox LXC device mappings, nesting
flags or configuration syntax automatically.

## Linux system containers

Proxmox LXC configuration files and backup tarballs are not directly interchangeable with
Incus images/exports. Prefer a clean Incus image of the right distribution, declarative service
configuration, then application-data restore. If migrating a root filesystem, use the supported
upstream `lxc-to-incus` process and review compatibility, uid/gid mappings and filesystem/device
mounts; Proxmox-specific launch hooks require translation. Do not copy host `/dev`, raw proc/sys,
Proxmox kernel files or container security options into a new host blindly.

Keep containers unprivileged. Test ownership after restore: Proxmox and Incus may use different
subordinate uid/gid ranges. Restore at the guest boundary or map IDs deliberately, preserving
metadata, capabilities and ACLs. Test DNS/networking before moving a production address.

## Full VMs

Use upstream [Incus migration guidance](https://linuxcontainers.org/incus/docs/main/howto/import_machines_to_instances/)
and the maintained `incus-migrate` tool for a supported VM-disk import workflow. Inventory boot
mode (UEFI/BIOS), partition layout, disk bus, VirtIO drivers, guest agent and Secure Boot first.
Export/copy a consistent powered-off disk to independent storage; check it before converting
formats with `qemu-img` if needed. Do not point conversion output at the source disk.

A Proxmox `vzdump` backup is not an `incus import` archive. Extract/convert its guest disks
using supported Proxmox tooling, then migrate the machine through Incus tooling. Multi-disk,
TPM, GPU/USB passthrough and firmware-state machines need individual plans and may require
fresh installation plus data restoration. The optional Migration Manager/Operations Center
is worth evaluating for a larger estate, not required for this single-host implementation.

For NixOS guests, rebuilding a new Incus-compatible NixOS image plus restoring application data
is often cleaner than transporting the old host-specific VM/LXC configuration. Update device,
filesystem and initrd settings and enable the Incus agent for full VMs.

## Docker projects

Move Compose definitions by project and restore PostgreSQL with a logical dump or a validated
cold volume copy. Copy upload/media/library directories with ownership preserved. Restore secret
files through the secret mechanism. Redis data is disposable only when the real application's
semantics say so; this repository's demo cache policy must not be assumed for every workload.

Choose one control plane per stack: Nix/CLI-owned files or Arcane Git/UI-owned state. Back up
Arcane's own state separately from application data; its own backup excludes Docker volumes.
Do not blindly copy a Docker graph directory across different storage drivers/Engine versions.

## Cutover and rollback

1. Bring up a test guest with a temporary IP, no public tunnel and no duplicate background jobs.
2. Verify application reads/writes, service logs, resource limits, mount ownership and backups.
3. Test restoring that guest and any custom volumes into an isolated target.
4. Schedule downtime; stop the old workload, perform the final consistent data sync, then move
   the intended guest IP, LAN DNS records or Cloudflare ingress to the new endpoint.
5. Verify external access, authentication, data checks, notifications and USB/hardware functions.
6. Keep the old workload stopped but recoverable. Never run both against the same writable
   database, USB device, storage dataset or conflicting IP.

Rollback means stopping the new workload and restoring the old endpoint **with a reviewed data
reconciliation plan**. Changes written after cutover do not magically return to the old database.
Do not promise zero data loss from an OS rollback. Retire the original backups/host only after a
sufficient observation period and a successful independent restore drill.

| Proxmox habit | Nixlab equivalent |
|---|---|
| `pct list`, `qm list` | `homelab list` / `incus list --project lab` |
| `pct exec ID -- CMD` | `homelab exec ALIAS -- CMD` |
| `qm terminal` | `incus console NAME --project lab` or UI console |
| resource configuration | Profiles and desired instance config/devices |
| `vzdump` | Offline Incus exports + application dump + independent restic |
| `pvesh` | Authenticated Incus REST API / `incus query` |
| host config changes | Reviewed Git/NixOS generation and rebuild |

## Settings schema 0 to 1

Unversioned JSON remains readable through the Nix migration. Existing authentication,
trusted clients, addressing, mounts and guest declarations retain their values.
There is no background rewrite or automatic access tightening. An ordinary reviewed
change stages schema 1, shows the version transition and uses the existing activation,
confirmation and rollback flow. Keep the old source and JSON together: an older
source may not understand schema 1. The existing transaction recovery restores the
original source and settings together; do not copy only the new settings onto an
old checkout. Unknown future versions require compatible source, not hand-editing
the version number. The broker fails clearly when Nix is unavailable.


## Arcane retirement: safe transition to Incus-only projects

As of the new platform direction, **fresh NixLab installations do not create
a Docker/Arcane applications VM**, even if the installer records Compose
workloads as a future requirement. The latter are intended for independently
declarative Incus/Incus Compose projects after compatibility qualification.
The old `apps` settings exist only to keep legacy hosts readable while a
separately reviewed migration is built.

**Existing `lab/apps` is not deleted or stopped by changing
`apps.enable = false`.** NixLab no longer emits the old "stop apps" reconciliation
sentinel for disabled apps. The legacy VM's disk, Docker named volumes, Arcane
database, encryption key, Compose definitions and runtime secrets remain
physical data under Incus until the operator explicitly migrates or archives them.
A NixOS rollback does not restore changes to that data.

Before removing the legacy VM, perform the following read-only checks on the
actual host and record the results in private recovery notes:

```sh
sudo incus list --project lab
sudo incus config show apps --project lab --expanded
sudo incus storage volume list guests --project lab
sudo incus exec apps --project lab -- docker ps --all
sudo incus exec apps --project lab -- docker volume ls
```

The pool in the third command is an example; use the configured Incus storage
pool rather than assuming `guests`. Treat container environment variables and
the expanded instance configuration as **potentially sensitive**; do not post
raw outputs, Docker inspect payloads or credential files publicly.

Before cutover, create an independent, encrypted off-host recovery point with
actual filesystem/database consistency. Use `incus export` for the powered-off
VM plus any independent Incus custom volumes; Docker named volumes and bind
mounts inside its disk require their own documented consistency/restore checks.
NixLab's current grouped restic backup may cover only declared/observed
volumes, so inspect its coverage report rather than assuming every service
is protected.

New Incus Compose support, SOPS/age secret migration and automatic project-data
backups are tracked by #127/#133/#134/#128/#129/#130. **No Arcane volume is
automatically converted to an Incus Compose volume**. Restore each application
from reviewed configuration, its encrypted secret material and separately
validated state. Test the new endpoint before switching LAN DNS or Cloudflare
ingress, and keep the old VM recoverable until an independent restore drill.
