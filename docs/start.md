# Start with Nixlab

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
