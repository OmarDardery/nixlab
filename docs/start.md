# Start with Nixlab

For an installed NixOS server without a desktop, run:

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/nixlab#install
```

For a fresh interactive installation, the installer asks about workloads,
network access, DNS, printer hardware, monitoring and the apps VM. It displays
an explicit plan **before** the normal reviewed NixOS activation. Use
`--interactive` to request the interview explicitly, `--answers /root/answers.json`
for headless provisioning, or `--plan-only` to preview without publishing.
Non-TTY operation never waits for input. Answers files contain **no secrets**.
See [the home-server scenario](home-server-scenario.md) for AdGuard Home,
Klipper USB passthrough, AIOStreams Compose and backup/restore checks.

Use the same command after an interrupted attempt. Local console installation
does not require an SSH key. Follow [plain NixOS installation](plain-nixos.md)
for adoption, key enrollment, credential recovery and diagnostics.

SSH password login works for non-root Linux accounts. Use your existing Linux
password, or the verified operator password printed by the installer. The host
disables desktop startup and suspend/hibernation. After reboot, run
`sudo nixlab doctor` to check the management stack.

Open Incus from the portal and enroll its browser certificate. Create your first
container or VM in Incus. For application helpers, run `sudo nixlab-helpers doctor`
then use `sudo nixlab-helpers run adguard --project lab` from the host terminal.
Review [networking](networking.md) before exposing LAN services.

Configure independent backups under Recovery and perform an isolated restore.
The [trial checklist](trial.md) covers reboot, networking, devices and rollback.
Advanced settings, Nix modules, optional integrations and maintenance remain in
the [operations guide](operations.md).

