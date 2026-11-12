# Home-server acceptance scenario

This scenario is a **host installation plus explicit workload commissioning**, not
automatic router/USB provisioning. The interview creates only the reviewed NixLab
foundation. It does not run an unreviewed third-party script, change router DHCP,
invent credentials, or promise physical guest restore results.

## Preflight and interview

On a conventional NixOS x86_64 server, confirm working networking, a working
backup/recovery path, KVM support and adequate RAM (prefer at least 8 GiB for
AdGuard, a printer stack and Docker VM together). Use the local console when
planning wired LAN bridge/VLAN changes.

Run:

    sudo nix --extra-experimental-features 'nix-command flakes' run github:OmarDardery/nixlab#install -- --interactive

Choose workloads=both, access=private, network=nat, and enable AdGuard,
Klipper, AIOStreams and basic metrics. Select laptop policy only for a laptop.
Read the pending tasks before confirmation. NixLab preserves the current host
address (for example 192.168.1.240/24), uplink and storage; it does not reserve
the address in the router. Configure a router DHCP reservation independently
if one is needed.

Headless equivalent: create a root-owned answers file:

    {
      "version": 1,
      "workloads": "both",
      "access": "private",
      "network": "nat",
      "adguard": true,
      "klipper": true,
      "aiostreams": true,
      "laptop": true,
      "monitoring": true,
      "backup": false
    }

Optional guestSubnet must be a *free* private /24; omit it to allow collision
avoidance. Save the file as /root/nixlab-answers.json with mode 0600.
Never place passwords, Cloudflare tunnel tokens or API keys in this file.

    sudo nix --extra-experimental-features 'nix-command flakes' run github:OmarDardery/nixlab#install -- --answers /root/nixlab-answers.json --plan-only
    sudo nix --extra-experimental-features 'nix-command flakes' run github:OmarDardery/nixlab#install -- --answers /root/nixlab-answers.json --non-interactive

After installation:

    sudo nixlab doctor
    sudo incus list --project lab
    systemctl --failed

Confirm the NixLab HTTPS portal, Incus and Cockpit access. The apps VM and
Portainer should be checked separately. Record the real CA fingerprint.

## AdGuard Home: LAN DNS

Use the pinned Incus Community Scripts runner only after inspecting the
upstream script; its support on this exact host remains unqualified:

    sudo nixlab-helpers doctor --project lab
    nixlab-helpers list
    sudo nixlab-helpers run adguard --project lab

Create a dedicated, persistent AdGuard guest and back it up. **NAT alone
does not provide LAN DNS access.** On a wired host, deliberately configure
an existing LAN bridge or VLAN in Connectivity and select it for AdGuard
inside Incus. Bridge creation is a separately reviewed change requiring a
local-console recovery path. Alternatively, narrowly forward *both* UDP/TCP
53 to the NAT guest after firewall review. Wi-Fi uplinks must not be
enslaved to an Ethernet bridge. MACVLAN can provide LAN addressing, but
normally cannot talk directly to the parent host; don't rely on host-side
health probes through that path.

Reserve a stable IP on the router and test from a different LAN client:

    dig @ADGUARD_LAN_IP example.com A
    dig +tcp @ADGUARD_LAN_IP example.com A

Check filtering/query logs and actual AdGuard UI ports. Verify reboot,
backup and restoration. **Only then** change router DHCP DNS; keep a
fallback resolver. Do not run competing DHCP servers without a transition.

## Klipper: printer MCU USB access

Keep the printer in a dedicated, independently backed-up Incus guest.
An existing Klipper container must be retained rather than recreated.
The current printer configuration is specific to the controller, probes
and motion system and cannot be inferred solely from an installer answer.

Discover the serial identity on the host:

    ls -l /dev/serial/by-id/
    sudo incus list --project lab

After verifying the real device and guest name, attach the character device:

    sudo incus config device add klipper printer-mcu unix-char source=/dev/serial/by-id/ACTUAL_DEVICE path=/dev/klipper-mcu --project lab
    sudo incus exec klipper --project lab -- ls -l /dev/klipper-mcu

Check Klipper's actual MCU connection, device ownership and reconnect after
USB unplug/host reboot. Mainsail (commonly port 80) and Moonraker
(commonly port 7125) must be restricted to LAN/VPN clients. Never expose
printer control endpoints through a public application tunnel.
See hardware.md for NixLab serial aliases.

## AIOStreams: isolated Docker/Compose stack

In the apps VM, use the example at
examples/homelab/aiostreams/compose.yaml and the accompanying
.env.example file. AIOStreams currently requires BASE_URL and a
64-character hexadecimal SECRET_KEY; AIOSTREAMS_AUTH is a
username:password list, and AIOSTREAMS_AUTH_REQUIRED=true protects its
configure page. Verify the current schema against
https://aiostreams.org/getting-started/deployment/.

Use a reviewed immutable ghcr.io/viren070/aiostreams image digest.
Provide secrets through a root-owned .env inside the apps VM or through
Portainer's protected stack environment, never the NixLab source.

    sudo incus exec apps --project lab -- docker info

Inside the apps VM, with the Compose files and chmod 600 .env:

    docker compose config --quiet
    docker compose up -d
    docker compose ps

Check real authenticated access at the apps VM's private IP on port 3000.
The named Docker volume is persistent **within the apps VM**, but still
requires a separate backup and restore drill. Host NixOS rollback will
not undo changes to the app database.

For a public application hostname (for example aio.example.com), create
or select an existing Cloudflare tunnel, store its credentials outside
Git/Nix store and review only an application ingress to
http://APPS_PRIVATE_IP:3000. Confirm HTTPS and login from outside your
LAN. Keep NixLab, Incus, Cockpit, Portainer, SSH and Klipper private.
The AIOStreams BASE_URL must match the actual external HTTPS URL.

## Day-two administration and commissioning

Use Incus UI for guest lifecycle, volumes, snapshots and consoles;
Portainer for Docker stacks; NixLab Changes for host configuration;
Recovery for independent restic backups; and SSH/Cockpit for operations.

After initial installation and two actual reboots, verify:
- management login and DHCP/reservation persistence
- guest IDs, disk mounts, DNS UDP/TCP 53 and correct router fallback
- printer MCU USB reconnect and no public Moonraker access
- AIOStreams authentication and actual data volume persistence
- off-host backup and an isolated guest/application restore
- no failed host services or manual Cockpit/Incus restarts
- laptop lid/thermal/power policy if used.

Record commissioning evidence; the release remains unqualified for a
specific physical target until these real checks are performed.
The real nested-KVM apps-VM CI issue remains tracked under #24.
