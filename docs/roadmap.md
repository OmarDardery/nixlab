# Focused single-host foundation

Nixlab prepares, maintains and recovers a NixOS host for Incus. Incus remains the
virtualization UI. See the [ownership contract](ownership.md).

| Area | Delivered behavior |
|---|---|
| Host lifecycle | Single-command preserved-installation setup, reviewed changes, source/OS updates, confirmation recovery |
| Ethernet | Host bridges/VLANs, management addressing, guest firewall integration, trusted LAN access, narrow outbound LAN exceptions |
| Hardware | USB serial discovery, selected stable aliases, native Incus attachment guidance, existing UUID filesystems/ZFS datasets |
| Native tools | Enabled Incus/Cockpit/Portainer links, upstream credits/docs, all-project read-only guest IPs, pinned Community Scripts Incus CLI wizard, explicit verification labels |
| Ownership | Ordinary native/helper resources remain native; declared infrastructure drift is refused before preseed |
| Recovery | Atomic generations, per-group downtime, shared-volume grouping, maintenance exclusions, independent restic, archive recovery |

The portal has no guest lifecycle/console/snapshot editor, application store or
arbitrary script runner. Legacy guest CLI/API actions remain for compatibility.
Docker and metrics history stay optional. NixLab does not ship
application-specific guests or install recipes as core features.

Source checks/Nix evaluation are distinct from live evidence. CI includes a real
container/firewall/device-attachment/restore test and builds the helper runner.
Simulated character devices do not certify physical USB. Community Scripts
qualification targets remain unverified until live install/restore evidence exists.

Before deployment, finish [commissioning](commissioning.md): actual installation,
installation/reboot, physical Ethernet/VLAN access, management-loss rollback, hardware
reconnect/reboot and an independent off-host restore. Cluster HA, automatic
Proxmox conversion, graphical disk erasure, GPU/PCI qualification and PBS-format
compatibility remain separate scopes.

## Delivery gates

The [existing roadmap](https://github.com/OmarDardery/nixlab/issues/19) remains
the delivery authority: qualify the stable single-host beta first, then daily
operations, complete single-node workflows, and finally optional multi-node
capabilities. Missing physical, real-VM or independent-recovery evidence blocks
the corresponding exit. Priorities below do not bypass those gates.

These entries extend existing typed configuration, reviewed transactions and
native tooling. Networking/HTTPS (#14), generic deployment patterns, secret
rotation and multi-host inventory already have roadmap coverage; their entries
below describe incremental capabilities rather than replacement implementations.

## Declarative Lab Inventory — P1 · Core

Prerequisites: qualified single-host release; existing typed configuration and
ownership safeguards.

Extend existing Nix configuration into a portable description of the lab.

- Connect guests, roles, resources, dependencies and service metadata through logical references.
- Separate portable definitions from host-specific network, disk and device assignments.
- Generate versioned inventory exports without another configuration database.
- Retain explicit adoption safeguards and native Incus ownership of undeclared resources.

## GitOps & Reviewed Updates — P1 · Optional

Prerequisites: inventory references; existing reviewed configuration transactions.

Make configuration and workload updates reviewable, pinned and traceable to source revisions.

- Support optional user-owned Git configuration without requiring Git for boot or recovery.
- Connect commit diffs and update proposals to existing build/review/activate/confirm operations.
- Record revision, deployment outcome and resulting system closure together.
- Reuse image pinning and guest deployment; delegate Portainer-owned stacks to Portainer and distinguish configuration rollback from data recovery.

## NixOS Guest Templates & Roles — P1 · Optional

Prerequisites: inventory references; existing native NixOS module interfaces.

Provide composable infrastructure roles using ordinary NixOS modules.

- Extract reusable functionality from the existing applications/Docker guest.
- Add base-server, reverse-proxy, VPN, monitoring and storage-client roles incrementally.
- Define typed inputs and explicit networking, persistence and secret requirements.
- Publish pinned, tested examples that remain editable native Nix configurations.

## Secrets UX — P1/P2 · Optional

Prerequisites: existing runtime-secret/SOPS support; independently recoverable keys.

Extend existing secret handling with safe assignment and recipient-management workflows.

- Provide named references and host/service assignments without displaying plaintext.
- Support ciphertext-first creation/import through SOPS while retaining runtime-file-only configurations.
- Stage recipient rotation with decryptability verification and recovery before retiring old recipients.
- Validate permissions and prevent known-secret leakage into generated configuration, logs and exports.

## Service Publishing — P2 · Optional · Extend #14

Prerequisites: existing networking/access workflow; inventory and secret references.

Extend planned DNS, proxy and HTTPS workflows with an owned, resumable publication lifecycle.

- Add explicitly enabled DNS-provider adapters with scoped credentials and exact change previews.
- Track partial completion across DNS, routing and certificates.
- Restrict removal and rollback to resources NixLab owns.
- Verify published endpoints before reporting success; public exposure remains opt-in and excludes management services.

## Multi-Host Fleet Control — P2 · Optional · Extend #19

Prerequisites: existing Milestone 3 exit criteria; inventory, roles, reviewed
deployments and secret-recipient workflows.

Coordinate independent NixLab hosts without making their operation depend on a controller.

- Add explicit enrollment, per-host configuration and scoped deployment authority.
- Coordinate staged deployments with offline and partial-failure reporting.
- Support opt-in trusted remote Nix builders with explicit architecture and trust requirements.
- Reuse maintained deployment tools and native Incus interfaces; defer HA, quorum, distributed storage and live migration to separate Milestone 4 qualification.

## Application Blueprints — P2 · Optional · Extend generic patterns

Prerequisites: guest roles; inventory references; reviewed configuration updates.

Give generic NixOS and Compose templates a versioned, editable lifecycle.

- Consume user-selected versioned templates and export ordinary Nix or Compose files.
- Include persistence, secret-reference, ownership and compatibility requirements.
- Preserve provenance and propose upgrade patches without overwriting local edits.
- Reuse existing deployment mechanisms and Portainer; avoid another template language or application store.

## Energy-Aware Fleet Policies — P3 · Stretch

Prerequisites: independent-host fleet control; existing #18 power capabilities;
dependency metadata.

Use supported power observations to schedule noncritical workloads safely.

- Show available power measurements and capabilities, including unknown or stale states.
- Offer opt-in start/stop policies that respect critical services, dependencies and backups.
- Preview effects and validate available wake mechanisms.
- Measure energy savings and availability impact without implying migration or HA.

## Delivery and validation

Sequence inventory and role contracts before GitOps/secrets, then publishing and
blueprint extensions, independent-host fleet control and finally energy policies.
Keep later features behind the existing milestone exits. Reuse existing APIs,
typed options and transaction records; add no competing module loader or mandatory
daemon. Preserve custom modules, native resources, identities, disks and secrets.

Each implementation needs focused acceptance criteria and a rollback procedure.
Test inventory round trips and adoption refusal; role composition; interrupted
updates and recipient rotation; publication ownership/partial failure; and fleet
operation with unreachable hosts or an unavailable controller. Keep evidence tied
to exact revisions. Builds, simulations and backups do not establish physical
qualification or successful restoration.
