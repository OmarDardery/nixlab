# One owner for each layer

Nixlab is the NixOS host foundation for Incus, not a second virtualization UI.

| Layer | Authoritative owner |
|---|---|
| Installation, physical interfaces, bridges/VLANs, host firewall, mounts, drivers, updates | Reviewed NixOS configuration |
| Ordinary guests, lifecycle, consoles, limits, device attachment, snapshots | Incus UI/API/CLI |
| Additional managed guest networks, DHCP, reservations, forwards, ACLs, pools/volumes | Incus |
| Community Scripts application installation/updates | Upstream Incus wizard/application tooling |
| Optional Docker stacks and their consoles/logs | Portainer or existing declared Compose source |
| Independent backup scheduling, coverage and whole-host recovery | Nixlab |

The portal links to Incus rather than duplicating guest controls. Its restore
workflow handles independent archives; ordinary snapshots stay in Incus. Legacy
guest CLI/API actions remain for compatibility.

The initial `lab` project, `guests` pool, `incusbr0`, `base` profile, optional apps
VM and explicitly declared instances remain source-owned. A preseed guard records
these infrastructure objects and refuses native drift before resetting anything.
The first upgrade also checks declared fields of existing objects. Other Incus
objects are neither recorded nor reconciled by this guard.

Ordinary Incus/helper-created guests are never adopted by an unrelated rebuild.
Their disks/application state require backups. Incus roles and certificates remain
separate from portal accounts.

On a drift failure, inspect the named object and its declaration. Restore the
declared field in Incus or explicitly update the Nix declaration to adopt exactly
the intended native edit, then review/rebuild. Unrelated additions must also be
declared or reverted. Do not discard ownership records to hide conflicts.

## Preview the change

Every newly built transaction includes a versioned **Change impact** plan on the
Changes page. It lists changed units, source-owned infrastructure/guest fields,
observed consumers, required migrations, ownership conflicts and recovery limits.
Expand **Who owns what** for the read-only ownership view. It adds no guest editor
and never adopts native resources merely because they appear in the inventory.

The CLI returns the same public JSON as the portal:

```sh
sudo nixlab-admin config review --module profiles/base.nix=/path/to/reviewed-module.nix
sudo nixlab-admin config status
# After the candidate finishes building:
sudo nixlab-admin config plan
sudo nixlab-admin config apply TRANSACTION_ID
```

Example effects on an observed lab:

| Proposed change | Affected resources | Plan result |
|---|---|---|
| Source-owned `lab/base` profile changes `limits.cpu` from 2 to 4 | `lab/web`, `lab/dns`, including native guests using that profile | Possible restart/live resource change; both consumers listed |
| Remap the UUID backing `/srv/nixlab/ssd` | Pool `guests` and its guest consumers | Migration required; activation blocked |
| Native Incus edit conflicts with a recorded declared field | Named profile, pool, network or declared guest | Ownership conflict; resolution instructions shown |
| Remove a guest declaration | Existing guest and disks | Retained; no deletion |

Backing pools/mounts and an initialized guest subnet still require explicit
migration. Valid proposals can now build a blocked review so the reason and
dependencies are visible. Malformed or physically invalid settings still fail
validation. No setting can bypass the migration gate through the CLI/API.

At build completion, the worker captures a bounded local Incus inventory: at most
256 guests/resources, 16 projects, 2 MiB per response and a 30-second observation
deadline. Missing, partial or inaccessible inventory is shown as **unknown** and
blocks activation; it is never treated as an empty lab. A genuinely uninitialized
Incus host is recorded separately. Unit definitions are compared by content hashes.
Code uploads, platform changes and package refreshes carry an explicit unknown
effects warning; dry-activation output and code review remain necessary.

The broker rechecks runtime preconditions before queueing activation. The worker
checks again after queueing and before publishing source, secrets or OS changes.
A native edit refreshes the plan and refuses that activation attempt. Review the
updated plan before applying again; conflicts/migrations remain blocked on retry.
An intervening host generation/settings change requires a new build. Old pending
candidates without this plan must be rebuilt. A routine native guest start/stop
does not invalidate the configuration plan; explicit declarations can still
request starts/stops, which the plan reports.

Raw snapshots, ETags and evaluated projections remain in root-only transaction
files. Public JSON shows identities, field names and fingerprints, not runtime
configuration values, environment variables or unit contents. It is a snapshot,
not continuous monitoring or a distributed transaction. Native Incus administrators
can bypass NixLab locks; avoid concurrent edits during activation. The native
preseed drift guard and per-instance ETag writes remain in force. Direct
`nixos-rebuild switch` bypasses this preview/recheck workflow.

## Guest field removal

The reconciler records last-applied ownership. Removing a previously managed
config/device key removes that key only when runtime still matches the previous
or desired value. Unowned keys remain untouched. A native edit to a managed key
requires explicit resolution. Removing a whole managed device or changing its
backing/type requires migration; removing a whole guest declaration never deletes
the guest. Profiles on declared guests remain an authoritative ordered list.
Legacy keys without last-applied evidence are not guessed to be owned.
