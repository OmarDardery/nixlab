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

For this homelab, verify each actual service rather than assuming old IDs are current:
AdGuard, aioStreams, Vaultwarden, the Hermes NixOS container, and Klipper/Moonraker/Mainsail
have different data and device needs. Klipper USB permissions/stable device paths and Vaultwarden
vault/database consistency deserve explicit migration tests. Incus does not inherit Proxmox
LXC device mappings, nesting flags or config syntax automatically.

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

Choose one control plane per stack: Nix/CLI-owned files or Portainer Git/UI-owned state. Back up
Portainer's own state separately from application data; its own backup excludes Docker volumes.
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
