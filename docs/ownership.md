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
