# Plain NixOS server installation

Run this on an installed x86_64 NixOS system with Internet access and working
hardware virtualization. A desktop, SSH key and second repository are unnecessary
when installing from the physical console.

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/nixlab#install
```

The flake reference is `github:OmarDardery/nixlab#install`, with no `.com`.
The packaged installer carries its own platform source and tools. It stages and
evaluates the preserved configuration before publishing installation state,
checks keys/network/Wi-Fi/KVM, builds the full stack and uses the durable
activation/rollback transaction. Existing filesystems are never formatted.

It retains the installed users, bootloader, mounts and current addressing.
Retaining DHCP does **not** assign `192.168.1.240`; reserve that address at the
router or explicitly configure static addressing afterward. Console output,
the homepage and `sudo nixlab doctor` use the detected assigned address.

## Normal choices

Append options after `--` to the command:

| Option | Purpose |
|---|---|
| `--ssh-key /path/to/laptop.pub` | Enroll a client public key for the `operator` account |
| `--ssh-password-login` | Explicitly retain password login for existing non-root Linux users |
| `--no-apps` | Omit the Docker/Portainer VM on first installation |
| `--adopt-incus` | Explicitly select the backup-and-adopt workflow described below |
| `--no-update` | Keep the installed platform source on retry |
| `--wizard` | Bootstrap the web setup instead of installing the full stack |
| `--guest-subnet 10.78.0.0/24` | Select an unused private guest subnet |

The default disables SSH password login and root SSH login. This policy is
printed before activation. A console installation can proceed with no key and
enroll one later:

```sh
sudo nixlab enroll-key /path/to/laptop.pub
```

The key must come from the **client** that will connect. Generating a private key
on the server does not authorize the laptop. A remote SSH installation must have
an administrator key or explicitly retain password login. Detection also checks
for an sshd process ancestor because sudo can remove SSH environment variables.

Cockpit uses an existing non-root administrator's Linux password. If no usable
local administrator password exists, the installer provisions a stable operator
password outside the Nix store and shows it only after checking the active hash.
The portal, Linux account, Incus browser certificate and Portainer account are
separate identities. Enroll the Incus browser certificate through the portal.

## Retry and source updates

Rerun the same command after failure or disconnection. `--resume` remains an
accepted compatibility flag, but is no longer needed. An active build or
activation is reattached; it is not duplicated. A completed, unchanged
installation is health-checked without creating another configuration job.

Platform updates are staged from the command's source, with a checkpoint under
`/var/lib/nixlab-admin/source-before-install-*`. Machine settings, modules and
the preserved installed configuration remain separate. If tracked platform files
were edited locally, the update is skipped and their paths are printed; those
edits are retained. Review/move overrides into `machine/modules/` before updating.
The installer checks for concurrent source changes before publication. A failed
build leaves its log and candidate for inspection, rather than erasing evidence.

Credentials are generated once outside the activation rollback manifest, so a
failure after database initialization does not cause password rotation on retry.
The setup password is available again only while it matches the active account.
Stored Portainer credentials are printed only after an authenticated API login.
If that password has changed in Portainer, use the existing account or explicitly
run recovery; a bootstrap file is never asserted to be the current password.

## Adopt an existing Incus host

The installer inventories projects, instances, profiles, networks, pools and
volumes. Interactive installation asks before adopting; unattended installation
requires `--adopt-incus`. It exports existing guests and custom volumes and saves
metadata under `/var/lib/nixlab-admin/incus-adoption/<attempt>/` before activation.
A failed export prevents conversion. Running-guest exports are crash-consistent;
quiesce databases or stop guests for an application-consistent maintenance backup.
Ensure free local space for these exports and keep independent off-host copies.

Nixlab chooses a separate unused bridge/pool, a non-overlapping subnet, and the
`lab` project. Existing native guests, their disks, profiles, networks and pools
remain Incus-owned. Their bridge forwarding and DNS/DHCP are retained at the
NixOS firewall boundary; native Incus ACLs remain in force.

Clustered Incus and a pre-existing unrelated `lab` project require an explicit
migration plan and are refused before conversion. Hosts with a Nixlab checkout
use automatic resume instead of adoption. Pool, bridge or subnet changes to an
already managed installation remain migrations, not installer retry operations.
NixOS rollback restores host configuration, not Incus schema or guest data.

## Health and recovery

```sh
sudo nixlab doctor
sudo nixlab portainer reset-password
```

Installation confirms only after checking the Incus API/UI, Nixlab portal,
Cockpit and enabled Portainer proxy/status/administrator initialization over TLS.
Tests of local endpoints cannot prove connectivity from every LAN/VPN client.
The startup identity service uses the assigned management address; listeners are
independent of DHCP address readiness and remain restricted by the firewall.

`doctor` returns a nonzero status on failed endpoint checks and saves a bounded
report at `/var/lib/nixlab-admin/last-health.json`, without tokens or passwords.
Transaction logs stay under `/var/lib/nixlab-admin/jobs/<id>/build.log`.
The installer prints the original error and saves `installation-failure.json`;
rollback errors are recorded separately. Recovery skips absent services and
NetworkManager connections that no longer exist.

Portainer recovery downloads the official helper before stopping Portainer,
backs up `/srv/portainer` inside the apps VM, resets the database credential,
synchronizes the host/guest credential files and restarts Portainer even if the
helper fails. It verifies readiness and authenticates before showing the new
username/password. The backup path is printed. The official helper currently uses
its upstream `latest` image; the helper output is retained in a root-only file if
its output format changes. This command does not delete stacks or guest disks.

## Installation regression checklist

| Observed problem | Implementation / regression coverage |
|---|---|
| Wrong flake syntax | One canonical copyable command in README and quick start |
| Mandatory key at local console; server key confused with laptop key | Console-safe key policy, sshd-aware remote detection, `enroll-key` and policy output |
| Late validation, partial checkout, manual resume/update | Disposable planning, atomic checkout publication, transaction reattachment and protected staged source updates |
| Existing Incus blocked or at risk of overwrite | Explicit inventory/export/adoption with separate owned resources and collision refusal |
| Nginx SSL listener missing certificates | Existing `onlySSL` fix retained; startup VM boots the actual apps-enabled proxy with verified upstream TLS |
| Duplicate image fingerprint on retry | Existing idempotent import/alias implementation and `test_app_image_import.py` retained |
| Rollback masked original error or restarted missing units/connections | Separate error fields, loaded-unit checks and existing-UUID checks |
| Cockpit address unavailable at boot; stale `.240` links | Address-independent listeners, runtime TLS identity, observed URLs; DHCP/reboot VM fixture |
| Success despite broken interfaces | TLS endpoint and Portainer administrator readiness gate before confirmation |
| Password login disappeared unexpectedly | Printed policy, remote installation guard and explicit password-login option |
| Portainer bootstrap secret disagreed with database | Stable credential files and authentication before displaying stored credentials |
| Manual Portainer recovery left it stopped | Backed-up reset command with restart in cleanup and authenticated verification |
| TLS deprecation and confusing shutdown logs | Current `--tlscert`/`--tlskey` flags; readiness/exit status drives diagnostics |

ISO, microcode, serial console, GRUB, SquashFS and Ventoy changes are outside
this workflow and were deliberately left untouched. VM tests use controlled
Portainer/Incus TLS backends for the proxy/reboot boundary; actual registry pulls,
nested KVM deployment and physical-host commissioning still need host testing.

Upstream recovery/flag references:
[password reset helper](https://github.com/portainer/helper-reset-password),
[deprecated flags](https://docs.portainer.io/advanced/deprecated).
