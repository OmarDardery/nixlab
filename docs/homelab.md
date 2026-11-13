# Cockpit, Incus and Portainer homelab

Cockpit supplies the **host** terminal, services and journal. Incus supplies VM
and system-container management and guest consoles. Portainer supplies Docker
Compose stacks and application consoles inside the dedicated `apps` NixOS VM.
Docker does not run on the hypervisor.

For a plain NixOS server, use the [single-command workflow](plain-nixos.md).
The steps below enable the optional tools from the host console.

## Enable and activate

1. Upgrade to the reviewed source revision. Preserve the machine directory, Incus
   database, storage pools, existing applications and hardware configuration.
2. In **Host → Services**, enable Cockpit and/or the isolated applications VM.
   Choose the Portainer HTTPS port explicitly (9443 for a fresh homelab stack).
   This stages desired settings; file-owned settings are retained. Laptop power
   policy is separate under **Host → System**; it is not changed by enabling tools.
3. Enter a separate **local operator password for Cockpit** (16–256 characters) and
   an initial Portainer password. Leave an existing Portainer password blank.
   Changing the bootstrap secret does not reset an existing Portainer account;
   change that password within Portainer.
4. Build and review, activate, verify access, then confirm. Check the automatic
   `cockpit.managePassword` change in the review when enrolling the local account.
5. Open the active homepage links. Use the detected address printed by installation (shown as `HOST_IP` below):

   | Interface | URL | Authentication |
   |---|---|---|
   | Incus | `https://HOST_IP:8443` | Enrolled Incus browser certificate |
   | Cockpit | `https://HOST_IP:9090` | Linux `operator` account or existing non-root local account |
   | Portainer | `https://HOST_IP:9443` | Portainer account |
   | Nixlab configuration/recovery | `https://HOST_IP:9440` | Nixlab portal account |

Existing settings retain their Portainer port, including **9441**, until explicitly changed. Management ports are configurable and collision-checked.
Homepage links use the observed management address, falling back to active settings. Portainer's
internal port remains 9443 behind the host's TLS-verified proxy. Only the
hypervisor may reach that published VM endpoint, including after Docker restarts.
Other Docker applications retain their own exposure policies.

For file configuration, upload the following as `homelab/management.nix` or copy
`examples/homelab/management.nix` to `machine/modules/homelab/management.nix`:

```nix
{ ... }: {
  nixlab.settings = {
    cockpit.enable = true;
    apps = { enable = true; portainerPort = 9443; };
    maintenance.laptopMode = true;
  };
}
```

Use the [reviewed module workflow](file-modules.md). A direct rebuild uses
`sudo nixos-rebuild switch --flake path:/etc/nixos#hypervisor` from that installation
root; direct switches do not arm portal confirmation recovery.

## Host login and commands

Cockpit is installed natively and authenticates through PAM. SSH password login is
enabled by default; the same Linux password works for SSH and Cockpit. Select
key-only SSH under Access if needed. Root SSH and root web login remain disabled. The
operator is a trusted infrastructure administrator with sudo access. Portal roles,
Incus identities, Linux users and Portainer roles remain separate.

The portal hashes the Linux password before staging it. Only a salted SHA-512
hash enters its root-only runtime secret file; neither plaintext nor the hash
enters the Nix source/store. Guarded activation preserves the old local operator
credential for recovery, including locking a newly enrolled account on rollback.
The encrypted-secret kit includes the managed hash. sops-nix users instead supply
`cockpit-operator-password` in the encrypted host file and set
`cockpit.managePassword = true`.

Alternatively, keep `managePassword = false` and use an existing local-account
password, or run `sudo passwd operator` from the console/SSH. Manual password
changes are outside the portal transaction. On immutable-user setups, declare the
local account/password through the preserved NixOS configuration.

Cockpit uses the local management CA while the Nixlab setup service is enabled;
verify its fingerprint from the host console. A manual setup without the portal
uses Cockpit's own certificate. Reach management only over trusted LAN/private
VPN. Cloudflare ingress rejects management ports, including customized Cockpit
and Portainer ports.

Use Cockpit's Terminal for `sudo incus list --all-projects`, journals and reviewed
Nix rebuilds. Use Incus consoles for guest commands. Paste Compose into
**Portainer → Stacks → Add stack → Web editor**. Docker containers are not Incus
system containers. Nix owns the apps VM and Portainer service; Portainer owns your
additional stacks. Back up stack YAML and runtime secrets independently.

## Keep the working services and network

The host management address and private guest bridge are separate networks. NAT
guests do not acquire `192.168.1.x` addresses automatically. Do not assign the
host's management address to a guest. Do not remap an initialized Incus subnet, pool or project
merely to match a fresh-install example.

For directly addressed LAN guests, use a reviewed **wired** bridge, VLAN
or routed network and reserve distinct free addresses after checking router
exclusions. Moving host addressing onto a bridge is a network migration;
retain console access and confirmation recovery. A private NAT bridge does
not automatically expose guest services to the LAN. Validate routing,
firewall rules, DNS and application reachability before changing router
settings. See [networking](networking.md).

Preserve all existing workloads and their data volumes, credentials and
application URLs. Guests outside platform-declared ownership remain
Incus-managed; enabling the apps VM does not migrate them. New Compose
stacks can use Portainer. Never put runtime secrets in uploaded Nix files.

For hardware-backed workloads, identify stable `/dev/serial/by-id/...`
paths or declared device aliases under **Hardware & Disks**, then attach
only the selected device to the correct guest with reviewed permissions.
Back up application configuration separately. Enabling management tools
never attaches peripherals or reinstalls third-party applications.

## Verification

Test Cockpit login, terminal and sudo; Incus guest consoles; a Portainer stack and
console; rejection from an untrusted client; and access after Docker restart and
host reboot. Confirm lid-closed operation. Test application data and an off-host
restore before retiring the previous system. See [commissioning](commissioning.md).

New-name archive/snapshot restores reset generated NIC MAC addresses while stopped,
so they can coexist with the original. Explicit NIC MACs and fixed addresses are
retained for administrator review; conflicting identities block release from
quarantine. The original guest and backup archive remain unchanged.

Automated VM checks exercise real Cockpit PAM/TLS and a host shell channel, plus
real Docker forwarding with a disposable HTTP fixture before/after Docker restart.
The fixture tests the Portainer port's access boundary; it does not claim to boot
the external Portainer image or validate your application stacks. Those remain
target-machine checks.

