# Update existing NixOS guests

Image imports update future creations. A guest deployment updates the OS on an
existing disk through local authenticated Incus exec; it never recreates the guest.
Each rollout belongs to one immutable host review. Host confirmation establishes
host access only. Deploy the reviewed guests separately after confirming it.

## Declare targets

Use a reviewed machine module. The guest must be source-owned, running NixOS and
reachable through its Incus agent/exec. The target is a built NixOS
`system.build.toplevel`, not an image alias or a browser-supplied command.

```nix
# In your flake, supply workerSystem as a separately evaluated NixOS configuration.
{ workerSystem, ... }: {
  nixlab.incus.instances.worker = {
    image = "local:your-initial-nixos-image";
    start = true;
    config."boot.autostart" = "true";
  };
  nixlab.deployment.targets.worker = {
    system = workerSystem.config.system.build.toplevel;
    dependsOn = [ ];
    healthUnits = [ "my-service.service" ];
    httpChecks = [ "http://127.0.0.1:8080/health" ];
    healthTimeout = 60;
    maxAttempts = 3;
    restartPolicy = "defer";
    rollbackOnFailure = true;
  };
}
```

`workerSystem` must be supplied by your flake/module arguments. Dependencies refer
to deployment keys, not guest names. Independent targets run sequentially; failed
or reboot-pending dependencies block their consumers. At most 16 targets are
accepted. Cycles, missing dependencies, duplicate guests and non-store target paths
fail review.

`healthUnits` checks active systemd services/targets/sockets. Optional HTTP checks
run inside the guest against explicit IPv4 loopback endpoints, require HTTP 200,
discard bodies and do not follow redirects. Do not put credentials in URLs or Nix
source. An active unit does not prove an application login or LAN reachability.

For the built-in apps VM, set `nixlab.deployment.apps.enable = true`. It adds the
`apps` target from the same evaluated guest configuration as the image, checks
`docker-portainer.service`, and disables the older automatic OS deployment in
onboarding. Credential/TLS provisioning stays in onboarding. Before opting in, set
`nixlab.settings.apps.portainerImage` to an image reference with a verified
`@sha256:` digest (or an immutable local `sha256:` image ID with a declared
image archive). Mutable tags and the unpinned example demo are refused in this
mode. Pin images in other Nix-owned guest workloads too. Portainer/helper-owned
stacks retain their native update tooling.

## Review, activate and deploy

```sh
sudo nixlab-admin config review --module workloads.nix=/path/to/reviewed-workloads.nix
sudo nixlab-admin config status
sudo nixlab-admin config plan
sudo nixlab-admin deploy status
sudo nixlab-admin config apply TRANSACTION_ID
# Reconnect and verify the host, then:
sudo nixlab-admin config confirm TRANSACTION_ID
sudo nixlab-admin deploy start TRANSACTION_ID
sudo nixlab-admin deploy status TRANSACTION_ID
```

The Changes page shows the same targets, dependency order, current/target closures,
health scope, reboot policy, per-target attempts and deployment phases. **Deploy
reviewed guests** requires an administrator and host confirmation. Native/non-NixOS
and unreachable guests are reported; deployment never adopts or initializes them.
The host must still run the reviewed generation when a rollout starts or resumes.
An unexpected guest OS generation, changed Incus configuration or replaced identity
requires a new review. A guest unavailable during review must be reviewed again
after it is running and observable.

## Interruption, retry and rollback

The journal is written before transfer, activation, reboot and rollback. Rollouts
hold the existing host-mutation and Incus locks, serializing against scheduled
backups and guest operations. A killed worker or controller reboot leaves its
checkpoint on disk. Guest profile publication and switching also share a guest-side
lock and a 600-second deadline, preventing a lost controller from leaving an
unbounded switch racing later recovery. Once the worker is confirmed stopped,
resume it:

```sh
sudo nixlab-admin deploy resume TRANSACTION_ID
```

Imported store paths can be reused. Before mutation, the worker rechecks ownership,
instance UUID, configuration fingerprint and allowed OS generations. Previous and
target OS closures are retained as guest GC roots. Failed transfers consume the
finite retry budget; they never trigger guest creation. Previously healthy targets
are checked again on resume. Reports and timestamps describe the last run, not
continuous monitoring.

`defer` leaves a VM **reboot-pending** when its kernel, initrd or boot parameters
change. Reboot it in Incus and resume before deploying dependents. `reboot` permits
a graceful reboot and waits up to 120 seconds for the reviewed boot configuration.
It never forces power off. Containers
share the host kernel and do not use the VM reboot check.

Switch/health failure can restore the previous guest OS generation. An interrupted
rollback is completed before another update. Rollback failure remains visible for
local inspection; the restored OS can itself need a reboot. Host OS rollback does
not automatically roll back guest OS generations.

**OS rollback does not undo database schemas, application state, guest disks or
external changes.** Keep tested independent backups and workload recovery procedures.
This is an ordered rollout, not a distributed atomic transaction.

To release a stopped journal's stale deployment lock without changing guest OS or
data:

```sh
sudo nixlab-admin deploy abandon TRANSACTION_ID
```

After abandoning or exhausting retries, inspect the guest and build a new review.
Journals follow configuration-history retention. Guest GC roots stay until an
administrator reviews/removes them inside the guest; retain them during recovery.

Native Incus administrators can bypass NixLab locks. Avoid concurrent guest edits
during rollout; the precondition checks do not create a distributed transaction.
