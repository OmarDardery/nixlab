# Target-machine commissioning

This checklist is acceptance testing for the implemented single-host release. Use a
spare installation disk or a verified recovery path. Record hardware model, firmware
mode, CPU, NICs, disks, exact source revision, results and relevant redacted logs.

## Installation and reboot

- Start with a plain installed NixOS system. Run the command in the
  [installation guide](plain-nixos.md); verify preserved boot configuration,
  accounts, filesystems and NetworkManager/DHCP or static addressing.
- On a host with existing Incus guests, review the inventory and adoption backups;
  verify guest disks and native configuration survive conversion.
- Interrupt an installation and rerun the same command. Verify automatic resume
  and unchanged credentials. Confirm completed installs do not duplicate resources.
- Test password SSH from a trusted laptop, Cockpit login and host terminal, Incus
  browser enrollment and Portainer authentication. Verify URLs match the assigned
  address and the console CA fingerprint before trusting the management CA.
- Reboot twice with delayed DHCP if applicable, without manual service restarts.
  Run `sudo nixlab doctor`; check `nixlab-management-health.service`, Cockpit
  socket/service, apps VM, proxy and usable credentials after each boot.
- Test explicit key-only SSH with a client public key and console recovery available.
  Check trusted source CIDRs and absence of public management ingress.

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
