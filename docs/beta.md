# Single-host beta candidate

Source version: **0.1.0-beta.1**. This is a candidate for homelab enthusiasts,
not a published or physically qualified beta release. The [qualification matrix](qualification.md)
and [roadmap](https://github.com/OmarDardery/nixlab/issues/19) record the missing evidence.
No tag, release or passed hardware result is implied by this version string.

NixLab is a NixOS hypervisor integration: NixOS owns the host; Incus owns guests,
networks, volumes and snapshots; optional Portainer owns Docker/Compose workloads.
Cockpit supplies host administration. NixLab connects these tools and coordinates
reviewed configuration, host updates and independent recovery. See [credits](projects.md).

## The console

- **Overview:** enabled management tools, host observations, all-project guest IPs
  and configured public application links.
- **Host:** networking, storage/hardware, optional services, access and advanced Nix files.
- **Backups:** backup coverage/status, independent recovery material and restore guidance.
- **Changes:** build/review/activate/confirm, including selecting main without typing a SHA.
- **Help:** local guides, copyable operational commands and upstream documentation/source.

IP inventory is read-only. Reported NIC addresses and configured reservations are
separate; missing guest agents do not produce invented addresses. Duplicate warnings
compare known managed-network namespaces, not unrelated isolated networks. Addresses
are not endpoint-health checks. Failed or stale observations remain visibly unknown.
Manage addresses, leases, ACLs and guest lifecycle in native Incus; see [networking](networking.md).

## Supported scope and remaining gates

Current scope is single-host x86_64 NixOS, Incus containers and intended KVM VMs,
optional isolated Docker/Portainer, reviewed host changes and restic coordination.
Source/virtual integration tests do not establish physical installation, real
apps-VM stability or independent replacement-host recovery. Backend/device/custom
secret coverage and application consistency require the documented drills.

Real-VM trials on the dedicated runner, physical fresh/upgrade/rollback/reboot and
management-recovery evidence, full off-host replacement recovery and successful
Pages publication still gate the beta. See [commissioning](commissioning.md) and
[recovery](recovery.md). Never treat a successful export as a successful restore.

This is not full Proxmox parity. Clustering/HA, universal hardware passthrough,
automatic external router/DNS changes, arbitrary live backup guarantees and
Proxmox backup-format compatibility are not qualified features. Existing optional
workloads and custom machine modules remain intact; UI simplification does not
delete guests, disks, volumes or settings.

Rollback of a host generation restores code/services, not application data or
Incus database migrations. Maintain independent backups. A versioned beta is
published only after the existing release workflow accepts current exact-revision
CI, physical and recovery evidence with reviewed limitations.
