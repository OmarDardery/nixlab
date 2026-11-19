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
| `--no-ssh-password-login` | Choose key-only SSH; remote installs then require a client key |
| `--no-apps` | Omit the Docker/Arcane VM on first installation |
| `--adopt-incus` | Explicitly select the backup-and-adopt workflow described below |
| `--no-update` | Keep the installed platform source on retry |
| `--wizard` | Bootstrap the web setup instead of installing the full stack |
| `--guest-subnet 10.78.0.0/24` | Select an unused private guest subnet |

The default enables SSH password login for non-root Linux accounts, including
`operator`. Root SSH login stays disabled. The installer prints this policy before
activation; rerunning it enables password login unless `--no-ssh-password-login`
or a file-owned setting explicitly selects key-only access. A console installation
can proceed with no key and enroll one later:

```sh
sudo nixlab enroll-key /path/to/laptop.pub
```

Key enrollment leaves the rollback guard armed. Open a new SSH session using
the enrolled key, verify access, then run the printed
`sudo nixlab-admin config confirm TRANSACTION_ID` command from that session.
The portal can also confirm the pending transaction. If access fails, leave it
unconfirmed so the deadline restores the previous generation.

The key must come from the **client** that will connect. Generating a private key
on the server does not authorize the laptop. A remote SSH installation using key-only access must have an administrator key. Detection also checks
for an sshd process ancestor because sudo can remove SSH environment variables.

Cockpit uses an existing non-root administrator's Linux password. If no usable
local administrator password exists, the installer still provisions a stable
private operator credential for compatibility. It never displays or returns that
password. For a persistent managed password, use **Host → Services → Local operator
password for Cockpit** in the portal, then review/activate the guarded change.
For unmanaged accounts (`cockpit.managePassword = false`), an authorized host
terminal can use `sudo passwd operator`. A manual password change does not update
a managed password file and may be overwritten by later activation. Existing
account passwords are preserved.
The portal, Linux account, Incus browser certificate and Arcane account are
separate identities. Enroll the Incus browser certificate through the portal.

## Portal access without printed passwords

The install, retry, resume and update commands report status, service URLs,
fingerprints and recovery steps. They do not return or print generated credentials.
On a host explicitly installed with `--wizard --web-enrollment`, open the HTTPS
portal, verify the CA, approve the displayed browser code with
`sudo nixlab-admin approve-enrollment`, then choose the portal password in that
browser. Existing portal accounts keep their current login.

For the default legacy path or forgotten portal access, an authorized administrator
can run `sudo nixlab-admin reset-password` from a private local/SSH/Cockpit terminal
and choose a new password. This is rotation, not retrieval. The command refuses
non-terminal input or an echoing password prompt. A successful verifier commit
retires the old setup plaintext, and old sessions are rejected. Arcane and
Linux passwords remain independent.

Private legacy service files are preserved while secure enrollment/recovery
release gates are completed. Do not delete them simply to remove output. Default
web-first enrollment and browser recovery remain gated on #63/#64/#67; transient
Arcane bootstrap and supplied-password reset remain #65/#66 work.

## Retry and source updates

For a completed installation, use `nix run --refresh github:OmarDardery/nixlab#update`
for the [guarded, one-command update](operations.md#reviewed-upgrades).

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
Stored Arcane credentials are printed only after an authenticated API login.
If that password has changed in Arcane, use the existing account or explicitly
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
```

Installation confirms only after checking the Incus API/UI, Nixlab portal,
Cockpit and enabled Arcane proxy/status/administrator initialization over TLS.
Tests of local endpoints cannot prove connectivity from every LAN/VPN client.
The startup identity service uses the assigned management address before TLS
services start. Cockpit socket activation listens independently of DHCP readiness
and restarts a failed worker automatically. The firewall restricts management access.
`nixlab-management-health.service` checks all enabled interfaces after each boot,
waits for readiness and retries on failure; inspect its journal if commissioning fails.
The server disables display-manager/X11 startup and suspend/hibernation. Wi-Fi
power saving is off when Wi-Fi provides connectivity; Bluetooth, monitoring history
and demo applications remain optional.

`doctor` returns a nonzero status on failed endpoint checks and saves a bounded
report at `/var/lib/nixlab-admin/last-health.json`, without tokens or passwords.
Transaction logs stay under `/var/lib/nixlab-admin/jobs/<id>/build.log`.
The installer prints a fixed failure message and saves a non-secret
`installation-failure.json`; transaction and rollback status remain separate. Recovery skips absent services and
NetworkManager connections that no longer exist.

Arcane recovery uses its installed upstream `admin reset-password` command in a private interactive guest terminal. It asks for a supplied password, leaves Docker workloads and persistent application data in place, and prints no generated credential. Back up `/srv/arcane` (including its independent encryption key) before recovery.

```sh
sudo nixlab-admin arcane reset-password
```

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
| Success despite broken interfaces | TLS endpoint and Arcane administrator readiness gate before confirmation |
| Password login disappeared unexpectedly | Password login enabled by default, printed policy and explicit key-only option |
| Arcane bootstrap secret disagreed with database | Private credential loading and a boolean authentication check; no plaintext retrieval helper |
| Manual Arcane recovery left it stopped | Upstream interactive supplied-password recovery without service interruption |
| TLS deprecation and confusing shutdown logs | Arcane TLS environment variables; readiness/exit status drives diagnostics |

VM tests use controlled Arcane/Incus TLS backends for the proxy/reboot boundary
and real Cockpit PAM/WebSocket and SSH password sessions before and after reboot.
Actual registry pulls, nested KVM deployment and physical-host commissioning still
need host testing.

Upstream recovery/flag references:
[Arcane recovery](https://getarcane.app/docs/users/account-recovery),
[deprecated flags](https://getarcane.app/docs/reference/environment-variables).
