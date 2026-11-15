# Staged transaction secret lifetime

Host configuration transactions use the existing mutation lock, job IDs and
systemd workers. They do not use a separate secret service. Production callers
must hold `runtime.locked()` when staging, deleting or sweeping credentials.

## Admission and expiry

`runtime.stage_secrets` publishes encoded credentials once into
`/var/lib/nixlab-admin/jobs/ID/secrets.json`. It validates every directory component
with no-follow descriptor lookups, accepts only trusted privileged ownership and
no untrusted directory writers, and creates a mode-0600 file. A root-owned sticky
temporary-directory ancestor is permitted for local tooling/tests. Readers reject
symlinks, hardlinks, special files, unexpected keys, non-private modes, oversized
content and foreign ownership. An existing destination is never overwritten.

The deadline is **24 hours after the job's original `created` Unix timestamp**,
including time spent building. The exact deadline counts as expired. Retrying,
restarting the broker, and rebooting do not extend it. Both the controller and the
activation worker reject expired credentials before activation; a delayed guard
timer does not permit an expired candidate to activate. Transactions without
secret changes have no secret admission deadline.

This is a wall-clock policy. A missing, non-finite, non-positive or future creation
timestamp makes age unknown. Admission fails closed and the sweep preserves the
file with a health finding. Repair the host clock or explicitly discard/review the
unapplied candidate. Clock correction can shorten or lengthen elapsed retention;
the code does not manufacture a new deadline or guess that unknown recovery is
safe to delete.

## State transitions

| State | Committed staged credentials | Recovery evidence |
| --- | --- | --- |
| Reviewed (legacy) / ready | Retain until deadline; then persist `cancelled` before unlink | Retain |
| Building | Retain while the worker is active; after deadline cancel/unlink only with a proven stopped worker | Retain |
| Failed, without pending recovery | After deadline, cancel/unlink only when build, apply and rollback workers are all proven stopped | Retain |
| Applying / awaiting confirmation / rolling back | Preserve while pending recovery owns the transaction; rollback/confirmation performs terminal disposal | Retain |
| Applying / awaiting confirmation / rolling back with no pending pointer | Preserve; after deadline report unresolved recovery rather than infer a terminal state | Retain |
| Confirmed / rolled back / cancelled / superseded / expired (legacy) | Remove immediately; retry any interrupted deletion | Retain according to existing recovery/GC ownership |
| Missing/corrupt record, unknown state/age, unsafe path/file, unknown worker status | Preserve committed material and fail the guard health check | Retain |

Normal confirmation, successful rollback, cancellation and supersession call the
same removal helper. Cancellation cannot discard a failed transaction that still
owns the pending recovery pointer. Public state names and historical job records
remain unchanged; expiry records `cancelled` with a fixed explanation.

## Interrupted writes and recovery

Publication writes a private `.secrets.json-HEX` temporary file, syncs its content,
atomically links it to the final name without replacement, syncs the directory,
then unlinks the temporary name and syncs the directory again. A kill can leave
an unpublished partial file or two names for the committed inode. A reader refuses
the latter until the sweep removes the temporary hardlink.

The existing rollback guard runs 20 seconds after boot and every 15 seconds. Under
the mutation lock it removes recognized publication temporaries, including the
old `.secrets.json` plus eight-character `tempfile` suffix. Those names are never
consumer inputs and can be removed even when pending recovery retains the final
file. No writer can concurrently publish while the guard owns that lock. Other
hidden files and subdirectories are not swept.

Expiry commits the terminal job state **before** unlink. If that write fails the
secret remains. If unlink or directory sync fails, the next guard retries from
the persisted terminal state. Permission, corruption, worker-observation and
deletion failures return fixed actionable errors and fail
`nixlab-rollback-guard.service`, which the existing doctor/health service inventory
reports. The sweep still processes other independent jobs. A staging finding does
not prevent the existing guest-operation restart recovery from running.

Unlinking bounds file reachability; it does not promise physical erasure from
filesystem snapshots, journals, backups or storage media.

## Staging location audit

| Location | Consumer and disposal authority |
| --- | --- |
| `jobs/ID/secrets.json` | Reviewed secret key set; worker activation; terminal/expiry sweep above |
| `jobs/ID/.secrets.json…` | No consumer; interrupted publication only; locked guard/helper removal |
| `jobs/ID/recovery/secret-*`, operator hash, saved NetworkManager credentials and TLS files | Existing rollback restores prior live state; **not** candidate staging. Retain until existing recovery retention/GC permits deletion |
| Fixed `runtime.SECRET_PATHS` and guest runtime credential files | Live service inputs; not staging and never removed by this sweep |
| Installer/bootstrap/update transactions | Use `Manager.review`, hence the same staging helper and expiry authority |
| Guest operation/deployment records and source snapshots | Do not stage host credential inputs; preserve their existing archive, restart and recovery ownership |
| Browser certificate and encrypted recovery-download temporary directories | Separate scoped private delivery; existing context managers own disposal, outside host transaction expiry |

No credential values enter the job/source snapshot, Nix expression/store output,
argv, public progress log or sweep finding. The metadata and recovery paths above
are not a redacted support-bundle allowlist.

## Evidence

`tests/test_secret_cleanup.py` covers expiry boundaries, invalid clocks, both
activation admission checks, pending cancellation refusal, terminal cleanup,
permission/alias rejection, full/read-only storage, failed job-state publication,
failed unlink, retained recovery, and actual SIGKILL before/after publication and
temporary unlink. All credentials are synthetic.

`tests/portal-vm.nix` additionally seeds interrupted writes and every terminal
state, checks that outsider/frontend users cannot read staged material, reboots,
then verifies the production boot timer removes expired/terminal staging and
preserves pending and unexpired credentials plus rollback evidence. This is a
supported NixOS fixture; adding it is not evidence that it has run. Record the
matching CI result before claiming real reboot/permission qualification.
