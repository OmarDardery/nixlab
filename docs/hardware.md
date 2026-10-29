# Hardware prerequisites for Incus workloads

Hardware & Disks reports USB serial identifiers, unique serial numbers, stable
paths and bound drivers through sysfs. Nothing is assigned automatically. Klipper
is an example workload, not a default installation or special portal feature.

Use an existing unique `/dev/serial/by-id/...` path directly in Incus, or declare
an optional alias through the reviewed portal form or a Nix module:

```nix
nixlab.settings.hardware.serialAliases = [{
  name = "controller";
  vendorId = "1234";
  productId = "5678";
  serial = "REPLACE-WITH-ACTUAL-UNIQUE-SERIAL";
}];
```

The exact match creates `/dev/nixlab/controller` and tells ModemManager to ignore
that tty. It does not broaden host permissions, assign a USB bus, install an app
or make a guest privileged. Devices without unique serials need a separately
reviewed physical-port identity; the alias form does not guess between devices.

Assign the device in Incus. For an unprivileged container:

```console
sudo incus config device add YOUR_GUEST controller unix-char \
  source=/dev/nixlab/controller path=/dev/controller \
  required=false mode=0660 gid=YOUR_GUEST_DEVICE_GROUP_ID --project lab
```

Use the numeric group inside that guest and add the application account to it.
`required=false` plus `source` enables Incus hotplug and permits boot without the
device. Native Incus `usb` assignment is the VM workflow and needs its own tests.

Test access as the application account, disconnect/reconnect, host reboot and
rejection of unrelated devices. Verify stable-path reconnect with the actual
adapter/kernel. Simulated character-device attachment does not certify physical USB
latency or printer operation.

Drivers/firmware use reviewed native NixOS modules through Configuration files.
Nixlab does not download arbitrary vendor driver scripts. GPU/PCI assignment and
IOMMU qualification are separate work. OS rollback does not reverse firmware.

For downtime-sensitive workloads, set `user.nixlab.backup=manual` in Incus or use
a backup maintenance exclusion. Nixlab excludes the whole shared-volume group and
reports partial coverage. This is not a physical interlock: Incus administrators
can still stop guests. Use the application's maintenance/backup procedures.
