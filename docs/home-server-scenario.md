# Generic homelab acceptance scenario

NixLab provides a managed NixOS host, Incus guests, an optional isolated
Docker/Portainer applications VM, private management interfaces, change
reviews and recovery workflows. **There are no built-in application-specific
workloads.** Choose your own guest images, container services and Compose
stacks independently.

## 1. Review your host

Use an existing NixOS installation with a working local console or SSH
recovery path, available storage, a backup of existing data, and supported
virtualization. A Docker apps VM requires adequate host memory.

```console
sudo nix --extra-experimental-features 'nix-command flakes' run \
  github:OmarDardery/nixlab#install -- --interactive --plan-only
```

Choose the topology and access model: `containers`, `compose`, or
`both`; `lan`, `private`, or `public`; and `nat`, `lan`, or
`vlan`. Select monitoring, backup intent and optional laptop lid policy.
Selections describe **infrastructure**, not a list of apps to install.

For unattended review, save a root-owned JSON file without credentials:

```json
{
  "version": 1,
  "workloads": "both",
  "access": "private",
  "network": "nat",
  "laptop": false,
  "monitoring": true,
  "backup": true
}
```

Then run:

```console
sudo nix --extra-experimental-features 'nix-command flakes' run \
  github:OmarDardery/nixlab#install -- \
  --answers /root/nixlab-answers.json --plan-only
```

Remove `--plan-only` when ready to apply. The installed setup preserves
existing host addressing, uplinks, filesystems and guest ownership. Existing
answer files containing obsolete example-app flags are accepted for
backward compatibility, but those flags are discarded and never deploy apps.

```console
sudo nixlab doctor
sudo nixlab install-plan
sudo incus list --all-projects
systemctl --failed
```

## 2. Add any system container or virtual machine

Use the Incus UI or CLI to choose guest type, project, image, storage,
network, resource limits and start policy. Keep persistent guest data
separate from disposable systems; do not silently recreate existing guests.

If the workload needs a LAN IP, explicitly review the host bridge, VLAN,
routing and firewall path. The default Incus NAT address is not a
LAN-reachable address by itself. Bridging a management NIC requires a
local-console recovery path; Wi-Fi must not be treated like Ethernet.

For DNS or other essential LAN services, verify both required protocols,
stable addressing and a fallback before changing any router or client
configuration. NixLab does not automatically reconfigure your router.

## 3. Attach physical hardware when needed

Discover the device's persistent ID on the host, then explicitly attach it
to the selected guest using Incus. Never assume the physical device name
or the guest application's user account.

```console
ls -l /dev/serial/by-id/
sudo incus config device add GUEST peripheral unix-char \
  source=/dev/serial/by-id/ACTUAL_DEVICE path=/dev/guest-device \
  --project lab
sudo incus exec GUEST --project lab -- ls -l /dev/guest-device
```

This is an illustrative command only. Check device permissions, application
access, reconnect after USB removal, and recovery across host reboot.
Restrict control interfaces to trusted LAN or VPN clients.

## 4. Deploy any Compose stack

If you enabled the apps VM, access Portainer through the NixLab console
(or use Docker Compose inside the isolated guest). Choose an image
version or digest intentionally, provide secrets outside public source,
and review published ports and origins. Deploy generic Compose YAML
using Portainer or a protected directory inside the apps VM.

```console
sudo incus exec apps --project lab -- docker info
sudo incus exec apps --project lab -- docker ps
```

Back up each stack's persistent Docker volumes separately. Host
configuration rollback is not application-data rollback. Only selected
applications should be published through a reviewed external ingress;
NixLab, Incus, Cockpit, Portainer and SSH remain private.

## 5. Commission and recover

After installation and at least two real reboots, verify host networking,
management access, guest startup, storage mounts, any hardware devices,
local service connectivity and application authentication. Configure an
independent encrypted off-host backup and perform an isolated restore
drill with real workload data.

Record commissioning evidence. Neither the installer nor a passing CI
check certifies the physical host until these observations are completed.
