# Networking and IP management

The default keeps the physical management NIC intact. Incus creates a separate private
bridge; NixOS owns its DHCP/DNS input permissions, forwarding and NAT. Incus bridge
firewall/NAT rules are disabled so there is one explicit host policy. The NixOS nftables
module does not flush unrelated tables. Docker's normal firewall runs inside the VM only.

## Illustrative address plan — adapt every value

| Object | Example only | Meaning |
|---|---|---|
| Physical management NIC | `enp2s0` | Must be discovered, never guessed |
| LAN | `192.168.50.0/24` | Existing router-managed network |
| Hypervisor | `192.168.50.10/24` | Host management address, not a guest |
| LAN gateway | `192.168.50.1` | Existing router |
| Incus bridge | `10.77.0.1/24` | Host-side address for private guests/DNS/DHCP |
| Incus DHCP | `10.77.0.100`–`10.77.0.199` | Dynamic guest reservations |
| Apps VM | `10.77.0.10` | Static DHCP reservation outside the range |
| Other guest | `10.77.0.20` | A distinct VM/container IP |
| Compose networks | Docker-allocated non-overlapping subnet | Internal app services, not LAN addresses |
| Public hostname | `app.example.com` | Cloudflare endpoint routed to an application port |

The apps VM and containers use DHCP normally; Incus assigns the same reserved address
from `eth0.ipv4.address`. The guest IP is a guest endpoint, distinct from the host IP and
from Docker-internal addresses. Fixed guest IPs must remain outside the DHCP dynamic range.
Container CPU/RAM are profile/config limits. VM disk sizes use block images; a directory
pool cannot promise enforced system-container disk quotas.

## Access paths and isolation

- Trusted management source CIDRs on the specified physical interface may reach host TCP
  22 and 8443. No global allow rule exposes those ports on other interfaces.
- Guests reach host DHCP/DNS only. The bridge is not a trusted host firewall interface.
- Guests on the bridge can communicate. This is one application trust zone, not per-tenant
  isolation; use separate bridges/ACLs for untrusted tenants.
- Guest forwarding to RFC1918, CGNAT and link-local destinations is blocked, protecting the
  LAN/VPN management ranges. Guest internet egress uses NAT through the explicit uplink.
- Replies to allowed established flows are accepted by NixOS. Host-originated connections
  can reach guest metrics and application ports directly.
- LAN-to-private-guest forwarding is disabled by default. Enable `connectivity.trustedLANAccess` to permit trusted management CIDRs through the registered guest interfaces; Incus retains guest ACL/forward ownership. Routed LAN access requires
  a static route `10.77.0.0/24 via 192.168.50.10` on the real router/client, using adapted values.
  This route is an external network initialization step; the host does not change the router.
- The demo's Docker port is published in the VM. Docker DNAT can bypass the VM input firewall;
  the outer host forward policy is the security boundary for LAN traffic. Do not attach the
  VM directly to the LAN without adding a guest Docker-aware forwarding policy.
- In manual mode Portainer binds VM loopback. With setup enabled, its private VM TLS listener is reached through the verified host proxy at 9441. It is not a public application origin.

If guests need a LAN DNS/NTP/server, add a narrowly scoped allow rule before the private-range
drop. Do not broadly trust `incusbr0` as a connectivity workaround. Verify new rules with nft
counters and connectivity tests, including after a firewall rebuild.

A guest or host with multiple interfaces may require reviewing reverse-path filtering and
routes. This default is a simple single-uplink topology, not a multi-router policy.

## Giving a guest a LAN address

Default guest IPs are private routed/NAT addresses, not layer-2 LAN leases. To give one guest
a true LAN IP, explicitly build a separate host bridge or VLAN-backed application bridge and
attach its NIC to it. Do not enslave the active management NIC remotely: move the host address
onto a bridge only in a console-backed network change plan. A second physical NIC is simpler.

Optional VLAN configuration uses native `networking.vlans`, `networking.bridges` (or equivalent
systemd-networkd netdev/network units), with a switch trunk and router/firewall rules. Device
NICs can use an existing bridge's `nictype = "bridged"` and `parent = "br-apps"`. Do not
attach an Incus-managed `network` and an unmanaged `parent` in the same NIC definition.
No interface, VLAN tag, router route or IPv6 prefix is guessed here.

## Docker networking choices

Ordinary Docker bridge addresses are private to the VM's network namespace and its routing/NAT
rules; they are not advertised to the LAN. Services in one Compose project resolve one another
by service name (`postgres`, `redis`), without publishing their database/cache ports.

| Method | Trade-off |
|---|---|
| Publish app port on VM | Simple; one stable guest IP and internal Compose network; default |
| Reverse proxy inside VM | Useful for many hostnames/HTTPS endpoints on one VM IP; Cloudflare can also route per hostname/port directly |
| macvlan | Gives containers LAN MAC/IPs, needs L2 support/IPAM; host-to-container communication needs extra handling |
| ipvlan | Fewer MACs, still requires deliberate L2/L3 routing and IPAM |
| Routed Docker networks | Possible with router routes and Docker forwarding policy; more operational work |
| One VM/system container per application | Clear IP/resource/security boundary; more guest overhead; create additional applications guests as needed |

Do not allocate a LAN IP to PostgreSQL/Redis just to make a dashboard show individual addresses.
Use one guest IP per application isolation boundary and Compose networking for its components.

IPv4 is the supported default. Host IPv6 policy can follow the installer; the Incus bridge has
`ipv6.address = "none"`. Enable guest IPv6 only with an allocated routed prefix, IPv6 firewall
and tested router advertisements/DNS. The source-CIDR management allow rules here are IPv4.

Private Tailscale access is an optional extension: enable its native module and define tailnet
ACLs, explicit interface/source firewall allowances and service reachability. This repository
does not silently enroll a node, trust all `tailscale0` traffic, or open management to a VPN.


## Reviewed Ethernet configuration

Connectivity creates host interfaces, not guest network objects. For example:

```nix
nixlab.settings.connectivity = {
  bridges = [
    { name = "br-lan"; interfaces = [ "enp2s0" ]; }
    { name = "br-apps"; interfaces = [ "vlan20" ]; }
  ];
  vlans = [ { name = "vlan20"; interface = "enp3s0"; id = 20; } ];
  guestInterfaces = [ "incusbr0" ];
  trustedLANAccess = false;
  privateAccess = [ { cidr = "192.168.50.53/32"; protocol = "udp"; ports = [ 53 ]; } ];
};
```

Replace names and addresses from real inventory. If the selected port carries
management, configure the same address/gateway/DNS on `br-lan` in that review.
Review the new management URL, keep console access, activate and confirm. An
interface belongs to only one bridge, VLAN parents must be discovered Ethernet
interfaces, and Wi-Fi bridge ports are rejected. The installed NetworkManager
owner is preserved; conventional hosts use native NixOS bridge/VLAN options.

In Incus, attach guests to `br-lan` or `br-apps` as existing unmanaged bridges.
Their addresses come from the LAN/VLAN router; Incus DHCP reservations do not
configure an unmanaged bridge. These guests share that LAN's trust boundary.
Do not assume the private-NAT firewall policy isolates a guest attached directly
to the LAN. Configure guest/native Incus policy as appropriate.

Create additional private managed networks in Incus. Register their bridge names
under `guestInterfaces` for outer host firewall integration. Incus owns NAT on
those additional networks; only the reserved `incusbr0` baseline uses host NAT.
Use `privateAccess` for selected guest access to LAN DNS/NTP/application servers.
It never grants host management input. Guest Internet forwarding is IPv4-only;
IPv6 needs a separate routed-prefix and firewall design.

No demo-specific port is opened automatically. Without a router route, use native
network forwards on an Incus-owned network or a LAN bridge. Forwarded connections
must also pass the host's trusted-LAN boundary; creating a forward does not grant
access from untrusted sources. The reserved incusbr0 has host-owned NAT/firewall;
use an additional Incus-owned network for native forwarding rules.
Switch trunks/router changes are external actions. See
[ownership](ownership.md) before editing the reserved bootstrap network in Incus.
