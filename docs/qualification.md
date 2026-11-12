# Single-host release qualification

NixLab is an unqualified single-host beta candidate. No physical target is qualified by adding a
test, enabling a workflow, or completing source-level checks.

| Target | Fresh installation | Upgrade | Physical restore evidence |
|---|---|---|---|
| x86_64 single-host foundation | Unverified | Unverified | Not submitted |
| x86_64 with applications VM | Unverified | Unverified | Not submitted |

This matrix deliberately contains no fabricated hardware results. A qualification
applies to a specific source/input revision, target fingerprint and core/apps
profile. Helper applications and physical USB/PCI devices need their own evidence.

## Evidence and publication

1. Complete the [commissioning procedures](commissioning.md) on a recoverable
   reference host. Collect at the initial healthy boot and after two deliberate
   reboots. Record installation retry, remote authentication, network-loss recovery
   and an off-host application restore as manual attestations.
2. Export the strict-schema report. Inspect it before sharing. It contains source,
   input/settings/content digests, current system closure, kernel/Incus versions,
   hashed target and boot identities, results and timestamps. It excludes raw
   serials, MAC/IP addresses, machine-id, settings, credentials and command output.
   Fingerprints are pseudonymous and can still correlate reports from one host.
3. On a workstation, generate a matrix for an **exact** release commit:

   ```sh
   PYTHONPATH=admin:scripts python3 admin/commissioning.py matrix \
     --revision FULL_40_CHARACTER_COMMIT_SHA fresh-report.json upgrade-report.json
   ```

4. Review the reports and check scope, age, source/content identity and actual trial
   results. Submit a PR updating this matrix and linking reviewed evidence and the
   corresponding successful CI run. Describe relevant hardware by model without
   publishing serials. Publication uses the ordinary documentation/release review;
   this command never publishes a release or changes a tag.

`ready-for-review` means all required results are current and passed on an observed
physical x86_64 installed host. Some results are explicitly operator attestations;
this is not an independently verified or tamper-proof hardware certification.
`unqualified` includes missing, stale, failed, skipped or unsupported required
checks, another source revision and simulated/virtual environments. Reports expire
after 30 days. A changed source, lock file, effective settings, OS closure, kernel,
Incus version, detected physical devices or target invalidates prior evidence.

The export refreshes its live context, so it cannot silently export an old green
status after the installed system changes. An old exported file is a historical
snapshot, not live monitoring; compare it with the candidate release before reuse.
Raw source/settings are intentionally not embedded: maintainers must separately
review the corresponding configuration to determine how broadly a result applies.

## Optional real applications VM test

Run **Real apps commissioning** in GitHub Actions on the revision under review.
It requires the [dedicated disposable physical runner](kvm-qualification.md) with working KVM nesting. The resulting `nested-apps-evidence`
artifact records the actual source revision and resolved OCI image digest. The
machine-readable report remains `nested-integration`, even if every automated
probe passes. A partial artifact from a failed run is not successful evidence.

The test imports the production host modules, builds the production apps image
with the staged Portainer archive, uses Incus image import/reconciliation and
onboarding, then verifies real Portainer authentication before and after two host
reboots. Registry access happens on the runner; no registry is needed inside the
nested guest. No production machine or existing user's data is used.

The routine validation suite continues to test controlled management backends,
actual containers and independent Docker forwarding. Those checks retain their
individual scopes; a successful container test does not qualify the apps VM.

The real apps deployment lane also requires three independent successful trials
on the exact candidate revision; see the dedicated runner procedure. Neither lane
replaces physical installation, upgrade, management recovery or off-host restore.
The beta minimum remains two deliberate physical reboots. Ten successful reboots
and fourteen days of representative operation are reported targets, not mandatory
beta gates. Report actual counts/duration and any unexplained failure.

## Source-only beta publication

The `Publish qualified single-host beta` workflow prepares a source archive,
checksums, provenance manifest, qualification matrix and reviewed limitations.
It does not publish an ISO or qualify a host by building an archive. It rejects
stable/RC tags; the first intended beta is `v0.1.0-beta.1`. `VERSION` identifies the current `0.1.0-beta.1` candidate. This source version
does not publish a release or satisfy any qualification gate.

Prepare and review a candidate version commit first. Tag that exact source, run
all acceptance workflows on it, and collect fresh/upgrade physical apps-profile
reports on one reference target plus independent full-coverage recovery evidence.
Do not change candidate source while collecting evidence. Store sanitized reports
in a separate reviewed evidence commit so adding reports does not change the
candidate SHA they identify:

- `qualification/fresh.json` and `upgrade.json`: strict commissioning exports,
  current for the candidate version, source and pinned inputs, with all mandatory
  checks passed. Simulated, stale, skipped or other-revision reports are rejected.
- `qualification/recovery.json`: redacted `nixlab-admin recovery export-report` after a
  completed data-verified independent recovery, with full coverage, source/input
  identity, attestation time and measured duration. Selected-data, future-dated
  or older-than-30-day recovery evidence is insufficient. Operational `status`
  includes private mappings and should not be committed as public evidence.
- `qualification/limitations.md`: tested hardware/software and storage/network
  compatibility; backup coverage and measured recovery procedure; actual reboot
  count and representative-operation duration; remaining limitations and evidence
  links. Ten reboots/fourteen days are reported targets, not mandatory beta gates.

Reports include operator attestations; maintainers must review actual supporting
records. Passing schema validation is not independent proof of physical activity.
Keep sensitive source inventories, keys, addresses and application data out of
public evidence. Use the existing commissioning redaction/export mechanism.

Record successful exact-source run IDs for `Validate Nixlab`, installer, portal,
three-trial dedicated real apps deployment, real apps commissioning, and Website
**publication**. The workflow verifies all jobs and eligible steps passed; a PR
website build with a skipped deployment does not satisfy publication. The
conditional tag-version step on an ordinary branch is checked separately against
the release tag and VERSION. No skipped integration is accepted.

When all these gates are genuinely satisfied:

```sh
gh workflow run release.yml --repo OmarDardery/nixlab --ref main \
  -f tag=v0.1.0-beta.1 -f evidence_commit=FULL_EVIDENCE_COMMIT_SHA \
  -f runs='{"validate":RUN_ID,"installer":RUN_ID,"portal":RUN_ID,"apps":RUN_ID,"commissioning":RUN_ID,"website":RUN_ID}'
```

Replace every `RUN_ID` with an integer and the evidence SHA with forty hex digits.
The qualification job is read-only; only the downstream publishing job receives
contents-write permission. It downloads this run's verified assets, checks
checksums and the tag's commit, uploads a **new draft prerelease**, rechecks the
commit, then publishes. Existing releases are not overwritten. A failed upload
or moved tag leaves the draft unpublished; inspect provenance and uploaded assets
before a deliberate manual recovery. Never delete a real release to obtain green CI.

The workflow has not been commissioned for publication: no physical reports,
independent real recovery, dedicated runner or successful Pages deployment are
currently supplied. It must fail closed until those gates are fulfilled.
