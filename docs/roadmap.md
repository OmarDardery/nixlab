# Focused single-host foundation

Nixlab prepares, maintains and recovers a NixOS host for Incus. Incus remains the
virtualization UI. See the [ownership contract](ownership.md).

| Area | Delivered behavior |
|---|---|
| Host lifecycle | ISO/preserved-installation setup, reviewed changes, source/OS updates, confirmation recovery |
| Ethernet | Host bridges/VLANs, management addressing, guest firewall integration, trusted LAN access, narrow outbound LAN exceptions |
| Hardware | USB serial discovery, selected stable aliases, native Incus attachment guidance, existing UUID filesystems/ZFS datasets |
| Integrations | Incus/Portainer launcher, pinned Community Scripts Incus CLI wizard, explicit verification labels |
| Ownership | Ordinary native/helper resources remain native; declared infrastructure drift is refused before preseed |
| Recovery | Atomic generations, per-group downtime, shared-volume grouping, maintenance exclusions, independent restic, archive recovery |

The portal has no guest lifecycle/console/snapshot editor, application store or
arbitrary script runner. Legacy guest CLI/API actions remain for compatibility.
Docker and metrics history stay optional. Klipper is an example, not a default
recipe or product focus.

Source checks/Nix evaluation are distinct from live evidence. CI includes a real
container/firewall/device-hotplug/restore test and builds the helper runner.
Simulated character devices do not certify physical USB. Community Scripts
qualification targets remain unverified until live install/restore evidence exists.

Before deployment, finish [commissioning](commissioning.md): actual installation,
first boot, physical Ethernet/VLAN access, management-loss rollback, hardware
reconnect/reboot and an independent off-host restore. Cluster HA, automatic
Proxmox conversion, graphical disk erasure, GPU/PCI qualification and PBS-format
compatibility remain separate scopes.
