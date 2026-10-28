# Nixlab installation ISO

Build from a clean checkout of the reviewed branch:

```sh
nix build .#packages.x86_64-linux.installer-iso -o result-iso
```

The image is under `result-iso/iso/`. The CI build publishes an ISO and SHA256SUMS
as the `nixlab-installer-x86_64` workflow artifact, retained for seven days. These
are candidate installation images, not hardware-certified releases. The media
uses the pinned NixOS minimal installer, with UEFI/USB boot support and no GUI or
running virtualization stack. SSH is disabled in the live environment. Hardware
boot compatibility and Secure Boot are not established by building the image.

CI also boots the produced ISO in a disposable BIOS VM with no writable disks
or network, checks the live installer commands and bundled-source exclusions,
and verifies that SSH and installed-system conversion are not enabled there.
It also checks that manually invoking the converter on the live OS is refused
before network discovery or configuration changes.
Image publication is gated on that check; its serial log is a separate artifact.
This test does not install a target OS or exercise first-boot conversion.

To repeat the same media check locally:

```sh
nix develop .#iso-test --command python3 tests/iso_boot.py result-iso/iso/*.iso --log iso-boot.log
```

The runner uses KVM when accessible, otherwise QEMU software emulation. The live
image provides both a local screen and a 115200-baud serial console.

## Installation

1. Write the ISO to a USB device using an image writer, then boot the target machine.
2. Connect the live environment to the Internet. The source is bundled, but the
   installed-system and first-boot builds can still need packages/flake inputs.
3. Use the standard NixOS partitioning and filesystem tools to prepare the disks
   you explicitly selected. Mount the target root at `/mnt`, and mount its boot
   filesystem as required for your selected bootloader. No Nixlab command formats
   disks or guesses a boot device. Existing disks should be left untouched unless
   you deliberately selected them for this installation.
4. Generate the target's hardware/configuration files:

   ```sh
   sudo nixos-generate-config --root /mnt
   ```

5. Review `/mnt/etc/nixos/configuration.nix`, especially the bootloader, boot device,
   networking, user access and hardware imports. Configure Wi-Fi if it is the only
   uplink; the installed system needs connectivity on first boot. Follow the
   [official installation manual](https://nixos.org/manual/nixos/stable/#sec-installation).
6. Stage Nixlab's first-boot module:

   ```sh
   sudo nixlab-stage-install --root /mnt --confirm-target /mnt
   ```

   This preserves the generated configuration as `nixlab-base.nix` and writes a
   small wrapper importing it and `nixlab-firstboot.nix`. It does not run the OS
   installer. Repeating it with the same module is harmless; conflicting partial
   staging is refused. Further base configuration edits go into `nixlab-base.nix`.
7. Install normally, setting the root password when requested:

   ```sh
   sudo nixos-install --root /mnt
   ```

8. Reboot into the installed system and remove the USB. The first-boot systemd
   service runs the bundled Nixlab converter. It preserves the target hardware,
   bootloader, users and stateVersion and starts the authenticated setup portal.
   Its console output gives the management URL, generated administrator password
   and CA fingerprint. Confirm the fingerprint before signing in. Add your SSH key
   and finish the settings/build/activate/confirm workflow.

No common administrator password, private key, machine inventory or disk layout
is baked into the ISO. The embedded source filter excludes machine/, local/,
installed-system/, site-modules/, secrets/ and the root hardware placeholder.
Build from a clean platform checkout: filters cannot sanitize secrets manually
written inside platform code.

## Recovery and limitations

The conversion service is `nixlab-firstboot.service`. It lets normal boot complete
before rebuilding, with a two-hour runtime limit. Inspect its journal if it
fails, fix connectivity or the reported condition, and restart it. Existing
installation inventory is resumed rather than overwritten. Partial source
creation without intact inventory requires administrator inspection. A success
marker prevents conversion on later boots. Root can always inspect the generated
base configuration before installing or use the normal NixOS recovery workflow.

This first image has a console installation flow, followed by web configuration
on the installed host. It is not yet a Proxmox-style graphical partitioning
wizard or a fully offline installer. The installed-system conversion command
refuses this RAM-backed live OS. Use the staging command on the mounted target
instead.

The bundled source is initialized as a local Git snapshot because the ISO does
not carry Git history. Before a later platform upgrade, fetch and review the
chosen upstream ref, then switch to a new branch tracking that ref. Plain
`git pull` on the initial `nixlab-iso` branch has no upstream. Do not force a
checkout over local edits; machine/ remains ignored and separate from source.
