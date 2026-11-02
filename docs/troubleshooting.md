# Troubleshooting

Start with the symptom. Keep the original error. Resist the traditional homelab
ritual of changing six things and discovering none of them was the problem.

## Start here: collect the useful evidence

```sh
sudo nixlab doctor
systemctl --failed
```

`doctor` checks the actual management address and enabled HTTPS interfaces,
including Portainer administrator readiness. Its concise report is saved at
`/var/lib/nixlab-admin/last-health.json`.

The installer reports its original failure separately from rollback failures.
Its report is `/var/lib/nixlab-admin/installation-failure.json`; detailed build
logs are under `/var/lib/nixlab-admin/jobs/<transaction-id>/build.log`.
Review logs before sharing them; do not publish passwords, tokens or private keys.

## Every management check times out against the host's own address

Read the saved result before running another check:

```sh
sudo cat /var/lib/nixlab-admin/last-health.json
sudo journalctl -b -u nixlab-management-health.service --no-pager -n 60
```

An earlier firewall configuration forced `trustedInterfaces = [ ]`, removing
NixOS's loopback allowance. Connections from the host to its own LAN address
arrive through `lo`; all six HTTPS probes could time out even with listeners
running. Current Nixlab trusts only `lo`, while retaining the LAN source/interface
restrictions and guest isolation. Update with the installation command after the
fix is available. Do not disable the firewall or skip health checks.

If the problem persists, check `ip -4 address`, `ip -4 route get HOST_IP` and
`sudo nft list ruleset`. Missing addresses, custom firewall rules and service
failures still need their own diagnosis. A failed activation rolls back, so
listener checks after rollback describe the restored system.

## Cockpit works only after a manual restart

First collect the boot evidence, before restarting anything:

```sh
sudo systemctl status cockpit.socket cockpit.service nixlab-management-identity.service
sudo journalctl -b -u cockpit.socket -u cockpit.service -u nixlab-management-identity.service --no-pager
sudo systemctl status nixlab-management-health.service
sudo journalctl -b -u nixlab-management-health.service --no-pager
ip -4 address
```

Current Nixlab listens on `0.0.0.0` for Cockpit, rather than binding the socket to
a DHCP address that may not exist yet. The activated service waits for the
management TLS identity and exact WebSocket origins. Failed Cockpit workers retry;
the boot health unit checks enabled interfaces and retries readiness failures.

If the journal still shows Cockpit binding to an old address such as
`192.168.1.9`, check the active configuration and any local overrides:

```sh
systemctl cat cockpit.socket
systemctl cat cockpit.service
```

Update using the installation command in the [install guide](plain-nixos.md),
then verify `doctor`, login and the Cockpit terminal after a reboot. Do not add
network-online dependencies to `cockpit.socket`: early socket ordering can create
a boot cycle. The service waits for identity preparation instead.

An inactive `cockpit.service` after an idle timeout can be normal: the socket
starts it on demand. A failed socket, failed requests or an unusable terminal is not.

## The URL says .9, but I wanted .240

The installer preserves working addressing. Keeping DHCP does not assign a static
address, regardless of how confidently it was written in a plan.

```sh
ip -4 address
ip -4 route
sudo nixlab doctor
```

Use the actual address in the installer output. Reserve your preferred address
in the router, or make a reviewed static-network change in Nixlab with console
access available. Do not copy example NIC names or guest addresses into your host.

Default interfaces use `https://HOST_IP:9440` (Nixlab), `:8443` (Incus), `:9090`
(Cockpit) and `:9443` (Portainer). Older installations can retain Portainer on
`:9441`; use the reported/configured port.

## SSH or Cockpit rejects my password

SSH and Cockpit use **Linux accounts**, not the Nixlab portal password and not the
Portainer password. Root SSH and root Cockpit login are disabled. Try your existing
non-root administrator account or the verified `operator` credential printed by
the installer.

```sh
ssh operator@HOST_IP
```

Password SSH is enabled by default. If you explicitly selected key-only access,
enroll your **laptop's public key** through a local host terminal:

```sh
sudo nixlab enroll-key /path/to/laptop.pub
```

A key generated on the server does not authorize your laptop. Check **Access**
for the password-login policy. A locked Linux account still needs a usable password;
enabling password authentication alone does not create one.

## Portainer's saved password does not work

An initialization password file cannot override an account already stored in the
Portainer database. Nixlab only presents stored credentials as current after an
authenticated API login. If you changed the password, use that account or run:

```sh
sudo nixlab portainer reset-password
```

The recovery command downloads the official helper before stopping Portainer,
backs up its data, resets credentials, synchronizes the runtime files, restarts
Portainer and verifies authentication before printing the result. It reports the
backup path. This is an explicit recovery operation; it does not delete stacks.

If recovery fails, keep its error and backup path and check:

```sh
sudo homelab exec apps -- systemctl status docker-portainer.service
sudo homelab exec apps -- journalctl -b -u docker-portainer.service --no-pager
sudo nixlab doctor
```

Do not delete `/srv/portainer` to make a login screen look new again.

## Installation stopped halfway through

Run the **same command** again:

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/nixlab#install
```

Active transactions are reattached; interrupted recovery is completed before a
new attempt. Credentials survive failed activation. You do not normally need
`--resume`, a fresh clone or an improvised database cleanup.

If the installer reports locally edited platform files, it preserves them and
skips the source update. Inspect the listed edits; move intended configuration
into `machine/modules/` before reviewing an update. Use `--no-update` only when
you deliberately want to retain the current platform source.

## An existing Incus database blocks installation

Select the [adoption workflow](plain-nixos.md#adopt-an-existing-incus-host).
The installer inventories the host and backs up guests and custom volumes before
using a separate Nixlab bridge/pool. Interactive runs ask; unattended runs need
`--adopt-incus`.

Existing clusters and an unrelated `lab` project require an explicit migration
plan. Never delete the Incus database to get past this check. It is not a cache.

## Incus works, but another interface is broken

Incus being reachable does not prove Cockpit, Nixlab or Portainer is ready.

```sh
sudo nixlab doctor
sudo systemctl status nixlab-ui.service nginx.service nixlab-apps-onboarding.service
```

For Portainer, check the apps VM and its agent:

```sh
sudo incus list --all-projects
sudo homelab exec apps -- systemctl --failed
```

Also check the client is on a trusted management subnet, the current host address
and any customized ports. Management interfaces are private; this documentation
domain does not forward requests to them.

## The browser does not trust the certificate

Private management uses a locally generated CA. Verify its fingerprint against
the host console before trusting that CA on a client. Use the detected address
from installation; an old DHCP address can also cause a hostname mismatch.

The public documentation site uses GitHub Pages HTTPS and is a separate service.
See [website hosting](website.md) for public-domain certificate and DNS problems.

## Rollback failed too

Treat the original activation error and `rollbackError` as separate failures.
Keep both reports and use a local console. Current recovery skips absent services
and NetworkManager UUIDs that no longer exist, but OS rollback cannot restore
Incus schema or guest database contents. Follow the [recovery guide](recovery.md)
before attempting manual state changes.
