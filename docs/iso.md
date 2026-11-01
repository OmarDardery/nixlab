# Nixlab installation ISO

Build from a clean checkout of the reviewed branch:

```sh
nix build .#packages.x86_64-linux.installer-iso -o result-iso
```

The image is under `result-iso/iso/`. Tagged versions are available from
[Releases](https://github.com/OmarDardery/nixlab/releases), with the ISO,
`SHA256SUMS`, `manifest.json` and `TRIAL.md` attached directly. Download all four
into one directory and run `sha256sum -c SHA256SUMS`; write the `.iso`, not
GitHub's automatically generated source archive, to your USB drive.

The CI build also publishes an ISO and SHA256SUMS
as the `nixlab-installer-x86_64` workflow artifact, retained for 90 days. The artifact
also contains build metadata and the trial checklist. These
are candidate installation images, not hardware-certified releases. The media
uses the pinned NixOS minimal installer, with UEFI/USB boot support and no GUI or
running virtualization stack. SSH is disabled in the live environment. Hardware
boot compatibility and Secure Boot are not established by building the image.

CI also boots the produced ISO in disposable BIOS and UEFI VMs with one blank
virtual test disk and no network, checks the live installer commands and bundled-source exclusions,
and verifies that SSH and installed-system conversion are not enabled there.
It also checks that manually invoking the converter on the live OS is refused
before network discovery or configuration changes.
Image publication is gated on that check; its serial log is a separate artifact.
The guided installer is exercised in dry-run mode. This test does not install a target
OS or exercise first-boot conversion.

To repeat the same media check locally:

```sh
nix develop .#iso-test --command python3 tests/iso_boot.py result-iso/iso/*.iso --log iso-boot.log
nix develop .#iso-test --command python3 tests/iso_boot.py result-iso/iso/*.iso --uefi --log iso-boot-uefi.log
```

The runner uses KVM when accessible, otherwise QEMU software emulation. The live
image provides both a local screen and a 115200-baud serial console.

## Guided installation

1. Download the release assets (or extract the CI artifact), verify `SHA256SUMS`, and write the `.iso` to a USB device.
2. Boot the laptop/server in the firmware mode you want to install (UEFI or BIOS).
   This unsigned media does not provision Secure Boot keys.
3. Connect to the Internet; use `nmtui` for Wi-Fi if needed.
4. Run `sudo nixlab-setup`. It lists disk paths, capacity, model, serial/WWN and stable
   identity. Select a whole disk and host name. Mounted disks, the live USB, read-only
   devices and disks smaller than 32 GiB are not eligible.
5. Review the plan and type its exact erase phrase. **All data on that selected disk
   will be replaced.** Required tools and pinned flake input availability are checked before erasure;
   identity and mount state are then checked again immediately before the first write.
   Other disks are not selected. `sudo nixlab-setup --dry-run` prints a plan without writes.
6. The installer creates GPT with an ext4 root and the appropriate EFI or BIOS boot
   partition, generates the actual hardware configuration, copies root-only NetworkManager
   profiles and stages first-boot setup. BIOS uses a stable disk identity. Laptop lid
   policy keeps the installed server running with the lid closed.
7. Set the root password at the normal `nixos-install` prompt. After success, remove the
   USB and reboot. The installed console prints setup URL, generated password and CA fingerprint.
8. Verify the fingerprint, sign in, add your SSH key and complete **Build & review →
   Activate → Confirm access**. Only the reviewed activation provisions the hypervisor.

The console wizard intentionally supports one ext4 boot disk. Custom RAID, encryption,
ZFS root and nonstandard boot layouts use the manual path below. A failed guided install
can leave the selected disk partially prepared; inspect it before starting another erase.
Disk preparation is a local/SSH console action; there is no dedicated browser
formatting endpoint. No disk is erased merely by booting this image.

## Manual installation

1. Write the ISO to a USB device using an image writer, then boot the target machine.
2. Connect the live environment to the Internet. The source is bundled, but the
   installed-system and first-boot builds can still need packages/flake inputs.
3. Use the standard NixOS partitioning and filesystem tools to prepare the disks
   you explicitly selected. Mount the target root at `/mnt`, and mount its boot
   filesystem as required for your selected bootloader. The manual staging command
   below does not partition disks or choose a boot device. Preserve existing data disks
   unless you deliberately selected them for this installation.
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

The image provides a guided console installer followed by web configuration
on the installed host. It is not a graphical partitioning wizard or a fully offline installer. The installed-system conversion command
refuses this RAM-backed live OS. Use the staging command on the mounted target
instead.

The bundled source is initialized as a local Git snapshot. The embedded upstream revision
is recorded independently in `platform-release.json`. Use the portal's reviewed commit
upgrade or `nixlab-admin config review --revision FULL_COMMIT_SHA` for later source updates;
no manual upstream-branch setup is required. Plain `git pull` is not the managed update path.
See [operations](operations.md) for upgrades, recovery and account boundaries.

## Publishing a version

After merging the release workflow and reviewed changes into `main`, make sure
`VERSION` contains the intended version, then push its matching tag:

```sh
git switch main
git pull --ff-only
git tag -a "v$(cat VERSION)" -m "Nixlab $(cat VERSION)"
git push origin "v$(cat VERSION)"
```

Actions validates the tagged commit, builds the ISO, boots it under BIOS and UEFI,
and publishes those exact tested bytes as release assets. The publishing job alone
has `contents: write`; it uses the built-in `GITHUB_TOKEN`, without a personal token.
The tag must match `VERSION`, and the artifact manifest must match both the version
and commit. Versions such as `0.1.0-rc1` become prereleases; plain versions become
stable releases. Release assets are not subject to the CI artifact's 90-day retention.

Publication first creates a draft with all assets, then makes it public only after
the uploads succeed. If publication fails after creating that draft, inspect and
delete the incomplete draft (keep the tag), then rerun the failed job. Existing
releases are never overwritten. To retry a failed build, rerun the workflow; you can
also run **Validate Nixlab** manually with the existing version tag selected. Manual
branch runs build artifacts without publishing a release. Do not move a published
tag; bump `VERSION` and create a new tag for changed source.
