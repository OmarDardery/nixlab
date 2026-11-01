# Start with Nixlab

For an installed NixOS server without a desktop, run:

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/nixlab#install
```

Use the same command after an interrupted attempt. Local console installation
does not require an SSH key. Follow [plain NixOS installation](plain-nixos.md)
for adoption, key enrollment, credential recovery and diagnostics.

For the separate installation-media route:

Verify the ISO checksum and follow [installation](iso.md). The installer prepares
only the selected disk. Boot the installed system, verify the console fingerprint,
sign in, add an SSH key and build, review, activate and confirm the host setup.

Open Incus from the portal and enroll its browser certificate. Create your first
container or VM in Incus. For application helpers, run `sudo nixlab-helpers doctor`
then use `sudo nixlab-helpers run adguard --project lab` from the host terminal.
Review [networking](networking.md) before exposing LAN services.

Configure independent backups under Recovery and perform an isolated restore.
The [trial checklist](trial.md) covers reboot, networking, devices and rollback.
Advanced settings, Nix modules, optional integrations and maintenance remain in
the [operations guide](operations.md).

