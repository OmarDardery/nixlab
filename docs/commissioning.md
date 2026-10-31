# Target-machine commissioning

This checklist is acceptance testing for the implemented single-host release. Use a
spare installation disk or a verified recovery path. Record hardware model, firmware
mode, CPU, NICs, disks, exact source revision, results and relevant redacted logs.

## Installation and first boot

- Boot the final ISO on the chosen laptop in its intended UEFI/BIOS mode. Verify
  checksum, console, keyboard, display and network. Secure Boot key enrollment is
  outside this unsigned installer.
- Check `sudo nixlab-setup --list` and `--dry-run`. Verify the installation USB and
  all mounted disks are excluded; verify model, serial and capacity of the target.
- On the disposable target only, complete guided installation and set its root password.
  Verify untouched data disks retain their filesystems/data. Boot without the USB.
- Observe `nixlab-firstboot.service`, its completion marker, management URL, generated
  password and CA fingerprint. Verify preserved hardware/boot configuration and Wi-Fi
  profiles if used. Test interrupted first boot/resume with console access available.
- Complete the portal wizard and confirm the reviewed activation. Check SSH keys,
  trusted source CIDRs, TLS identity and absence of public management ingress.

## Hypervisor and storage

- Check `systemctl --failed` and the KVM/Incus/preseed/instance units. Verify the KVM
  ioctl check, then actually boot a VM and an unprivileged Linux system container.
- Verify guest agent, DHCP/DNS, Internet egress, reserved IPs and resource limits.
  Test management access rejection from a guest and an untrusted client.
- Add an existing filesystem using a file module, review/build/switch/confirm, then
  reboot. Verify UUID mount and pool mappings. Check missing required data disks keep
  management reachable while dependent storage/backup services refuse fallback writes.
- Rebuild unchanged configuration and verify no duplicate/recreated guests, pools or
  profiles. Verify manually stopped guests and persistent disks survive appropriate
  unrelated rebuilds. Test declared-field drift rejection and explicit resolution.
- Test the lid policy, power supply and intended battery behavior before closing a
  running laptop. Validate optional smartd hardware support and Tailscale SSH if selected.

## Upgrade, roles and recovery

- Enable the management stack and enroll the Linux operator password separately
  from portal accounts. Test Cockpit host terminal/sudo, Incus guest consoles and
  Portainer stack consoles. Verify private source restrictions, custom ports, TLS
  renewal and access after Docker restart. Exercise guarded password rollback
  with console access available. See [homelab management](homelab.md).

- Exercise a module upload, settings-only change, package refresh and exact-SHA source
  upgrade. Review input/file changes and activation output; verify preserved `machine/`.
- Interrupt publication/activation with console recovery available. Confirm timeout
  recovery restores OS, source, settings and selected credentials after reboot. OS
  recovery must not be treated as guest/database rollback.
- Test named administrator, operator and viewer accounts. Verify forbidden operations
  fail server-side and role/password changes revoke sessions. Check native Incus/SSH
  privileges separately; they do not inherit portal roles.
- Create a guest snapshot, export and restore to a new stopped name. Verify checksum,
  autostart suppression, address-conflict rejection, review/release and actual data.
- Run the configured independent restic backup. Simulate a failed export and interrupted
  shutdown; verify formerly running guests restart and previous good off-host backups remain.
- Recover an archive from off-host storage, register it, restore the guest/custom volumes
  and verify an application/database. Record recovery time. A successful upload is not
  a restore drill.
- Download configuration and encrypted-secret kits, decrypt off-host, verify manifests,
  and stage on a spare NixOS machine. Test recovery with that machine's own hardware/boot
  configuration and deliberate NIC/disk remapping.
- Verify offline guides, diagnostics exclusions, certificate renewal and configured
  failure notification delivery. Configure external receivers before relying on alerts.

## Ethernet, serial devices and upstream helpers

- With local console access, move management onto a bridge, apply/confirm, and reboot.
  Repeat for a tagged VLAN on the actual switch. Test both the inherited networking
  backend and NetworkManager where supported; interrupt activation to verify rollback.
  Confirm no duplicate IP remains on a bridge port and existing Wi-Fi still works.
- In Incus, attach a guest to the LAN bridge and another to an Incus-managed private
  bridge. Register only the private bridge for host firewall integration. Verify DHCP,
  DNS, egress, trusted-LAN routing, guest ACLs and blocked access to private management.
  Test narrowly scoped private-access exceptions against the intended service.
- Select a real USB serial identity, create a stable alias, then attach it using Incus.
  Verify guest user/group permissions, disconnect/reconnect, host reboot and an actual
  workload. Klipper is one possible workload; there is no default printer recipe.
- Run `sudo nixlab-helpers doctor --project lab`, then qualify each selected helper
  through create, application health, reboot, update, backup, independent restore and
  cleanup. Record host generation, Incus version, source revisions, image/application
  versions and project/storage/network choices. The shipped targets are unverified;
  upstream support alone does not justify changing that label.
- Back up two independent groups and one pair sharing a writable volume. Verify
  immediate recovery per group, stopped guests staying stopped, cancellation recovery,
  two retained complete generations, and that exclusions report partial coverage.

Only call the selected hardware/network commissioned after these checks pass. Multi-host
HA, fencing/shared-storage qualification and broad third-party helper compatibility are
separate product scopes, not outcomes established by this checklist.
