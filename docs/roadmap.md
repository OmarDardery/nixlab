# Quality-of-life roadmap

Status: proposed work, except the homepage launcher described below. No roadmap item is an
installed service or a promise of compatibility. Order prioritizes recovery and daily operations
over additional dashboards. Features should work through structured, reviewed Nix settings and
existing Incus/SSH APIs. Avoid adding persistent processes when an on-demand query or systemd job
will do.

## Available now

The overview links to Incus, optional Portainer, upstream Incus/community helper catalogs,
configuration pages, SSH access, configured Cloudflare public hostnames, project guides and source.
Management URLs use active settings rather than unsaved form values. Disabled services point to
their configuration page. Prometheus remains on loopback; the homepage does not invent a public
monitoring endpoint. Resource links open separate tabs without opener/referrer access and do not
load external embeds or execute scripts.

The platform already includes reviewed builds, activation confirmation/recovery, settings export,
UUID mounts, modular radios, offline guest backups, an administration CLI and optional history.
The items below extend these features rather than install overlapping control panels.

## P0 — Commissioning and recovery

| Work | Why | Acceptance |
|---|---|---|
| Repeatable target-host test matrix | Configuration builds cannot establish boot/recovery correctness | Record Intel/AMD, UEFI/BIOS, Ethernet/Wi-Fi, guest boot, missing data disk and interrupted activation results; preserve the original host generation |
| Restore drill | A successful backup job alone does not establish recoverability | Restore a guest and a database into isolated test instances; verify data and record elapsed time |
| Recovery kit export | Configuration, secrets and guest data need separate recovery paths | Export source revision, lock, desired settings, imported hardware modules and a manifest; keep secrets in a separate encrypted archive; restore on a spare NixOS machine |
| Release-aware documentation links | Pre-release links currently target the implementation branch | Ship a release/source identifier with the portal; ensure every guide link resolves for that installed revision, including offline recovery instructions |

## P1 — Daily administration

| Work | Lightweight design | Acceptance |
|---|---|---|
| Unified guest summary | Fetch Incus API inventory on demand; keep deep guest administration in Incus | Show guest names, IPs, state and limits; provide start/stop/restart through a strict action allowlist with output/status and audit records; surface source ownership |
| Application recipe catalog | Curated Nix modules or Git-owned Compose definitions; reuse current guest deployment jobs | Preview resource/network/storage changes, pin image digests/guest fingerprints and deploy a reviewed recipe without raw shell input |
| Optional community helper compatibility | Package a reviewed/pinned Incus script engine and dependencies; support a curated allowlist initially | Test representative guest installers on NixOS-hosted Incus; honor project/profile/network selection; record source hashes and logs; never run Proxmox host tweaks |
| Backup/restore workflow | Extend existing systemd backup jobs and transaction logs | Show last success, failures, retention and destination; preview a restore into a new name instead of overwriting a live guest |
| Optional failure notifications | Native systemd failure units with a configured webhook/email destination and runtime credentials | Notify backup/activation/storage failures, redact secrets, support a test message and avoid a new always-on task platform |
| Configuration ownership/drift view | Compare evaluated source intent with Incus/API state when requested | Explain which fields Nix owns; show drift and require review before adopting existing resources; do not silently recreate/delete guests |

Community helper scripts remain imperative inside their guests. Pinning them improves traceability
but does not freeze package repositories or make every installation reproducible. Prefer native
recipes for applications requiring full declarative management. Compatibility mode should be
optional, add no resident daemon, and never expose arbitrary root script execution over the web.

## P2 — Storage, access and maintenance

| Work | Proposed behavior | Acceptance |
|---|---|---|
| Disk health and capacity | Optional native smartd plus on-demand filesystem/pool health | Identify device limitations (USB/RAID), show failures and attach alerts; keep monitoring opt-in |
| Storage migration assistant | Preview existing UUID/dataset mappings, destination capacity and Incus volume movement | Require a verified backup and explicit migration plan; no automatic formatting, shrinking or pool destruction |
| Private remote access | Optional native Tailscale configuration, private names and documented TLS trust | Keep dashboards private, accept secrets at runtime and validate access before removing the existing route |
| Update impact preview | Expand existing review to show lock/input changes, affected services and guest OS changes | Preserve reviewed immutable builds, recovery roots and explicit reboots; distinguish OS rollback from database migration |
| Secret/certificate health | Display presence, ownership and expiry; support application-specific rotation jobs | Never display stored private material; explain bootstrap credentials versus rotation of existing accounts |
| Proxmox migration assistant | Extend the migration guide with inventory and import previews | Import into new guest names, validate disks/networking before cutover and keep the original guest recoverable |

## P3 — Usability

- Searchable keyboard launcher for tools, guests, projects and documentation.
- Mobile/accessibility checks: keyboard focus, labels, contrast and narrow-screen tables.
- Copyable, redacted diagnostic bundle with exact revision, service failures and relevant logs.
- Offline packaged documentation with lightweight static rendering.
- Optional saved administration tasks with fixed argument schemas, exit status and audit records.
  Preserve the constrained execution boundary; do not add a general browser root terminal.

## Resource and ownership rules

1. No additional dashboard or daemon without a concrete capability the existing stack lacks.
2. Separate optional VM memory reservations from host overhead; measure RSS, idle CPU, writes and
   startup time on target hardware before claiming savings.
3. Services, firewall rules, mounts, radio policy, schedules and source-managed application
   definitions flow through Nix. Prefer official options and idempotent native integrations.
4. Use background systemd jobs for long operations, on-demand reads for summaries and bounded
   polling while a job is active. Never expose credentials through diagnostics.
5. Keep mutable disks, databases, snapshots, helper-installed applications and runtime identities
   explicit. Back them up independently; OS generations alone cannot restore their contents.
