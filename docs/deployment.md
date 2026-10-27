# Deployment and commissioning

For a fresh conventional NixOS installation, the recommended path is now the
[bootstrap installer and web wizard](setup.md). It collects hardware/network values locally
and provides subsequent settings/rebuilds through the portal. The steps below remain the
manual/advanced source workflow.

## Collect real inventory first

Do not copy the evaluation fixture into production. From the physical machine or its
installer, collect:

```console
lscpu
ip -br link
ip -br address
ip -4 route
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
findmnt
```

Record CPU vendor/architecture, VT-x/AMD-V firmware availability, boot mode,
management NIC, management IP/prefix, gateway/DNS and trusted administrator subnet.
Choose a guest subnet that overlaps neither LAN, VPN, Docker networks nor upstream routes.
Reserve the apps IP outside the guest DHCP range. Identify existing storage, free staging
space, and an independent backup target. Keep serial/local console access during network changes.

This project does not install onto or repartition the running Proxmox disk. Install NixOS
onto a separate verified disk/host or use a recoverable maintenance plan with tested backups.
Disk and bootloader setup are inherently hardware-specific and intentionally omitted from defaults.

## Configure and build

On the installed NixOS host, use this repository as `/etc/nixos`, retaining the actual
installer-generated hardware configuration. Add the correct bootloader configuration from
the installation; the repository does not guess BIOS versus UEFI or a boot device.

Edit `site.nix` with collected inventory. Keep `management.configure = false` while retaining
the installer's working networking. If enabling managed static networking, verify all values
and disable conflicting installer NetworkManager/networkd/DHCP definitions explicitly.
`false` does not erase an installed network manager's configuration. Ensure the chosen
management IP is already assigned before binding Incus.

Add your SSH public keys, never a private key. Verify the guest subnet/DHCP/static reservation.
Use `driver = "dir"` unless a reviewed existing ZFS dataset is available. Enable backups only
when independent storage and credentials exist. Set `ready = true` last.

```console
cd /etc/nixos
git add site.nix hardware-configuration.nix
nix flake metadata
find . -name '*.nix' -exec nix-instantiate --parse '{}' ';' >/dev/null
nix eval .#nixosConfigurations.hypervisor.config.system.build.toplevel.drvPath
nix build .#nixosConfigurations.hypervisor.config.system.build.toplevel --out-link result-host
sudo nixos-rebuild test --flake .#hypervisor
```

`test` activates services and firewall rules immediately; it is not a passive preview.
Run it with local console access and a second authenticated SSH session. Verify connectivity,
then persist the generation:

```console
sudo nixos-rebuild switch --flake .#hypervisor
```

Use `nixos-rebuild boot` and activate on reboot when a maintenance window is required.
Rebuilds do not format disks or delete omitted instances. They can still change profile
limits/live networking and restart services; review the diff before each deployment.

The host derivation includes the Nix-built apps VM image. First deployment is a large build.
Nix imports that image automatically, creates `lab/apps` if absent, applies the reserved NIC,
CPU/RAM/root-disk size and starts it. It enables the native Incus agent for exec and console.
The guest root password is explicitly locked despite the upstream image module's development
console defaults. Secure Boot for this guest image is disabled; host firmware policy is separate.

## Secrets and first boot

Native systemd credential loading protects cloudflared's tunnel JSON. sops-nix handles
reprovisioning when enabled; encrypted files may be committed, private decryption keys may not.

1. Create separate age identities for host and applications VM, keeping recovery copies offline.
2. Encrypt host YAML with host recipients and guest YAML with guest recipients using `sops`.
3. Commit the encrypted files, set their repository-relative paths in `site.secrets`, and enable it.
4. Provision each machine's age private key at `site.secrets.ageKeyFile`, mode 0600, before activation.
   A host key does not automatically appear in the VM. Initial VM provisioning can use authenticated
   `incus file push` from a protected local file, followed by a guest rebuild.
5. Rebuild the host and guest. sops-nix materializes secret paths at boot and restarts dependent services.

Expected encrypted YAML keys:

| Machine | Key | Content |
|---|---|---|
| Host | `cloudflared` | Tunnel credentials JSON as a YAML multiline string |
| Host | `restic-password` | Restic repository password |
| Host | `restic-environment` | Runtime restic environment, e.g. repository access credentials; can be empty for credential-free targets |
| VM | `portainer-admin-password` | Strong plaintext bootstrap password, encrypted by sops in Git |
| VM | `demo-db-password` | Strong database password; only required when demo enabled |

Without sops-nix, provision the same root-owned files under `/var/lib/nixlab-secrets` using
your existing secret deployment system. Parent directory mode is 0700, ordinary files 0600.
The demo password file must be 0444 inside that protected directory so the unprivileged
web/PG containers can read its bind-mounted secret. File-based Compose secrets are mounts,
not a Docker Swarm encrypted secret store. Do not use `builtins.readFile` on plaintext secrets.

Portainer stays stopped while its bootstrap file is absent. Supplying the file then requires
`systemctl start docker-portainer` in the VM, or a rebuild/reboot. The bootstrap password flag
only creates the initial admin; it does not rotate an existing account. Set up account changes
through Portainer and retain its persistent data. The secret mount still needs to exist on restarts.

Encrypted guest files are part of the VM image closure; a decryption key is never baked into it.
Do not enable the demo until the guest can materialize its database secret.

## Deploy changes into the existing VM

Importing a newer image changes future creations only. Existing guest disks retain their OS.
Use NixOS deployment over SSH with the same reviewed inventory/repository revision, for example
from the host or a management client with a guest route and authorized SSH key:

```console
nixos-rebuild switch --flake .#apps --target-host root@APPS_IP
```

If networking intentionally permits only the host to reach guest SSH, use an SSH config
`ProxyJump operator@HOST_IP` for the VM. Validate host keys independently. Alternatively,
execute `nixos-rebuild switch --flake <reviewed-flake-reference>#apps` through authenticated
`homelab exec apps -- ...`; that reference must include your completed inventory, not the
unconfigured public template. No browser-terminal keystrokes are needed.

Colmena/deploy-rs add coordinated fleet deployment and rollback health checks; they are
not required for one VM. Ansible is useful for repeatable operations on non-NixOS guests;
use SSH keys or the maintained Incus connection plugin, inventory by names, check mode where
supported and explicit command modules instead of terminal automation. No web task runner
is exposed; SSH, Incus and packaged predefined actions already meet the requirement.

## Cloudflare Tunnel

Set `site.tunnel.enable`, tunnel UUID and application-only ingress, for example after assigning
real reachable addresses:

```nix
tunnel = {
  enable = true;
  uuid = "YOUR-EXISTING-TUNNEL-UUID";
  credentialsFile = "/var/lib/nixlab-secrets/cloudflared.json";
  ingress = {
    "photos.example.com" = "http://APPS_IP:2283";
    "git.example.com" = "http://APPS_IP:3000";
    "app.example.com" = "http://APPS_IP:8080";
  };
};
```

These hostname/service strings illustrate syntax, not deployable IPs. Immich and Forgejo are
not installed by this example; deploy their maintained upstream Compose projects separately.
The complete included demo is the app/PostgreSQL/Redis project.

Use a **locally managed** tunnel: Nix owns ingress, including a final `http_status:404`.
Create the tunnel and hostname DNS routes in the Cloudflare control plane once, then deliver
its tunnel-specific JSON through the secret mechanism. The broader account `cert.pem` is not
needed to run an already-created tunnel and is deliberately not installed on the hypervisor.
Cloud account resources cannot be manufactured by an unauthenticated Nix rebuild.

A remotely managed token tunnel instead makes dashboard/API ingress authoritative; do not
combine that mode with these local rules. Use external IaC for Cloudflare resource creation
if you need account-level reproducibility. The native module uses `LoadCredential`, a dynamic
user, boot startup and restart-on-failure; a ten-second retry delay is configured.

No incoming firewall ports are opened for the tunnel. Default NixOS outbound policy permits
Cloudflare connectivity; restrictive egress gateways must allow its documented endpoints on
UDP/TCP 7844 and DNS. Protect application identities with application auth/Cloudflare Access
as appropriate. Never add Incus, Portainer, SSH, Prometheus or Cockpit hostnames to public ingress.

Sources: [NixOS module](https://github.com/NixOS/nixpkgs/blob/718f670e845328e9c2cecdd832835712b2cbd065/nixos/modules/services/networking/cloudflared.nix),
[locally managed tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/),
[sops-nix](https://github.com/Mic92/sops-nix).
