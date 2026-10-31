# Community Scripts on Nixlab

The runner packages the upstream **Incus** backend and shared engine. Portable
container scripts already use this backend; Nixlab does not emulate `pct`, `qm`,
`/etc/pve` or a Proxmox host.

```console
sudo nixlab-helpers doctor
nixlab-helpers list
sudo nixlab-helpers run debian --project lab
sudo nixlab-helpers run adguard --project lab
```

Run from SSH or the host console. The upstream wizard selects storage, resources
and networking. The portal shows compatibility, commands and upstream links;
there is no browser execution endpoint or second application store.

`helpers.enable` installs the runner without a daemon. Both `community-scripts/core`
and `community-scripts/Incus` are pinned by commit/content hash in
`helpers/sources.json`; packaged sources retain their licenses. Updates require a
reviewed platform change.

The runner supplies host dependencies through Nix, forces Incus host detection,
uses a private client configuration and targets the local server/selected project.
It leaves administrator remotes/defaults unchanged. Preflight checks the daemon,
image service, pool capacity, networks and pinned GitHub URLs. The upstream wizard
still validates the chosen application's actual requirements.

Shared locks serialize provisioning with host mutation and exports. Finish pending
host activation first. Root-only run metadata under `/var/lib/nixlab/helpers`
records project, source revisions and exit status. Upstream logs may contain
credentials; review before sharing them.

Both source origins are propagated into guest installation at the pinned commits.
Application installers may download current releases, and later upstream updates
remain mutable. Inspect actual versions inside the guest. NixOS host rollback does
not undo those applications; include application-consistent backups.

## Compatibility

- **Verified on Nixlab:** install, health, restart/persistence and restore evidence
  for the exact host/engine/script tuple has been recorded.
- **Upstream-supported; unverified here:** a packaged Incus container script exists
  but has not completed Nixlab qualification.
- **Unsupported:** PVE host tooling, PVE-specific VM scripts, missing scripts or
  requirements absent from the Incus backend.

Debian, AdGuard Home and Vaultwarden are qualification targets. The manifest keeps
them unverified until live evidence exists. Other packaged `ct/` entries are listed
with the same unverified label. No application is installed by default. The runner
currently accepts container scripts only; use upstream Incus VM implementations
separately.

For ordinary LAN addresses/discovery, prepare a host LAN bridge in Connectivity
and select it in the upstream wizard. Private NAT does not automatically publish
DNS/web interfaces or multicast discovery. Manage the resulting guest in Incus.
For AdGuard, verify TCP and UDP port 53 reachability from intended DNS clients,
plus the web setup/management port selected in AdGuard. The initial setup commonly
uses port 3000; confirm actual ports inside the guest. Back up its configuration and
data, then verify DNS, management access and persistence after reboot and restore.
