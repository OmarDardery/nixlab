# Administration

## SSH and Incus CLI

The `operator` account accepts declared SSH keys only. It may use sudo for infrastructure
administration. Neither ordinary users nor `operator` receive an unrestricted Incus/Docker
socket group automatically. Sudo-all is intentional for the trusted infrastructure operator;
use separate accounts/project-scoped Incus identities for less-trusted users.

```console
ssh operator@HOST_IP
sudo homelab list
sudo homelab exec apps -- systemctl --failed
sudo homelab exec apps
sudo homelab exec-many apps,adguard -- df -h
sudo homelab action apps disk
sudo homelab restart apps
```

`exec` without a command opens `/bin/sh`. With a command it forwards argument vectors,
stdout/stderr and the actual guest exit status. `exec-many` runs sequentially and returns
nonzero if any command fails. Only the explicit `action` whitelist supplies predefined commands.
No unauthenticated command execution server is installed.

For a trusted client, install the packaged helper with this flake's `packages.x86_64-linux.homelab`
or add the derivation to a Nix/Home Manager package list. A non-Nix client can use native `incus`
directly. Enroll a separate client certificate rather than sharing the host administrator identity:

```console
# On host; generated token is sensitive and short-lived, do not put in Git or chat logs.
sudo incus config trust add laptop
# On trusted client, use the token or endpoint as prompted; verify the server fingerprint.
incus remote add labserver https://HOST_IP:8443
incus --project lab list labserver:
incus --project lab exec labserver:apps -- uname -a
```

Confirm the displayed server TLS fingerprint through the physical console or independent
trusted SSH connection before accepting it. Tokens bootstrap certificate trust; normal operations
use authenticated TLS. An unrestricted certificate is host-root-equivalent. Use Incus restricted
project certificates/authorization for delegated users; review the version's supported controls.
Revoke a lost client with `incus config trust list` / `incus config trust remove FINGERPRINT`.

Default `/etc/nixlab/client.json` targets the local host and project `lab`. On a client, set
`HOMELAB_CONFIG` to a JSON file such as:

```json
{
  "remote": "labserver",
  "project": "lab",
  "appsInstance": "apps",
  "exportDirectory": "/home/USER/nixlab-exports",
  "aliases": { "100": "adguard", "105": "klipper" }
}
```

Those numeric aliases are examples, not migrated instances. Remote options also work directly:
`homelab --remote labserver --project lab list`.
Audit records include uid, action, target, duration and exit code through syslog. They deliberately
exclude arbitrary command arguments/output that might contain secrets. These records are not an
immutable privileged-session recorder; retain central logs if tamper resistance is required.
Native Incus/UI actions bypass this helper; Incus lifecycle events and the host journal complement it.

## Incus Web UI

Browse `https://HOST_IP:8443` only from a trusted management source. Incus generates a server
TLS key/certificate under `/var/lib/incus`; preserve it securely or re-verify fingerprints after
reinstallation. Certificate rotation must be planned. Optionally configure the native
`virtualisation.incus.useACMEHost` with a real DNS/ACME certificate; no public management port is needed.

Enroll the browser's client certificate using the UI enrollment flow or the installed client's
`incus webui` helper. For a browser without that helper, create a dedicated certificate/key pair,
register its certificate through authenticated host SSH (`incus config trust add-certificate`),
export a password-protected PKCS#12 bundle and import it in the browser. Keep the private key
and bundle out of Git. Refer to the exact installed Incus version's trust subcommand help.
The server certificate and the client certificate have different purposes; trusting a server
certificate does not authorize a browser as an administrator.

Use Incus for containers, VMs, image templates, limits, devices, volumes, networks and snapshots.
For a UI-created guest, runtime state belongs to Incus until deliberately adopted. Adoption
requires reviewing its full configuration and explicitly setting `user.nixlab.managed=true`;
the reconciler refuses a name collision without that marker. A marker is authorization, not
an ownership proof against someone who already has root access.

Create a container from an image by adding a `site.instances` declaration with `name`, `project`,
`image`, `type`, `profiles`, `config`, `devices`. Use a full immutable image fingerprint for
repeatable initial root filesystems. Floating `images:debian/13`-style aliases track a moving image.
Images are templates; they do not restore current guest data. `base` provides resource limits,
root disk/NIC and daily snapshots with seven-day expiry. Guest-specific device definitions
must contain complete replacement devices, not partial profile patches.

Example shape (replace fingerprint and reserved IP before enabling):

```nix
instances = [ {
  name = "utility"; project = "lab"; type = "container";
  image = "images:FULL-IMAGE-FINGERPRINT";
  profiles = [ "base" ];
  config = { "limits.cpu" = "2"; "limits.memory" = "1GiB"; };
  devices.eth0 = {
    type = "nic"; name = "eth0"; network = "incusbr0";
    "ipv4.address" = "CHOSEN-GUEST-IP";
  };
} ];
```

Unprivileged Linux containers are the default. NixOS system containers additionally need the
supported NixOS image and `security.nesting=true`; do not substitute a privileged container.
Full VMs need a bootable VM image and KVM. Resizing disks upward and changing limits is supported;
shrinking disks requires a separate migration/recovery procedure. Deleting a declaration never
means deleting data. Explicit deletion uses authenticated `incus delete` after a reviewed backup.

## Portainer and Compose

The UI is HTTPS on **apps VM loopback**, not host loopback. Forward it from a client:

```console
ssh -N -J operator@HOST_IP -L 9443:127.0.0.1:9443 root@APPS_IP
```

Open `https://localhost:9443`, independently verify the VM's Portainer self-signed certificate,
and log in as the bootstrapped admin. HTTPS is protected inside SSH; no public admin hostname
is configured. Portainer's Docker socket gives it root-equivalent control of the VM, not of the
hypervisor. Compromise of a VM can still expose its applications/secrets; it is not a safe place
for mutually hostile tenants.

For the included demo, enable `site.apps.enableDemo`, provision the database secret, deploy the
updated guest configuration and use:

```console
sudo homelab deploy demo
sudo homelab logs demo
sudo homelab update demo
sudo homelab project-restart demo
sudo homelab down demo
sudo homelab exec apps -- nixlab-project backup demo
sudo homelab exec apps -- nixlab-project lock demo
```

Deploy validates Compose and waits for health. The image itself defines the demo HTTP healthcheck;
PostgreSQL/Redis have Compose healthchecks. Update explicitly pulls registry-backed services and
keeps the locally Nix-built app image. Teardown retains volumes. The helper lock action writes
image-only digest overrides without resolved environment secrets. Review and commit overrides;
copy/deploy them into the corresponding `/srv/compose/<project>` directory. Replace the Portainer
image reference with a reviewed immutable digest in `site.nix` too.

The web app stores a visit count in PostgreSQL and uses Redis as a disposable cache. `/health`
checks both dependencies without adding visits. It is a commissioning demo, not an authenticated
production application. Do not publish its unauthenticated write endpoint as a real service.
Docker/OCI tags are mutable; use tested digests and review security updates regularly.
Changing a database password file alone does not rotate the password in an existing PG volume.
Perform coordinated SQL/application rotation, take a backup and test reconnects.

Additional CLI-owned projects live under `/srv/compose/NAME/compose.yaml`, preferably from a
reviewed Git revision with runtime file secrets. UI-owned projects should instead use Portainer's
Stacks > Git Repository or editor workflow. Keep one writer per project. CLI-owned external
stacks have limited Portainer lifecycle/editing support; use the packaged helper for them.
No additional Dockge/Komodo dashboard competes for control.

## Logs and monitoring

```console
journalctl -u incus -u incus-preseed -u nixlab-instances --since today
journalctl -u nixlab-incus-audit --since today
journalctl -t nixlab
systemctl --failed
sudo incus info apps --project lab --show-log
sudo homelab logs demo
sudo nft list ruleset
sudo incus network show incusbr0
sudo incus storage show guests
```

Forward host TCP 9090 over SSH to inspect Prometheus (`ssh -L 9090:127.0.0.1:9090 operator@HOST_IP`).
Only loopback exposes Prometheus and the host node exporter. Incus metrics are fetched through
its authenticated local UNIX socket into node-exporter textfiles. The VM node exporter is reached
by the host bridge IP; other guest monitoring requires enabling/configuring an exporter in that guest.
Incus UI/metrics supply per-instance CPU, memory, disk and network counters; node exporter supplies
host and VM OS metrics. Journal storage is persistent and size/retention bounded.

Prometheus alert rules cover missing targets, low disk space, failed units and stale backups.
There is no configured external Alertmanager receiver: alerts/local failure logs are not a promise
of a phone/email notification. Add your real receiver and runtime credentials before relying on
unattended alert delivery. Logs are not shipped to a remote collector by default.
