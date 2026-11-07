# Validation and acceptance

The supported installation target is a conventional installed x86_64 NixOS server.
The pinned Nixpkgs revision is recorded in `flake.lock`. Use the latest successful
[validation run](https://github.com/OmarDardery/nixlab/actions/workflows/validate.yml)
for the exact source revision being installed.

## Automated checks

The workflow runs these checks on every pull request:

- Python tests for installation planning/resume, source preservation, credentials,
  adoption/export, transactions, rollback, recovery and guest reconciliation.
- JavaScript and Chromium workflow checks against a controlled backend.
- Nix parsing, direct conversion evaluation and configuration variants, including
  preserved boot/users/mounts/stateVersion, NetworkManager, Wi-Fi, storage and apps.
- `checks.x86_64-linux.portal`: real NixOS UNIX-socket permissions, HTTPS portal,
  PAM login, Cockpit WebSocket terminal, origin rejection and password SSH policy.
- `checks.x86_64-linux.startup`: NetworkManager/DHCP startup and reboot with the real
  apps-enabled nginx proxy. Verify trusted TLS, actual-address URLs, Cockpit login
  and terminal, password SSH and the automatic management health service on each boot.
  Incus/Portainer HTTPS backends are controlled fixtures in this test.
- `checks.x86_64-linux.management`: Docker forwarding boundaries, including restart.
- `checks.x86_64-linux.foundation`: real Incus containers, guest preservation,
  network access, shared-profile impact/ETag observation and drift refusal,
  backup/export and isolated restore data integrity.
- Complete host, apps guest disk/metadata, installer and administration tool builds.

Run the lightweight source checks locally:

```sh
python3 -m compileall -q admin installer scripts tests helpers
python3 -m unittest discover -s tests -v
node tests/test_homepage.js
node tests/test_operations_ui.js
node tests/test_foundation_ui.js
```

Run integration checks on a NixOS-capable builder with KVM:

```sh
nix build .#checks.x86_64-linux.portal .#checks.x86_64-linux.startup --no-link -L
nix build .#checks.x86_64-linux.management .#checks.x86_64-linux.foundation --no-link -L
```

## Target-host acceptance

Automated fixtures do not establish physical NIC/USB behavior, a complete nested-KVM
apps deployment, live registry/helper recovery or independent off-host restore.
Follow [commissioning](commissioning.md) on a recoverable host. Record the exact
source revision, installed hardware, reboot results and redacted failure reports.
The installer is not run against a production machine from this development workspace.

The manual `hypervisor` output deliberately refuses incomplete inventory. Never
install a `validation-*` configuration: its addresses, keys and hardware are fixtures.
OS rollback does not roll back guest data or the Incus database schema.

The `guest-deployments` check boots two real NixOS Incus containers and tests
ordered in-place OS updates, preserved data, health rollback, dependency failure,
a killed export/import worker, and resume after rebooting the controller. It does
not establish apps-VM/KVM or authenticated Portainer qualification.
