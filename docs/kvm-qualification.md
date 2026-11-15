# Dedicated KVM qualification

The real apps tests require a **disposable physical x86_64 Linux runner**, labelled
`nixlab-kvm`, with working KVM nesting. The topology is physical runner → NixOS
test host → Incus apps VM. This removes the unreliable hosted hypervisor layer;
it does not turn a NixOS test VM into physical installation evidence.

## Commission the runner

Use a dedicated machine with no production data, credentials, network mounts or
access to production management networks. Allow outbound GitHub, Nix cache and
OCI registry access. Recommend 32 GiB RAM and at least 120 GiB free build space.
Install the supported GitHub Actions Linux x64 runner and its dependencies using
the download and checksum instructions in repository **Settings → Actions →
Runners → New self-hosted runner**. Enable VT-x/AMD-V in firmware and nesting in
the host KVM module before registration. Do not change a production host.

Run these checks as the unprivileged runner account:

```sh
test "$(uname -m)" = x86_64
test "$(systemd-detect-virt)" = none
python3 - <<'PY'
import fcntl
with open('/dev/kvm', 'rb', buffering=0) as device:
    assert fcntl.ioctl(device, 0xAE00, 0) == 12
PY
cat /sys/module/kvm_intel/parameters/nested 2>/dev/null || \
  cat /sys/module/kvm_amd/parameters/nested
```

Nesting must report `Y` or `1`. The account must have KVM access. Device presence
and nesting are prerequisites, not proof that an Incus VM boots.

Create the GitHub environment `nixlab-qualification` with required maintainer
review and no production secrets. Permit only reviewed branches/tags. Do not
approve a workflow dispatch until its exact source SHA and workflow are reviewed.
These workflows have no `pull_request` trigger. Do not add one for self-hosted
runners in this public repository.

Register an **ephemeral** runner after reviewing GitHub's setup commands. From
the extracted runner directory, with a maintainer authenticated to `gh`:

```sh
./config.sh --unattended --ephemeral \
  --url https://github.com/OmarDardery/nixlab \
  --token "$(gh api --method POST repos/OmarDardery/nixlab/actions/runners/registration-token --jq .token)" \
  --labels nixlab-kvm --name nixlab-kvm-disposable
./run.sh
```

Use a short-lived registration credential, never a production PAT. Deprovision or
reimage the runner after each job, including failed jobs. Ephemeral registration
alone does **not** erase its filesystem. Runner provisioning/cleanup stays outside
the workflow being tested. The Nix installation action requires its normal Linux
installation prerequisites; prepare these only on the disposable runner.

## Run and assess the candidate

```sh
gh workflow run guest-deployments.yml --repo OmarDardery/nixlab --ref REVIEWED_BRANCH_OR_TAG
gh run list --repo OmarDardery/nixlab --workflow guest-deployments.yml --limit 5
gh run view RUN_ID --repo OmarDardery/nixlab --log-failed
gh run download RUN_ID --repo OmarDardery/nixlab --name apps-rollout-evidence
```

The deployment job runs three independent test derivations, stopping at the first
failure. Each trial boots fresh guest disks from the same source revision and OCI
digest. The run/attempt/trial suffix prevents a previous successful test result
being substituted for a fresh execution, while guest closures can still be cached.
All three trial files must say `passed`, the source/image identity must match the
candidate, and the entire job must succeed. A missing file or partial artifact is
unverified evidence. The job records the physical runner CPU/kernel; compare the
NixOS test logs and pinned inputs for the guest kernel, Incus, QEMU and firmware.

The fixture checks the actual guest agent, real TLS/authenticated Portainer user,
failed health rollback and blocked dependents, successful in-place OS updates,
unchanged UUID/device topology, persisted file contents and Docker volume records,
and persistence/authentication after an apps VM reboot. Production transport
deadlines apply. OS rollback does not promise database/schema rollback.

Register a fresh ephemeral runner, then run host-reboot commissioning:

```sh
gh workflow run commissioning.yml --repo OmarDardery/nixlab --ref REVIEWED_BRANCH_OR_TAG
```

Its report remains `nested-integration` and unqualified for physical installation.
Each workflow run/attempt also changes the commissioning test derivation so a
cached successful test cannot stand in for a new reboot drill. Standard CI
evaluates both fixtures with two trial identifiers and requires different test
derivations; this verifies cache separation, not guest execution. The commissioning
artifact records the run/attempt, source revision and physical runner CPU/kernel
even when a later image or boot phase fails.
The two workflows share a concurrency group to avoid resource contention. Record
tested Intel/AMD hardware separately: one successful CPU does not qualify another.
Before beta publication, repeat both workflows on the resulting release revision
and attach successful run/artifact links to #24 and the qualification matrix.

## Failure and rollback

Inspect the first failing phase and bounded boot diagnostics. Do not retry until
a concrete runner or test change explains the failure; do not extend production
timeouts, skip authentication, or replace the real apps OS with a fixture service.
If the runner is unavailable the job stays queued/unverified, never passed.

These changes affect disposable tests only. Reverting the workflow/fixture commit
restores the previous test topology; it does not modify an installed NixLab host.
No dedicated runner or physical target has yet been qualified by this document.
