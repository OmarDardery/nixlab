"""Versioned, non-secret installation intent -> conservative NixLab setup choices.

This module NEVER changes host networking, guests, disks, router DNS, USB devices,
Cloudflare or credentials. Its output is a reviewed plan for the existing
transaction engine and explicit post-install tasks for external workloads.
"""
import copy
import ipaddress
import json
from pathlib import Path

VERSION = 1
WORKLOADS = ("containers", "compose", "both")
ACCESS = ("lan", "private", "public")
NETWORKS = ("nat", "lan", "vlan")
KNOWN = frozenset(("version", "workloads", "access", "network", "adguard",
                  "klipper", "aiostreams", "laptop", "monitoring", "backup",
                  "guestSubnet"))
QUESTIONS = (
    ("workloads", "Workloads [containers/compose/both]", "both", WORKLOADS),
    ("access", "Access [lan/private/public]", "lan", ACCESS),
    ("network", "Guest connectivity [nat/lan/vlan]", "nat", NETWORKS),
    ("adguard", "Plan an AdGuard Home DNS guest? [y/N]", "no", ("yes", "no")),
    ("klipper", "Plan a Klipper printer guest? [y/N]", "no", ("yes", "no")),
    ("aiostreams", "Plan an AIOStreams Compose stack? [y/N]", "no", ("yes", "no")),
    ("laptop", "Enable always-on laptop lid policy? [y/N]", "no", ("yes", "no")),
    ("monitoring", "Collect basic host metrics? [Y/n]", "yes", ("yes", "no")),
)


def defaults():
    return dict(version=VERSION, workloads="both", access="lan", network="nat",
                adguard=False, klipper=False, aiostreams=False,
                laptop=False, monitoring=True, backup=False, guestSubnet=None)


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - KNOWN:
        raise ValueError("Installer answers must be an object with documented keys only")
    result = defaults()
    result.update(raw)
    if type(result["version"]) is not int or result["version"] != VERSION:
        raise ValueError("Unsupported installer answers version")
    for field, choices in (("workloads", WORKLOADS), ("access", ACCESS), ("network", NETWORKS)):
        if result[field] not in choices:
            raise ValueError(field + " must be one of: " + ", ".join(choices))
    for field in ("adguard", "klipper", "aiostreams", "laptop", "monitoring", "backup"):
        if type(result[field]) is not bool:
            raise ValueError(field + " must be true or false")
    subnet = result["guestSubnet"]
    if subnet is not None:
        net = ipaddress.ip_network(subnet, strict=True)
        private = tuple(ipaddress.ip_network(v) for v in
                        ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if net.version != 4 or net.prefixlen != 24 or not any(net.subnet_of(p) for p in private):
            raise ValueError("guestSubnet must be an unused RFC1918 IPv4 /24")
        result["guestSubnet"] = str(net)
    if result["aiostreams"] and result["workloads"] == "containers":
        raise ValueError("AIOStreams needs the Compose apps VM; choose workloads=both or compose")
    return result


def load(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("Answer file must be a small, regular JSON file")
    return normalize(json.loads(path.read_text()))


def interview(input_fn=input, output=print, existing=None, on_answer=None):
    values = dict(defaults() if existing is None else normalize(existing))
    output("NixLab configuration interview. Enter keeps the recommendation.")
    for key, prompt, fallback, choices in QUESTIONS:
        original = values[key]
        default = ("yes" if original else "no") if isinstance(original, bool) else str(original)
        while True:
            response = input_fn(prompt + " (default: " + default + "): ").strip().lower()
            value = response or default
            if value in ("y", "yes"): value = "yes"
            if value in ("n", "no"): value = "no"
            if value in choices: break
            output("Choose one of: " + ", ".join(choices))
        values[key] = (value == "yes") if isinstance(original, bool) else value
        if on_answer is not None:
            on_answer(normalize(values))
    return normalize(values)


def plan(answers, host, settings, *, remote=False):
    a = normalize(answers)
    m = settings["management"]
    net = ipaddress.ip_interface(str(m["address"]) + "/" + str(m["prefixLength"])).network
    wireless = any(i["name"] == m["interface"] and i.get("wireless") for i in host.get("interfaces", []))
    ram_kib = int(host.get("memoryKiB") or 0)
    # Unknown memory never pretends to be enough for a heavy apps VM.
    apps = a["workloads"] in ("compose", "both")
    if apps and ram_kib and ram_kib < 4 * 1024 * 1024:
        raise ValueError("Apps VM needs at least 4 GiB physical RAM; choose containers or upgrade RAM")
    if apps and not ram_kib:
        raise ValueError("Cannot discover available RAM for the requested apps VM")
    if a["network"] != "nat" and wireless:
        raise ValueError("Wi-Fi uplinks cannot be enslaved to an Ethernet bridge/VLAN. Use NAT and configure LAN access separately")
    if a["network"] != "nat" and remote:
        raise ValueError("LAN bridge/VLAN changes require local-console review; use NAT for remote installation")
    if a["network"] != "nat":
        # Do not claim to have a safe bridge/VLAN activation wizard. Existing
        # Ethernet connectivity is preserved; the bridge requires explicit review.
        deferred_network = True
    else:
        deferred_network = False
    if a["guestSubnet"]:
        selected = ipaddress.ip_network(a["guestSubnet"])
        if selected.overlaps(net):
            raise ValueError("Guest subnet overlaps management LAN")
        for route in host.get("routes", []):
            try:
                occupied = ipaddress.ip_network(route.get("dst", "default"), strict=False)
            except ValueError:
                continue
            if selected.overlaps(occupied) and not (
                    settings.get("ready") and settings["guests"].get("cidr") == str(selected)):
                raise ValueError("Guest subnet overlaps an existing route: " + str(occupied))
    if a["network"] == "vlan" and wireless:
        raise ValueError("VLAN-backed guest access requires a wired uplink")
    pending = []
    if deferred_network:
        pending.append("Review an existing Ethernet bridge/VLAN and switch configuration in Connectivity; no host NIC is modified by this installer")
    if a["adguard"]:
        pending.extend((
            "Install a supported AdGuard Home Incus container with the pinned nixlab-helpers wizard; this installer never runs community scripts without review",
            "Assign AdGuard an actual stable LAN-reachable IP (wired bridge/VLAN or individually reviewed forwarding); verify TCP+UDP 53 before changing router DHCP DNS",
            "After a successful DNS+restore test, set router DHCP DNS to the verified AdGuard address and retain a recovery resolver",
        ))
    if a["klipper"]:
        pending.extend((
            "Install Klipper in a dedicated container; inspect the actual MCU USB vendor/product/serial and declare a stable hardware alias",
            "Pass the serial character device through Incus, verify /dev/klipper-mcu ownership, and validate Mainsail/Moonraker LAN access",
            "Never expose the printer control API publicly; confirm printer power-loss and restart recovery",
        ))
    if a["aiostreams"]:
        pending.extend((
            "Deploy the supplied AIOStreams Compose example in the apps VM only after setting private app credentials and an explicitly selected upstream image",
            "Verify the stack locally before exposing an application hostname through a reviewed Cloudflare tunnel",
        ))
    if a["access"] == "private":
        pending.append("Enable/enroll Tailscale through the Private Access recipe; verify private SSH without publishing management dashboards")
    if a["access"] == "public":
        pending.append("Supply an existing tunnel UUID and root-only Cloudflare credential separately; publish only selected app origins, never SSH/Incus/Cockpit/NixLab")
    if not a["backup"]:
        pending.append("Configure an independent off-host restic repository and test an actual isolated restore before trusting guest data")
    else:
        pending.append("Supply an off-host restic repository, encryption password and schedule through Recovery; no backup destination is invented")
    if a["monitoring"]:
        pending.append("Host metrics collection is enabled; notification delivery and retention require separate confirmation")
    pending.append("Run commissioning after initial install and two reboots; verify DHCP, SSH, Incus, apps VM, and all real workloads")
    reasons = [
        "Existing management addressing, host uplink, users and disks are preserved",
        "Private Incus NAT is the safe initial guest topology",
        "Cockpit and Incus remain available; Docker/Portainer are isolated inside an apps VM" if apps else
        "Docker/Portainer VM is omitted to conserve resources",
        "No secrets, router DNS settings or community scripts are applied automatically",
    ]
    if a["network"] != "nat":
        reasons[1] = "Requested LAN networking is deferred for guarded local-console review; initial guest topology stays NAT"
    if a["adguard"] and a["network"] == "nat":
        reasons.append("NAT alone does NOT serve DNS to LAN clients; AdGuard needs an explicit LAN reachability step")
    return dict(version=VERSION, answers=a, apps=apps, laptop=a["laptop"],
                monitoring=a["monitoring"], guestSubnet=a["guestSubnet"],
                reasons=reasons, pending=pending, networkRequiresReview=deferred_network,
                estimatedAppMemoryMiB=(int(settings["apps"]["memory"].replace("GiB", "")) * 1024 if apps else 0),
                managementAddress=m["address"])


def apply(candidate, proposal):
    """Apply only supported typed options. All risky external tasks stay pending."""
    value = copy.deepcopy(candidate)
    value["maintenance"]["laptopMode"] = proposal["laptop"]
    value["monitoring"]["collection"] = proposal["monitoring"]
    # direct.prepare has already selected apps and collision-safe private NAT.
    return value


def preview(proposal, output=print):
    output("\nNixLab proposed configuration (review before activation)")
    output("  Apps VM: " + ("enabled" if proposal["apps"] else "omitted"))
    output("  Host metrics: " + ("enabled" if proposal["monitoring"] else "disabled"))
    output("  Laptop policy: " + ("always-on" if proposal["laptop"] else "standard server"))
    output("  Guest network: private NAT (existing host LAN unchanged)")
    output("  Management: " + proposal["managementAddress"] + " (preserved)")
    for reason in proposal["reasons"]:
        output("  Why: " + reason)
    output("External/manual steps (NOT performed by installer):")
    for item in proposal["pending"]:
        output("  - " + item)
