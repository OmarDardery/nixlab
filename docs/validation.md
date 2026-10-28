# Validation evidence and acceptance procedure

Validation date: 2026-10-27. Pinned Nixpkgs:
`718f670e845328e9c2cecdd832835712b2cbd065` (NixOS 26.05).
The workspace is an Ubuntu container, not the target bare-metal host. No activation,
formatting, VM start or production network change was performed.

## Results obtained in this workspace

| Check | Result |
|---|---|
| `nix-instantiate --parse` across all Nix files | Passed |
| NixOS host `system.build.toplevel.drvPath` evaluation | Passed with explicit test-only inventory/hardware fixture |
| NixOS applications VM system evaluation | Passed |
| Backup + Cloudflare enabled host variant evaluation | Passed; fixed a native restic cleanup-hook conflict during validation |
| Demo-enabled guest variant evaluation | Passed |
| ZFS host variant evaluation | Passed; pool import is guarded and force-import is disabled in final configuration |
| `homelab` package build | Passed |
| Applications VM `system.build.toplevel` build | Passed; produces a complete NixOS generation, does not prove guest boot |
| Native backup and monitoring unit derivation builds | Passed |
| Full host build dependency plan (`nix build --dry-run`) | Passed; includes Nix-built guest disk/image dependencies |
| Python safety + setup tests | 39 passed |
| Python compilation + YAML parsing | Passed |
| `docker compose ... config --quiet` | Passed using the pinned Nixpkgs Compose executable |
| Physical `/dev/kvm` acceleration, host firewall behavior, guest boot, UI login, sops decryption, real Cloudflare connectivity and restore | Not tested here |
| Complete host generation + apps QCOW2/metadata image build | Not completed here; host dependency plan evaluated, apps OS generation built separately |

The Python tests cover unowned-instance refusal, idempotent resource updates, type-change refusal,
CLI argument boundaries and exit status, failed-export archive preservation, original guest-state
recovery after failure, and network/storage preflight rejection. They use mocks and do not replace
live Incus integration tests. Both Nix grammar checks and real Nix evaluation were used; grammar
parsing alone was not called option validation.

The production `hypervisor` output intentionally fails while `site.ready=false`, required inventory
is empty and actual boot/root filesystem configuration is absent. Passing `validation-*` outputs
is not permission to install them. The test SSH key and test disk label are not production credentials.
The default VM image bootstrap does not continuously apply guest OS changes to existing disks.

## Installer and portal verification

The follow-up installer/portal implementation additionally passed:

- Setup-enabled NixOS evaluation with mounted ext4/XFS pools, Wi-Fi/iwd, Bluetooth,
  Nginx TLS proxy, unprivileged web service and root broker.
- Inherited-installation evaluation preserving `system.stateVersion = "25.11"`, root
  filesystem/boot configuration and NetworkManager ownership while applying managed policies.
- Complete **bootstrap OS** and **setup-enabled applications VM OS** generation builds.
- Packaged admin/installer builds and UI/broker/Nginx unit derivation builds.
- Real HTTPS handler tests for authentication, secure cookies, CA bootstrap, CSRF rejection,
  hostname/rebinding rejection and invalid-setting refusal; schema, snapshot, secret-isolation,
  recovery cleanup and timeout tests. The full suite currently contains 39 tests.
- JavaScript syntax, installer Bash syntax/ShellCheck, and browser QA of the dashboard,
  storage selection/role enforcement and radio/preferences pages against simulated hardware.

The execution sandbox rejects filesystem UNIX-socket connections. The HTTPS integration tests
therefore use an explicit **test-only loopback transport adapter** for the same broker handlers.
Production has no TCP broker mode. Actual UNIX socket access separation, systemd activation,
root privilege boundary, runtime mounts/radios, first boot, certificate enrollment, guest closure
transfer and the network-loss rollback guard still need integration testing on NixOS hardware.
The browser preview performed no host management actions and used reserved/fictitious inventory.
The installer was not executed against a real host in this workspace.

The preserved-base bootstrap means ordinary installation no longer requires supplying hardware
values in chat or writing Nix manually: the target collects actual inventory and the wizard requires
an initial configuration. Advanced/manual mode still uses the deliberately incomplete defaults.
The supported automatic import is a conventional fresh x86_64 install, not arbitrary external flakes.

## Repeatable source checks

```console
python3 -m unittest discover -s tests -v
find . -name '*.nix' -exec nix-instantiate --parse '{}' ';' >/dev/null
nix flake metadata
nix eval .#nixosConfigurations.validation-host.config.system.build.toplevel.drvPath
nix eval .#nixosConfigurations.validation-apps.config.system.build.toplevel.drvPath
nix eval .#nixosConfigurations.validation-services.config.system.build.toplevel.drvPath
nix eval .#nixosConfigurations.validation-demo.config.system.build.toplevel.drvPath
nix eval .#nixosConfigurations.validation-zfs.config.system.build.toplevel.drvPath
nix build .#packages.x86_64-linux.homelab .#packages.x86_64-linux.admin .#packages.x86_64-linux.installer --no-link
nix eval .#nixosConfigurations.validation-setup.config.system.build.toplevel.drvPath
nix eval .#nixosConfigurations.validation-inherited.config.system.build.toplevel.drvPath
nix build .#nixosConfigurations.validation-bootstrap.config.system.build.toplevel --no-link
nix build .#nixosConfigurations.validation-setup-apps.config.system.build.toplevel --no-link
node --check admin/static/app.js
bash -n install.sh
nix build .#nixosConfigurations.validation-apps.config.system.build.toplevel --no-link
nix build .#nixosConfigurations.validation-host.config.system.build.toplevel --no-link --dry-run
docker compose -f examples/demo/compose.yaml config --quiet
```

`.github/workflows/validate.yml` repeats source tests/evaluation, helper and apps-system builds,
and the complete host dependency plan with pinned action commits. A configured machine should
also run the full host build and VM image build before activation. CI job existence is not a claim
that its remote run has passed; inspect the actual run status.

## Host commissioning

After adapting real inventory and building the real `hypervisor` output:

```console
sudo systemctl status nixlab-kvm-check incus incus-preseed nixlab-instances
sudo incus --project lab list
sudo incus --project lab config show apps --expanded
sudo incus --project lab exec apps -- uname -a
sudo incus --project lab exec apps -- systemctl status incus-agent docker
sudo incus network show incusbr0
sudo incus storage show guests
sudo nft list ruleset
sudo systemctl status nixlab-metrics prometheus prometheus-node-exporter
```

Confirm `/dev/kvm` exists, the ioctl unit passed, VM configuration/Incus logs report KVM
acceleration, and the actual VM boots. Verify CPU/RAM/disk limits and the reserved IP. Test a
real unprivileged system container from a verified image too; a booted VM is not a container test.
Confirm guest DNS/DHCP, internet egress, guest-to-guest communication, and management isolation.
A guest must not reach host SSH/API or other private management addresses. Only the intended
trusted sources should reach the host management ports. Test LAN routing and allowed app port
from an actual LAN client, and deny tests from an untrusted source/interface.

Rebuild the same generation and confirm no duplicate pools, networks, profiles or instances.
Change a non-destructive resource limit, reconcile, inspect it, then repeat reconciliation and
confirm no further changes. Remove a declaration and verify the guest remains. Create an
unowned name collision in a disposable test project and verify reconciliation refuses it.
Restart the host firewall and verify DNS/egress still work. Reboot during a console-backed window
and verify Incus autostart, ZFS import if used, secrets, VM agent and application availability.

## UI, application and recovery acceptance

- Independently verify server fingerprints; enroll/revoke a test client and browser certificate.
- Confirm the Incus UI can create/start/stop/edit a disposable guest and open its terminal/console.
- Confirm snapshot creation, deletion and an isolated snapshot restore through CLI/UI as available.
- Forward Portainer through SSH; confirm local authentication, stack grouping, logs, stats and console.
  Test a separately named UI/Git-owned stack if editor/deployment workflows are needed.
- Deploy demo with a real runtime password, confirm `/health`, record a visit count, stop/start the
  Compose project and confirm PostgreSQL data survives. Confirm neither database port is published.
- Run the actual scheduled backup, inspect local archives and the independent restic repository,
  then restore an instance and custom volume into an isolated target. Check application data.
- Simulate a backup failure with a disposable target. Confirm nonzero unit state, old good backups,
  guest restart recovery and alert visibility. Configure an external receiver before assuming notifications.
- Test Cloudflare application hostname routing after account/DNS setup; confirm unmatched hostnames
  return the catch-all response and no management interface appears in public ingress.

Do not call this production-commissioned until these tests pass on the chosen hardware/network.

## Lightweight profile regression checks

CI evaluates `validation-minimal`, verifies that Docker, Nginx and local Prometheus are disabled
and that no application-onboarding unit exists. The full services fixture verifies that history
can be enabled. It also compares evaluated Nix defaults with the Python test fixture, validates
monitoring limits and parses the settings-export JavaScript. Runtime memory measurements and
physical-host activation remain separate target-machine checks.
