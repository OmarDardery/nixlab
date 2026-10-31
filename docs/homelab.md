# Cockpit, Incus and Portainer homelab

Cockpit supplies the **host** terminal, services and journal. Incus supplies VM
and system-container management and guest consoles. Portainer supplies Docker
Compose stacks and application consoles inside the dedicated `apps` NixOS VM.
Docker does not run on the hypervisor.

## Enable and activate

1. Upgrade to the reviewed source revision. Preserve the machine directory, Incus
   database, storage pools, existing applications and hardware configuration.
2. In **Integrations**, select **Stage Cockpit + Portainer + laptop mode**. This
   changes desired settings only; it preserves network addresses, disks, Cloudflare
   ingress and existing workloads. File-owned settings are retained.
3. Enter a separate **local operator password for Cockpit** (16–256 characters) and
   an initial Portainer password. Leave an existing Portainer password blank.
   Changing the bootstrap secret does not reset an existing Portainer account;
   change that password within Portainer.
4. Build and review, activate, verify access, then confirm. Check the automatic
   `cockpit.managePassword` change in the review when enrolling the local account.
5. Open the active homepage links. With your intended host address:

   | Interface | URL | Authentication |
   |---|---|---|
   | Incus | `https://192.168.1.240:8443` | Enrolled Incus browser certificate |
   | Cockpit | `https://192.168.1.240:9090` | Linux `operator` account or existing non-root local account |
   | Portainer | `https://192.168.1.240:9443` | Portainer account |
   | Nixlab configuration/recovery | `https://192.168.1.240:9440` | Nixlab portal account |

The preset selects Portainer port 9443. Older settings retain **9441** until
explicitly changed. Management ports are configurable and collision-checked.
Homepage links use active settings, rather than an unapplied draft. Portainer's
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

Cockpit is installed natively and authenticates through PAM. Enrolling its operator
password does **not** enable SSH password authentication or root web login. The
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

Your host `192.168.1.240` and bridge `10.90.90.1/24` are separate networks. NAT
guests do not acquire `192.168.1.x` addresses automatically. Do not assign the
host's `.240` to a guest. Do not remap an initialized Incus subnet, pool or project
merely to match a fresh-install example.

For directly addressed LAN guests such as AdGuard, use a reviewed **wired** bridge
and reserve distinct free addresses (for example `.241` onward after checking
router exclusions). Moving the host address onto a bridge is a network migration;
keep console access and use confirmation recovery. Wi-Fi cannot be enslaved as an
Ethernet bridge. A routed alternative is a router route for `10.90.90.0/24` via
`192.168.1.240`, plus reviewed trusted-LAN guest access. Point router DHCP DNS at
AdGuard's reachable address only after testing DNS from a LAN client. See
[networking](networking.md).

Preserve running AIOStreams, Vaultwarden and Klipper instances. Existing instances
outside platform-declared ownership remain Incus-managed; enabling the apps VM
does not move them. New Compose stacks can use Portainer. Keep AIOStreams
authentication and its public URL, `https://aiostreams.dardery.work`, in the
application's supported settings and preserve its existing Cloudflare origin.
Never put authentication secrets into uploaded Nix files.

For Klipper, retain `/dev/serial/by-id/...` or a deliberately discovered stable
alias. Declare host aliases through **Hardware & Disks**, then assign the device
in Incus with the correct guest UID/GID/mode. Save `printer.cfg`, macros and
Moonraker configuration independently before changing the instance. Enabling
management tools does not attach a printer or reinstall software.

## Verification

Test Cockpit login, terminal and sudo; Incus guest consoles; a Portainer stack and
console; rejection from an untrusted client; and access after Docker restart and
host reboot. Confirm lid-closed operation. Test application data and an off-host
restore before retiring the previous system. See [commissioning](commissioning.md).

Automated VM checks exercise real Cockpit PAM/TLS and a host shell channel, plus
real Docker forwarding with a disposable HTTP fixture before/after Docker restart.
The fixture tests the Portainer port's access boundary; it does not claim to boot
the external Portainer image or validate your application stacks. Those remain
target-machine checks.
