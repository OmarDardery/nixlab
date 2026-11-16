# Administration

## Native service sessions

NixLab, Incus, Cockpit and Arcane authenticate separately. The optional Arcane
TLS proxy uses the upstream [Nginx njs module](https://nginx.org/en/docs/njs/) to
remove `nixlab_session` from requests to the guest and from guest responses.
Browsers share cookies across ports, so the proxy must enforce this boundary.
Arcane's own cookies, cookie security attributes and JWT headers are preserved.
This module runs inside Nginx; it adds no background service. A custom replacement
proxy must preserve the same isolation.

## Commands, docs and community resources

Open **Help** in the Nixlab host console for copyable platform updates,
package-refresh reviews, health checks, guest/Docker examples and community helper
commands. Each example says where to run it. Copying never executes a command or
creates a guest from the browser. Application wizards run interactively in the host
terminal; Docker examples target the apps VM.

The same section groups Nixlab guides, upstream documentation and community resources
by project. Incus helpers, Cockpit plugins, Docker Compose examples and Arcane
templates are labeled separately. Optional backup, secret, ingress and monitoring
references are under the expanded resources list. Community compatibility and
Nixlab qualification remain separate.

## Credential delivery during installation and recovery

Installer summaries show URLs, public fingerprints, status and recovery steps.
Until #65/#66 qualify the replacement, a verified private Arcane bootstrap
input still has its protected path reported; no credential value is returned.
They do not print credentials or offer a password-retrieval API. Arcane health
checks consume private service credentials and return only a boolean. Its reset command invokes upstream interactive recovery at a private terminal; it does not generate a replacement password or retain reset output. Bootstrap-file lifetime remains tracked separately.

Console-approved browser enrollment is explicitly enabled with
`--wizard --web-enrollment`; the default legacy path is not silently migrated.
An existing or forgotten portal account can be rotated with
`sudo nixlab-admin reset-password` from an authorized private host terminal. It
refuses non-terminal/echoing input, accepts a supplied password and removes the
old setup plaintext only after committing its replacement verifier. Existing
sessions fail their next identity check; no UI restart is necessary. Do not put
passwords in command arguments, transcripts or support bundles.

For a persistent managed Linux operator password, use **Host → Services** and
review/activate the supplied password through the existing guarded transaction.
For unmanaged local accounts, use `sudo passwd operator` from a trusted privileged
terminal. Manual changes do not update a managed hash file and may be overwritten
by later activation. Ensure a working Linux login before ending installation. Service runtime files and existing verifiers remain
available until the corresponding enrollment/recovery release gates are qualified.

Arcane reset still uses the backed-up offline helper and retains its generated
credential privately. It does not replace the database. A supplied-password reset
and removal of retained bootstrap credentials remain separate lifecycle work.

## SSH and Incus CLI

The `operator` account accepts Linux passwords by default and optional declared SSH keys. Root SSH login is disabled. It may use sudo for infrastructure
administration. Neither ordinary users nor `operator` receive an unrestricted Incus/Docker
socket group automatically. Sudo-all is intentional for the trusted infrastructure operator;
use separate accounts/project-scoped Incus identities for less-trusted users.

```console
ssh operator@HOST_IP
sudo homelab list
sudo homelab exec apps -- systemctl --failed
sudo homelab exec apps
sudo homelab exec-many apps,web -- df -h
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
  "aliases": { "100": "web", "105": "database" }
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

In the NixLab portal, open **Host → Access**. Enter a device name, a dedicated
bundle import passphrase (16–256 characters), and your current NixLab password.
The default identity manages only the `lab` project and lasts 90 days. The advanced
unrestricted checkbox explicitly grants Incus host administration; it requires the
same fresh password check and a separate acknowledgement in the API. Use it only
for a trusted host administrator. Incus authentication remains independent of your
NixLab portal session; no blanket CA trust is installed.

The portal generates an ECDSA P-384 client identity with client-authentication EKU,
digital-signature key usage and `CA:FALSE`. It registers only the public certificate
and offers the encrypted PKCS#12 bundle once, in the issuing browser session, for
two minutes. JSON contains fingerprint, scope and expiry metadata, never the key,
bundle or import passphrase. The passphrase goes through protected request/stdin
channels and is never returned separately. A **Claim pending download** button lets
the same session recover a lost receipt. A consumed or expired download cannot be
replayed, including after a broken connection. Logout/revocation drops pending
material; encrypted bytes live only in frontend memory, with expiry enforced on
claim and stale-buffer removal on subsequent requests or process shutdown.

Import the `.p12` using your dedicated bundle passphrase:

- Firefox: Settings → Privacy & Security → Certificates → View Certificates →
  Your Certificates → Import.
- Chromium: Settings → Privacy and security → Security → Manage certificates, or
  the operating system certificate manager used by that Chromium build.

Open the Incus HTTPS endpoint, select this browser identity and switch to the
**lab** project. Independently verify the server TLS fingerprint. Server trust does
not authorize the browser as an administrator. Supported browser/OS import stores
and selection dialogs must be qualified on the actual client versions in use.

Use **Refresh browser identities** to view NixLab-owned fingerprints, their actual
Incus project restrictions, expiration and trust status. **Renew** creates a new
identity and encrypted bundle; the previous identity stays trusted until explicitly
revoked or its disclosed expiration. **Revoke** requires your current NixLab
password and removes only the selected owned fingerprint through Incus trust APIs.
It also invalidates any pending portal download. The existing guard removes expired
NixLab-owned trust; unrelated and older external trust entries are never adopted
or removed automatically. Manage legacy/external certificates through Incus itself.
The separate encrypted recovery kit preserves the root ownership registry;
configuration-only exports and support metadata do not carry browser keys or bundles.

If download/import is cancelled, a timeout occurs, or the broker restarts, inspect
the identity list before retrying. Ownership intent is durable before Incus mutation,
so even uncertain registration remains individually visible/revocable. Private keys
are confined to root-only `/run/nixlab-browser` directories and cleaned on normal
success/failure. The existing locked guard removes crash remnants older than five
minutes; reboot clears `/run`. Guard failures are health findings, not silent cleanup.

For local recovery, inspect `sudo incus config trust list` and revoke a verified
full fingerprint with `sudo incus config trust remove FINGERPRINT`. Do not remove
other operators' trust. Browser bundle renewal/revocation does not change Linux,
NixLab portal, or application passwords.

The Python/HTTPS tests cover cryptography, failure cleanup, ownership, reauthentication,
CSRF/session binding and single-claim races. The real Incus fixture checks TLS 1.3,
lab mutations, rejection of other-project/global mutations, explicit unrestricted
scope, revocation and owned expiration while preserving pre-existing trust. Passing
those fixtures does not prove a browser's native import dialog or certificate
selection UI. Record Firefox/Chromium versions, OS store, import, lab selection,
expiry and revoke smoke results before claiming that client combination qualified.


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

## Arcane and Compose

The UI is HTTPS on **apps VM loopback**, not host loopback. Forward it from a client:

```console
ssh -N -J operator@HOST_IP -L 9443:127.0.0.1:9443 root@APPS_IP
```

Open `http://localhost:9443` through the encrypted SSH tunnel when setup TLS is disabled,
and log in as the bootstrapped admin. HTTPS is protected inside SSH; no public admin hostname
is configured. Arcane's Docker socket gives it root-equivalent control of the VM, not of the
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
copy/deploy them into the corresponding `/srv/compose/<project>` directory. Replace the Arcane
image reference with a reviewed immutable digest in `site.nix` too.

The web app stores a visit count in PostgreSQL and uses Redis as a disposable cache. `/health`
checks both dependencies without adding visits. It is a commissioning demo, not an authenticated
production application. Do not publish its unauthenticated write endpoint as a real service.
Docker/OCI tags are mutable; use tested digests and review security updates regularly.
Changing a database password file alone does not rotate the password in an existing PG volume.
Perform coordinated SQL/application rotation, take a backup and test reconnects.

Additional CLI-owned projects live under `/srv/compose/NAME/compose.yaml`, preferably from a
reviewed Git revision with runtime file secrets. UI-owned projects should instead use Arcane's
Stacks > Git Repository or editor workflow. Keep one writer per project. CLI-owned external
stacks have limited Arcane lifecycle/editing support; use the packaged helper for them.
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

Forward host TCP 9091 over SSH to inspect Prometheus (`ssh -L 9091:127.0.0.1:9091 operator@HOST_IP`).
Port 9090 remains available for Cockpit; history and Cockpit can be enabled together.
Only loopback exposes Prometheus and the host node exporter. Incus metrics are fetched through
its authenticated local UNIX socket into node-exporter textfiles. The VM node exporter is reached
by the host bridge IP; other guest monitoring requires enabling/configuring an exporter in that guest.
Incus UI/metrics supply per-instance CPU, memory, disk and network counters; node exporter supplies
host and VM OS metrics. Journal storage is persistent and size/retention bounded.

Prometheus alert rules cover missing targets, low disk space, failed units and stale backups.
There is no configured external Alertmanager receiver: alerts/local failure logs are not a promise
of a phone/email notification. Add your real receiver and runtime credentials before relying on
unattended alert delivery. Logs are not shipped to a remote collector by default.

## Port 80 and management shortcuts

Open `http://HOST_IP/` from a trusted LAN client. The lightweight port-80
entrypoint sends an HTTP 308 redirect to `https://HOST_IP:9440/` (or your
configured NixLab HTTPS port). **Authentication and session cookies are
never served over HTTP.** Keep using the NixLab-generated TLS identity and
verify its fingerprint on initial enrollment.

The portal Overview and sidebar link to enabled Incus UI (:8443),
Cockpit (configured port, normally :9090), and Arcane (configured
port, normally :9443 on fresh installs or :9441 on older installs).
Disabled or unconfigured services are not advertised as live. The
**Help** page includes a copy-only homelab cheatsheet.
Port 80 belongs to the *NixLab host IP*; a Mainsail server inside a
separate Incus guest may independently listen on port 80 on its own IP.

The port-80 listener uses Nginx on the host management interface. Do not
install a second host web server that also binds 0.0.0.0:80. After an
upgrade, run `sudo nixlab doctor` and check the HTTP-entry redirect
result; the recovery guard still protects unconfirmed configuration
switches. Do not expose management ports through Cloudflare tunnels.



### First-administrator claim state and interrupted enrollment

The root broker owns version 1 of `/var/lib/nixlab-admin/bootstrap-state.json` (mode 0600). The portal cannot read or edit it. `auth.json` and named account verifiers keep their existing formats; account state lives outside configuration snapshots and transaction rollback does not replace it.

- `unclaimed`: enrollment was explicitly enabled by the root installer; no browser can claim without a separate host approval.
- `host-verified`: a SHA-256 digest of the approved browser code, boot identity and ten-minute monotonic deadline permit one claim. A broker/web restart preserves the deadline; reboot or expiry invalidates approval.
- `committing`: durable claim intent exists. The broker removes legacy enrollment markers before atomically publishing the verifier. If publication is interrupted before a valid verifier exists, the portal stays closed and needs a new `sudo nixlab-admin approve-enrollment` approval. If the verifier committed, the broker completes the receipt on retry/restart. A repeated completed request returns `alreadyComplete` and cannot replace the password.
- `account-committed`: first claim is closed permanently. Missing or corrupt account files require authorized recovery, never another anonymous claim.
- `legacy-provisioning` / `migrated`: private legacy installer provisioning and existing accounts retain their working verifier. Authenticated password replacement commits the new verifier before retiring `setup-password`; the account fingerprint rejects old sessions.
- `recovery-authorized`: the local-root, terminal-only `sudo nixlab-admin reset-password` command has authorized a supplied replacement. It returns to `migrated` after publication; it never opens first-claim enrollment.

A preexisting opt-in `web-enrollment` marker without the new authority needs fresh console approval; an old approval file alone is insufficient. Existing valid accounts remain usable. Unknown future versions, malformed authority, and symlinks fail closed. Preserve the files and restore a supported version from the encrypted recovery kit rather than deleting account state to force enrollment. Unsupported authority also blocks automatic password/state rewrites; a compatible runtime or deliberate operator repair is required.

The encrypted secrets recovery kit includes the claim authority with account verifiers. It excludes reusable browser approval material; any in-flight proof within the authority remains bound to its original boot and expiry. The PAM/bootstrap experience remains tracked in #63 and browser recovery approval in #67; this state authority does not enable opt-in web enrollment by default.


### Verify this machine before first-admin creation

On a genuinely unclaimed host installed with `--wizard --web-enrollment`, the HTTPS portal offers **Verify this machine**. Verify the management CA first. Enter an existing local Linux administrator's username and password. The root broker runs the dedicated `nixlab-enrollment` PAM service for password and account checks, then asks sudo's policy whether that identity may run an argument-free root Bash shell. Wheel membership or permission for an unrelated limited command alone is insufficient. Locked, expired, missing, noninteractive or nonadministrator accounts and missing PAM support are denied. No Linux user or password is changed. The service uses NixOS PAM rule generation, with root and null-password shortcuts disabled. Additional authentication policy can be configured through that PAM service; challenges that cannot complete through its bounded password input fail closed and require console approval.

The host password exists only in the HTTPS request and a protected helper stdin pipe; it is never passed in argv, stored, or returned. The helper's output is discarded. Verification is bounded by helper deadlines, a two-helper concurrency limit, five attempts per source address and twenty globally in five minutes. Bootstrap session creation is also bounded. Rate records and hashed browser session metadata survive a broker/web restart.

Approval belongs to one `__Host-nixlab_bootstrap` Secure/HttpOnly/SameSite=Strict cookie, its source address and HTTPS origin, with a separate CSRF token and a fixed ten-minute monotonic lifetime. Reload can resume that browser's approval. A broker/web restart does not extend it; reboot invalidates it. Copying the displayed console approval code cannot claim from a different browser, and the legacy unbound HTTPS claim route no longer works.

For SSH-key-only administrators, open a private host terminal and run `sudo nixlab-admin approve-enrollment`. Enter the displayed browser code at the non-echoing prompt; the command refuses non-root/non-terminal/echo-fallback input and prints no token or password. This approves that same bounded browser, not a reusable password. If PAM verification is unavailable, use this fallback rather than weakening PAM or sudo policy.

After approval, choose a unique lower-case NixLab username and a new 16–256 character portal password. The existing single-claim authority commits one verifier and invalidates every bootstrap browser. The winning browser receives an ordinary NixLab session; host proof is not ordinary portal authentication. Named first administrators use the existing `users.json` format; selecting `admin` uses the existing reserved `auth.json` format. Previously installed accounts retain their login and no new first-claim path opens for them. Local `reset-password` still resets the reserved recovery administrator `admin`, preserving other named accounts.

This adds no password synchronization with Linux, Cockpit, Incus or Arcane. Default installer enrollment remains release-gated; do not remove a working legacy path or switch an established host without fresh-install/upgrade qualification. Browser bootstrap session metadata (`bootstrap-sessions.json`, root-only mode 0600) is bounded and contains hashes, scope, deadlines and rate counters, not reusable cookie values or host credentials. It is intentionally excluded from recovery kits; portable account verifiers and the existing claim authority remain included.
