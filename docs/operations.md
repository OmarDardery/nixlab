# Operating the single-host release

The Backups page distinguishes export/off-host backup status from repository
verification. Its repository result includes actual metadata/full-read scope,
schedule, interrupted/failed/unknown states and clock warnings. Changing the target
requires new evidence; a previous repository's success does not carry over. See
[repository verification](recovery.md#scheduled-repository-verification) for the
existing service, typed Nix policy and recovery limits. A successful integrity
check is separate from a tested guest/application restore.

Install from an existing NixOS system using the [single command](plain-nixos.md).
The installer configures and verifies Incus, Cockpit and the Portainer VM, preserves
installed data and resumes interrupted work. The [commissioning checklist](commissioning.md)
covers target-machine acceptance.

## Daily work

### Host console

Bash includes ble.sh suggestions/highlighting, Tab completion, fzf history search
with **Ctrl+R** and file search with **Ctrl+T**, and a compact Starship prompt.
The prompt always identifies the user and host, highlights root, and shows Git/Nix
context without remote lookups. Existing account shells and personal shell files
are preserved; native NixOS program options remain overridable.

Interactive login runs `nixlab-fetch`, using upstream's minimal Fastfetch build
without desktop/audio/image integrations. It shows hardware/resource information plus
NixLab's observed management address and enabled service URLs, service states,
backup-service result and test-activation/kernel notices. `nixlab-status` prints
only the operational summary. These are bounded local observations, not endpoint
health or successful restore evidence. They do not authenticate, contact registries
or request sudo. Missing observations stay unknown.

`nixlab-update` runs the normal guarded main update and forwards its options;
`failed` lists failed services.
Noninteractive shells print no welcome screen. `.hushlogin` or an inherited
`NIXLAB_NO_FETCH=1` disables the login summary. Personal Starship configuration is
respected. The banner uses ordinary characters, so a special terminal font is not
required. Bash history ignores space-prefixed commands; avoid entering credentials
on a command line.

### Maintenance

If status reports **Worker state unavailable**, retry after checking systemd on
the host console. This means the observation failed, not that the transaction
failed. NixLab preserves the job record and recovery intent rather than allowing a
competing operation based on that unknown result. The rollback guard remains
independent. Do not delete job or pending-state files to clear this error.

The Backups page coordinates independent backups, repository checks,
archive restores, recovery downloads and host health. Use Incus for ordinary guest
lifecycle, consoles and snapshots. Legacy commands below remain for compatibility.

For maintenance, inspect system generations with `sudo nix-env --list-generations
--profile /nix/var/nix/profiles/system` and GC roots with `nix-store --gc --print-roots`.
After reviewing boot and recovery needs, `sudo nix-collect-garbage --delete-older-than 30d`
removes old profile generations and unreferenced store paths; it changes boot-menu
history. Active system and Nixlab's explicit recovery roots remain protected.
`sudo nixlab-admin prune-jobs` previews old terminal configuration jobs beyond the
newest 20; add `--apply` to remove them, or `--keep N` to choose the retained count.
Active, pending and last-confirmed jobs are protected. Guest-operation history and
export archives are separate and are not pruned by this command.

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

From the console, open **Changes → Review latest main**, inspect the selected
revision, reauthenticate and acknowledge its privileged code, then build, review,
activate and confirm access. Selection resolves main to one fixed SHA; it does
not silently follow later commits or bypass the existing impact/rollback gates.

For an existing NixLab server, run this in its interactive terminal:

```sh
nix run --refresh github:OmarDardery/nixlab#update
```

For a broken existing Ethernet/bridge management connection, run **at the host's
local console**:

```sh
nix run --refresh github:OmarDardery/nixlab#update -- --repair-network
```

It follows the saved port/controller relationship, previews the exact existing
profiles, asks before enabling autoconnect, activates the ports and bridge, and
selects their observed address within your existing trusted networks. If discovery
is ambiguous, add `--interface YOUR_BRIDGE`. It creates no bridge/profile, assigns
no new address and preserves gateway, DNS, addressing mode and ownership. Managed
static networking, missing hardware, guest networks and ambiguous profiles require
review rather than guesses. The launcher needs network access to fetch NixLab.

A temporary systemd identity selection validates the observed address and generates
the usual TLS identity so the existing Incus daemon can start. It changes no
firewall rules or authentication and never initializes an empty replacement Incus
database. Normal update review then observes the real guests and storage. Existing
source-defined profiles with autoconnect disabled receive a small, reviewable
machine module; native profiles remain owned by NetworkManager. A stronger custom
Nix override blocks activation rather than being silently overridden.

The temporary identity selection is removed, the native identity service restarted,
and autoconnect checked before health/access confirmation. The normal activation
and rollback guard remain authoritative. Failed/cancelled preflight restores the
previous autoconnect flags; the existing connection may remain active. Interrupted
work retains `/var/lib/nixlab-admin/network-repair.json`; rerun the same command.
Reboot persistence still requires an actual reboot check after confirmation.
If an interrupted repair rebooted without connectivity, its pinned launcher can
run the same preflight without downloading first:
`sudo /var/lib/nixlab-admin/worker-runtime/bin/nixlab-update --repair-network`.

When Incus is already available and only the selected management interface needs
correction, `#update -- --interface YOUR_INTERFACE` reviews the observed address
and prefix without modifying native profiles. Existing source-owned management
overrides must agree with the built selection.

The command requests sudo itself, resolves main once to a full commit SHA and
builds through the existing transaction engine. It displays the build/impact
review and asks before activation. After health checks it prints the verified service URLs; open another SSH
connection and check the portal/workloads, then answer the confirmation prompt
before its deadline. No transaction IDs need copying. Rejection or failed health
starts rollback; interruption leaves the existing rollback guard active. Run the
same command to resume its own unfinished update at its original pinned revision.
Unrelated configuration jobs and unfinished recovery block a new update. An
already-current source is a no-op. The command does not reboot the host. Existing
Incus reconciliation and application onboarding still follow the enabled
configuration; inspect their effects and review any guest rollout separately.
Retain independent backups and schedule any kernel reboot.

`--refresh` refreshes Nix's cached launcher. The updater independently resolves
main, so the candidate stays fixed even if main advances while it builds.

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

The packaged source's Git snapshot needs no manually configured tracking branch for this path.
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

Scheduled backups create root-only configuration and secret archives inside each complete generation, then
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

## Generation layout and coverage

A run stages exports and recovery kits under `.incomplete/<id>`, then atomically
publishes `generations/<id>` and updates `latest.json`. Restic excludes incomplete
data. Two complete local generations are retained; legacy flat files are not
moved/deleted automatically. The inventory and checksums inside each generation
are authoritative. Recovery kit failure also prevents publication.

Guest groups stop/export/recover in turn. Shared writable custom volumes join all
consumers, including projects sharing the default volume namespace. Additional
`backup.groups` contain lists of `project/guest` names. Host writers/external data
still need their own consistency procedures.

`backup.exclude` and Incus `user.nixlab.backup=manual` protect whole consistency
groups from automatic shutdown. Explicit exclusion names must match existing
guests; a typo refuses shutdown. The generation and portal record exclusions and
selected coverage. Off-host upload can succeed for that selection, but the global
full-host freshness indicator is not advanced. Investigate missing coverage.

Run an independent repository check from the portal or
`sudo nixlab-admin guests backup-check`. `sudo systemctl stop restic-backups-nixlab`
cancels a scheduled backup; signal handling and the cleanup/recovery unit restore
original guest running states. Inspect pending restart intent before retrying.
The portal tracks the actual service through export, off-host transfer and retention,
keeps cancellation available during transfer, and reports an off-host failure even
when the local generation completed successfully.
The default calendar is `*-*-* 03:00:00`; change `backup.schedule` through review.

## Host clock readiness

Doctor and the portal health view report synchronized, plausible but unsynchronized,
or invalid host time. Plausibility is deliberately coarse (2026 through 2099);
it cannot establish correct UTC. Unavailable synchronization evidence is a warning,
not proof of synchronization. Check the host date, native time service, DNS and
network connectivity before relying on backup ages or qualification timestamps.

Only a grossly invalid clock blocks creation of a new Incus browser certificate.
A transient NTP outage does not block host boot, existing sessions or ordinary
Incus operations. Correct the clock and refresh the health view; no additional
background service is installed.
