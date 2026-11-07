# Single-host release qualification

NixLab remains a release candidate. No physical target is qualified by adding a
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
It requires a runner with working nested KVM. The resulting `nested-apps-evidence`
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
