# Operating the single-host release

Boot the ISO, connect to the Internet and run `sudo nixlab-setup`. Use the
[ISO guide](iso.md) for the destructive-disk confirmation and manual alternatives.
First boot starts the authenticated setup portal; the first reviewed activation
provisions Incus. The [commissioning checklist](commissioning.md) is the remaining
target-machine acceptance work.

## Daily work

The portal's Operations & recovery page loads guest inventory on demand. It shows
state, addresses, limits, declared-field ownership/drift, service health, certificate
expiry and backup success. Long actions run as systemd jobs and remain independent
of the browser. Refresh retrieves results; active jobs are polled while that page is open.

Start/stop/restart use fixed local Incus commands; stop and export use graceful
shutdown without force. Snapshots stay on the same storage and are not independent
backups. Instance exports exclude custom-volume contents. The configured full restic
backup exports custom volumes too and includes configuration and runtime identities.
Application-consistency hooks remain application-specific; the demonstration stack
has a PostgreSQL dump hook.

```sh
sudo nixlab-admin guests list
sudo nixlab-admin guests snapshot --name myguest --snapshot before-update
sudo nixlab-admin guests export --name myguest --acknowledge
sudo nixlab-admin guests status
sudo nixlab-admin guests backup --acknowledge
```

The scheduled backup and guest jobs serialize exports through the same Incus lock.
Configuration publication/backup preparation also use the configuration lock. Avoid
concurrent manual Incus changes during a backup. A durable restart record recovers
guests stopped by interrupted exports; the recovery unit is available even when
scheduled backups are disabled. Check health for guests still awaiting restart.

On-demand exports use the selected backup staging directory, in `on-demand/`, rather
than the portal's transaction directory. A selected backup filesystem must be mounted
with its expected UUID; a missing disk never silently redirects an export onto root.
The default staging directory shares the system filesystem: provision enough space
or select an independent staging disk before exporting large guests. Exports are not
automatically deleted; manage retention after verifying an independent copy.

## Restore and recover off-host archives

Select a completed export and a **new** instance name, or copy an existing snapshot
to a new name. Imports never overwrite a live instance. Copies are created stopped,
with autostart disabled and a restore-review marker. Our start action refuses them
until an administrator releases the reviewed copy. Fixed-address conflicts and writable data-volume sharing with another instance block
release. Inspect NICs, guest configuration, shared volumes and credentials in Incus
before release; custom volumes require their own restore. The full Incus interface
retains administrator authority, including the ability to override these safeguards.

Recover a full backup with the native restic client into an isolated directory.
The backup inventory records profiles, networks, projects, pools, server configuration,
trusted certificate records and archive SHA256 values. Check that inventory and restore
required profiles/projects/custom volumes before
starting a guest. Register a recovered Incus instance archive locally:

```sh
sudo nixlab-admin guests register-export --file /root/restored/instance-export.tar.gz --project lab --name original-name
sudo nixlab-admin guests status
sudo nixlab-admin guests restore --name original-name --archive EXPORT_ID --target restore-test --acknowledge
```

Registration copies and hashes the file into managed staging; it does not import or
modify the original guest. The original instance may be absent. Restore verifies the
recorded SHA256 before import. A browser cannot submit an arbitrary host file path.
Native `incus storage volume import` handles separately recovered custom volumes.

Keep original declarations disabled during disaster recovery: otherwise rebuilding can
create empty replacement guests. Restore into new names, inspect data, and explicitly
adopt/rename them before re-enabling their declarations. For the reserved applications
VM, leave its feature disabled, restore to a temporary name, then use native Incus
administration to establish the intended `apps` instance before enabling onboarding.
Check disk size, pool and network mappings before claiming ownership. Never delete
the only good copy to make a name available.

## File configuration and recipes

For a new data disk, `sudo nixlab-admin disk prepare` provides a local/SSH console
workflow: choose an inactive, unmounted disk, review its identity, type the erase
phrase, and prepare one ext4 data filesystem. It writes a UUID-based Nix proposal
and prints the guarded review command. It does not activate a mount or pool. Active
LVM/RAID/ZFS members and mounted system/live disks are excluded. Existing filesystems
need no formatting: use the form or a file module directly.

Settings forms and uploaded modules use the same immutable transaction:
**stage → build/review → activate → verify → confirm**. The default confirmation
window is ten minutes after activation completes. Direct rebuilds bypass this portal
recovery transaction. See [file modules](file-modules.md) for exact options and CLI.

The packaged recipe catalog is discovered from `admin/recipes/*.json`. Each entry
contains structured settings and/or a reviewed Nix module, not a shell installer.
Stage a recipe, supply any initial service credentials, then build normally. Adding
a recipe file requires no registry edit. The initial catalog includes laptop lid
policy, SMART health, private Tailscale SSH, Prometheus history, the applications VM
and the demonstration web/database/cache stack. New optional services stay off until
explicitly activated. Runtime packages follow the lock file; OCI image tags still
need an immutable digest when registry-content pinning is required.

The laptop recipe takes effect after reboot because the native logind module keeps
existing login sessions alive during a switch. Guided fresh installation declares
that policy in the initial OS. For private access, enroll Tailscale with
`sudo tailscale up`, then use key-authenticated SSH to its private address. Forward
`9440:127.0.0.1:9440` and use `https://localhost:9440` with the verified local CA.
Use the configured portal port if different. This recipe opens only private SSH;
it does not publish management dashboards or route the guest network.

## Reviewed upgrades

The Review page accepts a full upstream Git commit SHA. Reauthenticate and acknowledge
its privileged code, then build. The worker fetches exactly that commit from the
Nixlab repository in isolation, preserves machine settings/modules/hardware, records
changed file hashes and builds an immutable closure. Pinned-input changes and a
kernel-reboot indicator are displayed with the existing dry-activation output.
Review the linked commit's code before activating.

```sh
sudo nixlab-admin config review --revision FULL_40_CHARACTER_COMMIT_SHA
sudo nixlab-admin config status
sudo nixlab-admin config apply TRANSACTION_ID
# Verify access and services before confirming:
sudo nixlab-admin config confirm TRANSACTION_ID
```

Publication records old and new platform paths before writing, so interrupted updates
can remove partial additions and restore the previous source as well as OS/settings.
Local machine state never becomes upstream platform code. Personal changes belong
under `machine/`; reviewed upgrades replace tracked platform code. A separate package
refresh updates Nixpkgs inside the same candidate. Updates never force a reboot.

The ISO's Git snapshot needs no manually configured tracking branch for this path.
Its upstream revision is recorded in `platform-release.json`. Private repositories
require a root-owned Git credential helper configured before the background fetch;
do not put tokens in the SHA field, Nix source or module uploads. A failed fetch/build
does not publish source or change the active generation.

## Accounts and trust boundaries

| Portal role | Authority |
|---|---|
| Administrator | Host configuration/code, account management, recovery export and all supported guest actions |
| Operator | Start/stop/restart, snapshots and exports in `lab`; no host/code changes or recovery material |
| Viewer | Read status/inventory and guides; no guest mutations or configuration changes |

Only administrators manage accounts, with password reauthentication. Password/role
changes and deletion revoke affected sessions on their next request. The reserved
`admin` recovery account cannot be deleted. Portal role checks are enforced by the
HTTPS handler, not just disabled buttons. The root broker is accessible only through
the local root/UI UNIX socket; requests and jobs carry audit identities.

These roles do not change SSH/sudo, native Incus certificates or Portainer permissions.
The `operator` SSH account is a full infrastructure administrator. An administrator
Incus browser certificate is separately powerful. Do not give either to a portal-only
viewer/operator. This is not a multi-tenant isolation or SSO system.

## Portable recovery and diagnostics

Download a configuration kit from Operations & recovery. Supply an age **public**
recipient to include a separate encrypted secret archive. The browser downloads both
inside one outer recovery tar to avoid multiple-download blocking; extract that outer
container first. Reauthentication is required.
The configuration kit contains source, lock, machine files and a checksum manifest;
it excludes runtime private keys/password files. Inline secrets manually written in
Nix source remain source and cannot be automatically sanitized.

```sh
sudo nixlab-admin recovery export /root/nixlab-kit --include-secrets --recipient age1YOUR_PUBLIC_RECIPIENT
sudo nixlab-admin recovery inspect /root/nixlab-kit/nixlab-config.tar.gz
sudo nixlab-admin recovery stage /root/nixlab-kit/nixlab-config.tar.gz /root/recovery-review
```

Staging validates member types, paths, limits and hashes before extracting into a new
root-only directory. It never activates a system or overwrites `/etc/nixos`. Decrypt
the secret archive separately with your off-host age identity, inspect it, then stage
it into another new directory. It includes regular runtime service secret files, portal identities,
local CA/leaf keys, NetworkManager profiles and the configured sops age key when present. Protect external/HSM identities and
symlink-based secret stores separately; SOPS recovery needs both encrypted source and
a usable decryption identity.

On replacement hardware, install/bootstrap normally first. Keep its newly generated
hardware/boot modules. Review recovered settings and module files against the new
NICs, disk UUIDs and storage before submitting them through the guarded transaction.
Restore selected runtime credentials with root-only permissions; the portal password
record needs group `nixlab-ui` and mode 0640. Preserve CA identity only when intended.
Guest/application data comes from its independent backups, not this kit.

Scheduled backups create root-only configuration and secret archives in staging, then
restic encrypts them for off-host storage. Those staging archives are **not encrypted
at rest** unless their filesystem is encrypted. Protect the staging disk accordingly.

The diagnostic download contains release identifiers, fixed service states, certificate
expiry and backup status. It excludes arbitrary journals, build logs and secret values.
Guides are packaged and served locally under `/docs/`, so installed documentation does
not depend on the implementation branch remaining available.

## Optional failure notifications

Upload a module setting `nixlab.notifications.enable = true`. Store an HTTPS webhook
URL in the root-owned runtime file `/var/lib/nixlab-secrets/notification-url` (mode 0600),
or select another runtime file through `nixlab.notifications.webhookFile`. Failed KVM,
instance, backup and rollback units trigger bounded notification jobs. The payload
contains service identity and failure state; URLs, responses, journals and secrets
are withheld. Redirects are refused. No resident notification daemon is added.
