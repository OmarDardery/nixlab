# Install once, configure through the web

Nixlab now includes a bootstrap installer and a self-hosted first-run/settings portal.
The supported starting point is a **fresh conventional Intel/AMD x86_64 NixOS installation**
with working IPv4 networking, `/etc/nixos/configuration.nix` and `hardware-configuration.nix`.
NixOS runs directly on the hardware; Incus uses the Linux KVM subsystem. Installing the
portal alone does not make the machine a complete hypervisor—the reviewed wizard activation does.

Custom flake-only configurations, externally linked module trees, TPM/LUKS enrollment,
root filesystem migration and creating/formatting ZFS pools require an explicit import/storage
plan. The installer fails before activation when it cannot preserve a conventional configuration.
It does not promise to convert every arbitrary existing NixOS desktop/flake automatically.

## Run on the target host

Until PR #1 is merged, use the implementation branch explicitly:

```console
curl -fsSL https://raw.githubusercontent.com/OmarDardery/nixlab/feat/declarative-incus-platform/install.sh -o install-nixlab.sh
sudo bash install-nixlab.sh --ref feat/declarative-incus-platform
```

Or use the packaged installer:

```console
sudo nix --extra-experimental-features 'nix-command flakes' run 'github:OmarDardery/nixlab/feat/declarative-incus-platform#install' -- --ref feat/declarative-incus-platform
```

After merge the default `main` ref works. The standalone launcher obtains Git, Python and
OpenSSL through the pinned Nix package set; no `nix-env` package installation is required.
The packaged installer provides its own runtime tools. Read/review the script before running it
as root. It is intended to be run on your NixOS machine, not in this ChatGPT workspace.

Optional network selection flags:

```console
sudo bash install-nixlab.sh --ref feat/declarative-incus-platform --interface enp2s0 --address CURRENT_ASSIGNED_IP --trusted-cidr YOUR_ADMIN_SUBNET
```

Without these flags the installer selects the existing default-route interface, its actual
assigned IPv4 address and its connected subnet. It never invents a physical NIC, host address,
boot device or filesystem UUID. Use explicit flags for multi-uplink/VPN/routed management.
`--address` selects an already assigned address during bootstrap; static changes happen in the UI.
The installer prints the HTTPS URL, one-time initial password and local CA fingerprint.
Keep console access for the first activation.

## What the first switch preserves

1. Saves a root-only complete copy of the original `/etc/nixos` under
   `/var/lib/nixlab-admin/original-config` and pins the original OS generation for recovery.
2. Clones the chosen repository/ref into `/var/lib/nixlab/config`.
3. Copies the actual `hardware-configuration.nix` and imports a preserved local Nix module tree
   under `installed-system/`. Keeping only the hardware file would lose bootloader settings,
   existing accounts, `system.stateVersion`, and often the management network.
4. Includes only `.nix` modules in that imported tree. Unrelated plaintext files/private keys
   are excluded from the flake source. Configurations importing additional assets/remote modules
   need an import plan; build failure leaves the old installation recoverable. Existing inline
   secrets in Nix source remain Nix source; this tool does not attempt to rewrite arbitrary Nix.
5. Creates a local CA, separate host/application TLS leaf keys and a salted password hash under
   the root-owned runtime state directory. They never enter Git or the Nix store.
6. Runs `nixos-rebuild build` and `nixos-rebuild switch` for the **bootstrap** target, which adds
   the setup/recovery services and KVM module while retaining the installed configuration.
7. Renames the original directory to `/etc/nixos.pre-nixlab` and points `/etc/nixos` at the managed
   clone. The preserved backup remains root-only. Builds use explicit flake targets.

No disks are partitioned or formatted. No VM or guest bridge is initialized by bootstrap.
If the first switch fails, the installer attempts to restore the preceding profile/generation.
Use `--resume` for an intact installation inventory after a failed bootstrap; an existing
checkout/configuration is never blindly overwritten. Lost portal passwords can be reset locally
with `sudo nixlab-admin reset-password`, followed by restarting `nixlab-ui` to revoke sessions.
The installer does not overwrite a pre-existing Incus database.

## First-run wizard

Verify the CA fingerprint against the installer output **before signing in**. The login page
provides a CA download without authentication; it is a public certificate, not a private key.
The HTTPS certificate chain also includes the local CA. In installer mode the Incus server and Portainer portal use that CA too; existing ACME configuration, if present, retains certificate ownership. Trust it on your administrator devices
only after checking the fingerprint. You can then use ordinary HTTPS for the private portal.

The portal is at `https://HOST_IP:9440` by default. Sign in with the initial password and configure:

- **Network:** preserve the installed uplink or declare a static address/gateway/DNS. Set trusted
  management CIDRs. Select a non-overlapping guest subnet, bridge address, DHCP range and apps IP.
  “Suggest an unused subnet” proposes a new private range from detected routes; review disconnected
  LAN/VPN networks too. It is a proposal, not a discovered host address.
- **Storage:** choose detected existing filesystem UUIDs and a role. Select no additional disk to
  keep the safe system-filesystem baseline. Root/boot/swap or already-mounted-elsewhere devices,
  duplicate UUIDs, mismatched filesystem types and mount paths that hide files are rejected.
- **Access:** paste an SSH public key for the `operator` account. Existing local accounts remain
  in the imported installation. The host's managed SSH policy requires key authentication.
- **Applications:** optionally enable Docker/Portainer (off by default), then choose VM CPU/RAM/disk and initial Portainer password. CPU/RAM suggestions use
  detected hardware. The optional demo also needs a database initialization password.
- **Radios:** choose Wi-Fi/Bluetooth on or off. Wi-Fi uses NetworkManager with the iwd backend.
  Existing NetworkManager profiles can remain; an old Nix wpa_supplicant profile requires entering
  its SSID/password in the wizard. WPA personal networks are supported, not enterprise 802.1X.
  Wi-Fi cannot be disabled while it remains the selected management uplink.
- **Host preferences:** host name, time zone, TRIM, Nix store optimisation, setup port and activation
  confirmation window. Updates/reboots stay explicit; there is no automatic image or volume pruning.
- **Backups/tunnel:** optionally configure the independent restic repository/credentials and an
  already-created locally managed Cloudflare Tunnel. Runtime secret inputs stay outside the source.

Click **Build & review configuration**. The server validates the schema and actual inventory,
creates an immutable source snapshot and runs real Nix evaluation/build plus `dry-activate`.
Inspect the setting diff and activation log. Then **Activate reviewed generation** runs
`nixos-rebuild switch` on that exact snapshot. Sign in again if the HTTPS service restarts.
Use the displayed new URL if you changed the IP/port, and click **Confirm access** before the
countdown expires (ten minutes by default after activation completes).

An unapplied candidate can be discarded; this stops its build worker without changing live settings. Only the active reviewed candidate can be activated. The last confirmed change remains available for one-step rollback even if a later build fails.

A persistent systemd guard restores the prior OS profile/settings if activation fails or remains
unconfirmed. It also runs after a reboot. An interrupted activation has a separate maximum activation
window before recovery. Rollback restores managed secret files, TLS leaf identity, NetworkManager
profiles and recorded radio block state as well as the prior configuration. Source drift after a
review blocks activation. Build failures never overwrite the live settings or credentials.

Rollback restores OS/configuration state, not guest/application data or Incus database migrations.
Pool creation, first guest creation and database writes can persist through a rollback. Retry after
a partial first activation preserves that attempt's pool/network identity. Moving initialized pools
or guest address space requires a deliberate migration rather than editing the wizard fields.

## Drives and tool integration

| Role | Declarative result |
|---|---|
| General data | UUID-backed `fileSystems` entry at `/srv/nixlab/NAME`; optional read-only mount |
| Primary guest storage | Same mount plus the `guests` directory pool at `MOUNT/incus` |
| Additional Incus pool | Same mount plus a `disk-NAME` pool visible in the Incus UI/CLI |
| Backup staging | Same mount plus offline export staging at `MOUNT/exports` |

Managed mounts use `nofail` and a device timeout so a missing data disk does not trap the host
in an emergency boot without its management UI. Required mounts participate in a storage-readiness
target. Incus and backups additionally use native `RequiresMountsFor`, so the relevant service
fails rather than storing guest/backup data on an unmounted fallback directory. A preseed guard
checks mount points before creating a pool subdirectory. Monitoring/journal show failures.

Incus/backup roles need writable ext4/XFS/Btrfs filesystems. FAT/exFAT/NTFS data mounts are general
file mounts, not recommended guest pools; NTFS is mapped to the kernel ntfs3 driver. Existing
filesystem ownership is preserved. Directory pools still have the quota/snapshot limitations in
the architecture guide. This wizard does not turn a filesystem into a ZFS pool or claim RAID safety.

An initialized Incus pool mount cannot be removed, rebound to another UUID or moved in this UI.
Use an export/restore or supported pool migration procedure first. Snapshot/backup data lives outside
NixOS generations. Increasing the apps disk may require a guest reboot to grow its filesystem;
shrinking/moving storage needs migration. A backup staging disk alone is not an independent backup.

## Applications without guest SSH setup

On first activation the Nix-built apps image is created through Incus. The onboarding service
waits for its native agent, transfers the reviewed NixOS closure through authenticated Incus when
needed, then switches the guest OS generation. Future wizard changes therefore also update the
existing VM's NixOS configuration; importing a new image alone would not do that.

Runtime credentials and a separate application TLS key are delivered through Incus file/exec
operations. Portainer remains stopped until its required files exist. The host portal links to:

- `https://HOST_IP:8443`: Incus Web UI. **Create browser certificate** downloads a revocable,
  password-encrypted PKCS#12 identity. Import it into your browser/OS personal certificates,
  then open Incus. The password is shown once; keep it and the identity private.
- `https://HOST_IP:9441`: Portainer through Nginx. The proxy checks the applications VM's TLS
  chain/name against the local CA. It permits trusted management CIDRs and supports WebSockets.
  The host TLS key is not shared with the VM. Log in with the Portainer bootstrap credential.

The apps VM's backend TLS port is inside the private guest network; host forwarding rules exclude
it from ordinary LAN access. Guests on that bridge are one shared application trust zone. Portainer
still has VM-root-equivalent Docker access. The portal and the Docker dashboard are separate
control planes with separate credentials. Browser identity import is a client-side trust step,
not a hand-written Nix configuration change.

Disabling the applications VM stops only the owned VM and disables its autostart; its disk and data are retained. An unowned guest named apps is left untouched while the feature is disabled. Deleting a guest remains an explicit Incus operation after backup.

Bootstrap passwords only initialize Portainer/PostgreSQL; changing those password files later does
not rotate existing database/UI accounts. Rotate inside the application with a coordinated data
backup. The setup UI does not claim otherwise.

## Ongoing use and security boundary

The UI edits `local/settings.json`; `site.nix` merges it with `site-defaults.nix`. Nix itself parses
and evaluates the configuration, including the preserved installation. Arbitrary Nix programs
are not safely reversible with regex parsing, so the web editor deliberately exposes a typed
settings model and an evaluated read-only overview instead of editing arbitrary source text.
Advanced modules, instance definitions, sops policy and secret file paths remain repository-owned.
No raw shell command, arbitrary Nix source, arbitrary file path or user-supplied flake URL endpoint exists.

The HTTPS process runs as an unprivileged `nixlab-ui` user. Its root broker accepts only defined
operations over a mode-0660 local UNIX socket. The portal has TLS, a salted PBKDF2 password hash,
rate-limited login, secure HttpOnly/SameSite cookies, idle/hard session expiration, CSRF tokens,
same-origin checks and management hostname/source checks. It is a single trusted infrastructure
administrator interface, not a multi-tenant permission system. Keep it off public tunnels.

Build/apply run in independent systemd units, so restarting the web process does not abandon the
activation or its recovery guard. Logs are visible in the UI and journal; important operations are
recorded in root-only `/var/lib/nixlab-admin/audit.jsonl`. Credential values are absent from the Nix
snapshot and job metadata. Discarded/superseded candidates release their build GC roots and staged
credentials. One-step confirmed recovery remains pinned; obsolete recovery secret copies and older
transaction roots are released after the next successful confirmation. The original installation
generation remains pinned separately for console recovery. The broker/UI are restarted after a switch to load updated runtimes.

Package refresh is a review checkbox: the Nixpkgs stable input advances inside the candidate,
its lock file is built/reviewed and only then becomes live. Repository code updates still need a
reviewed source update; this service never blindly pulls executable code from Git on a timer.
A monthly service renews local leaf certificates before expiry, reloads the Incus server identity without intentionally stopping guests, and reprovisions the VM identity.

If sops-nix is enabled by an advanced source configuration, it retains ownership of secret
provisioning; conflicting plaintext secret inputs are refused. Ordinary first-run users can use
the root-owned runtime secret files through the wizard without creating an age identity manually.

For diagnostics:

```console
journalctl -u nixlab-admin-backend -u nixlab-ui -u nixlab-rollback-guard
journalctl -u nixlab-apps-onboarding -u nginx
systemctl --failed
nixos-rebuild switch --flake /etc/nixos#hypervisor
```

For original-system recovery from a console, inspect `/var/lib/nixlab-admin/original-system`,
restore that profile and activate its `bin/switch-to-configuration switch`, then restore
`/etc/nixos.pre-nixlab` to `/etc/nixos` after removing only the managed symlink. Guest data is
retained separately. If a console/manual rollback leaves a radio blocked, `rfkill unblock wifi`
restores it; the portal's managed rollback restores its recorded radio state automatically.

## Lightweight defaults and declarative ownership

A new installation starts with Incus, its embedded web UI, the small Python setup frontend/root
broker, and a host node exporter. There is no Node.js runtime, SPA build service, Cockpit,
libvirtd, host Docker, Grafana or database for the setup portal. Browser assets are static files
served by the authenticated HTTPS process that is already needed for configuration requests.
Adding BusyBox httpd, Apache httpd or a separate static web server here would add a daemon
without removing that API process. Authentication, TLS and source checks also apply to assets.

Docker/Portainer is **opt-in** on the Applications page. Disabled means no new apps VM, Docker
engine or Portainer proxy is started on the host. An existing owned apps VM is stopped and retained.
Initial resource suggestions are capped at 2 vCPUs/2 GiB, based on detected hardware; this is a
starting allocation, not a promise that every application fits it. Increase it for real workloads.
Wi-Fi, Bluetooth, the demo, Cloudflare and backups also remain opt-in. Backup protection still
requires configuring an independent destination; the lightweight preset is not a backup policy.

Local Prometheus history is **opt-in** on Host preferences. With it disabled, host node metrics,
Incus live resource views, persistent journal and systemd service health remain available; there
is no local historical database or Prometheus alert evaluation. The apps VM node exporter is
started only with history enabled. Enable history for trend graphs/alert rules, with a default
60-second interval and 14-day retention. External alert delivery still requires a receiver.

Nginx runs only when the application portal is enabled, with one worker and 256 connections.
It provides verified upstream HTTPS, client CIDR restrictions and WebSocket forwarding. This is
why it is retained for Portainer instead of a basic static httpd. No throughput or RAM benchmark
is implied by these configuration choices. Incus, QEMU and the application workloads dominate
resource requirements; ordinary containers avoid the overhead of an additional guest kernel.

All of these choices flow through typed desired settings into native NixOS modules. Existing
installations retain their explicit applications setting; a source update does not silently
turn off an opted-in VM. The UI exports desired settings JSON without secret values for review
and backup. After activation the authoritative settings are in `local/settings.json`; back that
up together with the source revision, `flake.lock`, preserved installation modules and separate
secrets. Imported settings must be reviewed for the target machine rather than blindly restored.

Nix declares services, packages, mounts, firewall rules, radios, Compose definitions and desired
Incus resources. Native options are preferred; guarded initialization/reconciliation covers
Incus state and runtime credentials. VM disks, snapshots, Portainer-created stacks, database
contents, certificate enrollment and backup contents are mutable state, not reproducible Nix
configuration. To make an application reproducible, keep its Compose project in Git or the
Nix-owned project definitions; editing it only in Portainer does not make it declarative.
