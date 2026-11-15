# Credential lifecycle and output contract

Audit base: `93b575369bff928856320c3d9f227784cf3e43c5` (2026-11-15).
This records current code and required boundaries. It does **not** certify that
NixLab currently meets the complete contract. Open defects below block that claim.

## Required contract

Installation, update and recovery output may contain service URLs without userinfo
or tokens, public certificate fingerprints, transaction IDs, status and next steps.
Passwords, bearer tokens, session cookies, private keys and plaintext recovery
material must not enter CLI stdout/stderr, journals, audit events, job metadata,
URLs, process arguments, Nix expressions/store outputs, Git or diagnostic bundles.
Password hashes are sensitive account material even though sentinel plaintext
tests cannot detect them; keep their files and exports protected too.

Secret inputs belong in protected request bodies, stdin or native credential files.
Do not put them in command arguments or environment variables merely to hide them
from a transcript. Runtime files must have a protected ancestry and deliberate
permissions; a mode on the leaf alone is insufficient. Sops source remains
encrypted; its decryption key and decrypted outputs remain outside the Nix store.

Private delivery is an explicit exception to the *public-artifact* boundary:

- An authenticated TLS response may set the session cookie. It must use Secure,
  HttpOnly and SameSite restrictions; never copy that header into diagnostics.
- An explicitly requested, authenticated certificate download may deliver its
  client private material. It needs reauthentication, a narrow scope, bounded
  validity, revocation and a no-store response. Current issuance is incomplete.
- Root-only runtime files and systemd LoadCredential may deliver credentials to
  their intended service. Application credentials exposed inside the intended
  guest/application trust boundary are not host administrator credentials.
- An explicitly requested encrypted secret recovery kit may carry secrets to the
  approved age recipient. This is a different artifact from a redacted support
  bundle. Configuration-only exports must exclude secret material.

Do not remove the last working credential-delivery or console recovery route
before its replacement is available and tested. Recovery must work with the apps
VM, Portainer and other application services stopped.

## Current producer-to-consumer inventory

Paths below are defaults; source-managed secret paths and sops settings may
override them. Secret *contents* are never inventory fields.

| Credential | Producer and consumer | Persistence and current disposal | Delivery and assessment |
|---|---|---|---|
| Initial portal password | `installer/install.py:setup_password`; `runtime.password_record`, `accounts.matches` | `/var/lib/nixlab-admin/setup-password` plaintext plus salted PBKDF2 `auth.json`; CLI `reset-password` removes setup-password, ordinary `accounts.change_password` does not | `install.summarize` reports the root-only legacy file path; new console-approved browser enrollment sets a supplied password. Complete first-claim/recovery qualification in #63/#64 |
| Portal accounts | `admin/accounts.py:update/change_password`; `main.Auth` | `auth.json` and `users.json`, root-owned/group-readable by nixlab-ui; account hash/role changes invalidate existing sessions on lookup | Supplied in authenticated TLS bodies or local getpass; list API returns names/roles; account changes require reauthentication |
| Portal session/CSRF | `admin/main.py:Auth.login/get`, HTTPS frontend | Bounded in-memory sessions; idle and absolute expiry, logout/password/identity changes revoke; web restart discards memory | Cookie and authenticated CSRF response are private protocol delivery; never support-bundle/log fields; tests must inspect public artifacts separately from Set-Cookie |
| Linux operator password/hash | `admin/local_access.py:ensure_operator_password/operator_password`; NixOS user configuration | `/var/lib/nixlab-admin/operator-password` plaintext, `/var/lib/nixlab-secrets/cockpit-operator-password` hash; shadow is live account authority; worker recovery preserves old hash | `install.summarize` reports the protected runtime file path and console password-change command; supplied-password/approval recovery belongs to #67; current retained plaintext requires retirement |
| Apps Portainer bootstrap password | `installer/direct.py:activate`, `modules/apps-onboarding.nix`, `modules/docker-host.nix` | Host `/var/lib/nixlab-secrets/apps-portainer-password`, guest `/var/lib/nixlab-secrets/portainer-admin-password`; mounted into Portainer with admin-password-file; retained after initialization | Host Incus file push uses mode 0600; `portainer.credentials` verifies privately; `summarize` reports status and protected file path only: #62/#65; existing Portainer database must remain authoritative |
| Portainer reset result | `admin/portainer.py:reset`; root `main.py` portainer action | Guest data backup under `/var/lib/nixlab/portainer-recovery/TIMESTAMP`; host `portainer-reset-TIMESTAMP.txt` retains helper stdout plus current credential file/account metadata | Helper output is captured, but successful CLI returns only URL, backup and protected credential file path; #66 must accept a supplied password without exposing it; helper image `latest` also requires #58 |
| Incus browser private key/P12 passphrase | `controller.Manager.browser_certificate`; Incus trust API and browser | Root-only /run temporary directory is removed; owned public trust metadata persists; ECDSA P-384 client identity lasts 90 days, with guarded owned-trust expiry | Supplied import passphrase; metadata-only public JSON plus session-bound, CSRF-protected, one-claim encrypted download for 120 seconds. Reauthenticated issue/revoke, lab restriction by default, explicit acknowledged unrestricted action, owned inventory and renewal; real client import/selection qualification remains #68 evidence |
| Management CA/server/app TLS keys | `runtime.issue_tls`, `diagnostics.identity`, `main.renew` | `/var/lib/nixlab-admin/{ca,tls,apps-tls}.key`; host CA retained, leaf renewal; worker recovery retains prior host TLS files | Portal uses systemd LoadCredential; Incus bind mounts host leaf files; apps leaf copied into guest; public CA/fingerprint are safe, private CA never distributed to guests |
| Transaction secret inputs | `controller.review`, `runtime.encode_secrets`, `worker.apply/save_recovery/rollback` | Root-only `jobs/ID/secrets.json`, then fixed live paths; terminal cleanup retries confirmed/rolled-back/cancelled/superseded; failed/resumable jobs and recovery manifests retained | Separate from candidate source/job metadata; see [staged transaction lifetime](staged-transaction-secrets.md) for enforced admission/expiry, protected publication and visible cleanup failures; rollback material is not generic staging |
| Wi-Fi, restic environment/password, tunnel credential JSON | `runtime.encode_secrets`, fixed `runtime.SECRET_PATHS`, or `modules/secrets.nix` sops | Host `/var/lib/nixlab-secrets`; restic/tunnel paths source-managed; sops age key defaults `/var/lib/sops-nix/key.txt` | Intended services read files; password hashing uses stdin, environment-file values are quoted; command/error and generated-unit audits remain required |
| Failure webhook URL | `modules/notifications.nix`, `scripts/notify.py:send` | Root-owned `/var/lib/nixlab-secrets/notification-url` by default; source declares only its path | systemd LoadCredential; delivery failure withholds URL/response. URLs may contain bearer credentials and must not appear in public status; Alertmanager routing in #53 must reuse this boundary |
| Demo database credential | `modules/apps-onboarding.nix`, `modules/guest-secrets.nix`, Compose consumer | Guest `/var/lib/nixlab-secrets/demo-db-password`, intentionally readable by its application container via secret mount | Application-level credential, not host login; protect host/guest parent directories and exclude from public artifacts; optional demo only |
| Recovery secrets | `admin/recovery.py:secret_files/export/download` | Checks file/ancestry trust, packages separate age-encrypted secret archive; temporary download directory cleaned | Configuration archive separate from encrypted secret kit; export API requires administrator reauthentication. New support bundles in #72 must not reuse secret export as a redacted bundle |

## Current disclosure and lifetime defects

| Finding | Exact code boundary | Required follow-up |
|---|---|---|
| Legacy credentials still retained for protected local delivery | `installer/install.py:summarize` | Output now contains URLs, fingerprints, status and protected file paths only; #62 still needs retirement of legacy files after replacement flows qualify |
| Generated reset password and helper output retained privately | `admin/main.py` portainer action; `admin/portainer.py:reset` | #66 supplied-password reset; preserve backed-up database/restart recovery and never log helper stdout |
| Long-lived Linux/setup plaintext | `local_access.ensure_operator_password`; `install.setup_password`; `accounts.change_password` | #64/#67 retirement with compatibility for existing accounts; never delete the last viable recovery credential |
| Browser client qualification still required | `browser_certificates.py`, protected binary download, client import stores | #68 implements ownership, scope, reauthentication, renewal and revocation; retain the issue until matching Incus CI and real browser import/selection/expiry/revoke evidence qualify the supported clients |
| Broader operation/deployment and service-journal qualification remains | `admin/operations.py`, `admin/deployments.py`, generated units | Broker/frontend errors, host command errors, installer failures and transaction worker records now use fixed messages. Other producers and real service journals still require negative captures |
| Staged credentials/recovery material can outlive useful purpose | `runtime.cleanup_terminal_secrets`; controller/worker terminal transitions | #73 explicit state/lifetime table, expiry and crash/deletion-failure visibility, preserving indispensable recovery material |
| Some secret/application files deliberately enter recovery exports | `admin/recovery.py:secret_files/export` | Keep encrypted recovery export separate from #72 diagnostics; redaction cannot be inferred from archive name |

These findings are defects, not compatibility promises. The output-boundary hardening
changes no authentication authority, persisted schema, state path or recovery material.
Rollback requires no production data migration.

Broker/frontend errors suppress arbitrary exception text. Only explicitly typed
`runtime.PublicError` messages may pass through; construct these from reviewed fixed
literals, never input, paths, child output or another exception. Transaction workers
suppress exception chaining before the systemd entry point. Request-thread failure
handlers suppress the stdlib traceback. Host command child output is discarded rather
than copied into public transaction logs. These choices reduce diagnostic detail; use
status, exit codes and the local recovery procedures when investigating failures.
Legacy transaction logs are projected onto a fixed progress-message vocabulary before
portal or installer delivery, and legacy error fields are replaced on portal reads.
This does not scrub historical journals or private files already created by older code.

## Reproducible tests and evidence limits

`tests/credential_fixtures.py` provides distinct synthetic setup, Linux, Portainer,
P12, cookie, staged-secret and private-key values. `assert_public_clean` recursively
checks mapping keys/values, lists and byte/text artifacts, including URL, base64 and
hex forms. Failure messages name credential categories only. It is a test utility,
not a general-purpose production redactor or proof against every encoding/hash.

`tests/test_credential_contract.py` checks the scanner itself and actual safe
boundaries: failed Portainer subprocess output, rejected secret input, account
metadata/failed login/audit output and cancelled staged secrets. Existing
`tests/test_setup.py` covers HTTPS auth/CSRF, staging exclusion and rollback;
`tests/test_completion.py` covers secret/configuration export separation;
`tests/test_installer_workflow.py` covers verified Portainer access and reset
recovery; `tests/test_clock_readiness.py` checks no partial trust mutation on an
invalid clock. Run with `python3 -m unittest discover -s tests -v` in the supported
CI environment. The repository uses unittest, not pytest or node --test, for these
Python cases; UI tests run the existing explicit Node scripts.

Installer/reset success now has sentinel coverage and status-only output; protected
private credential files remain in place for local recovery. #61 cannot
be marked fully accepted until #62–#68/#73/#74 add negative and successful-path
captures for their final flows, including unauthenticated enrollment, failed or
interrupted bootstrap, aborted/rebooted workers, browser certificate failures,
CLI reset and redacted bundle export. Capture stdout/stderr, response bodies,
audit/job records, subprocess arguments, generated units/config and CI artifacts;
exclude deliberately private delivery only through explicit access/lifetime tests.
Never upload real host secrets to CI for this test.

Pure Nix evaluation and supported VM tests must inspect generated artifacts; no
local Python test proves absence from the Nix store or journals. Full release
qualification still requires its executable evidence contract and matching CI.

### Staged host transaction credentials

The job record stores reviewed secret field names. Transactions without secret
changes need no staging file. All installer/controller paths use one protected,
no-overwrite staging helper. Both activation entrypoints enforce the original
24-hour review deadline. The existing locked boot/periodic guard removes partial
publication files, cancels safely expired work, retries terminal cleanup and
reports unknown state/access failures without deleting recovery evidence.

See [the complete state-transition table and location audit](staged-transaction-secrets.md).
Wall-clock uncertainty fails closed; pending or unknown recovery remains protected
and requires explicit recovery rather than speculative deletion. Real reboot and
non-root permission qualification is encoded in the portal VM fixture and requires
matching NixOS CI evidence.

### Output-boundary hardening evidence

Additional synthetic captures cover first-run and legacy installer summaries, installer
child-command failures, successful Portainer reset public results and private file mode,
failed reset plus failed restart, unauthenticated enrollment error responses, hostile
broker ValueErrors, transaction failure records/journal exceptions, child log routing,
and legacy transaction output. The reset helper still generates and retains private
credentials; supplied-password reset and lifecycle deletion remain #66/#65 work.

A complete #61 acceptance claim remains blocked on the final #62–#68 flows, diagnostics
bundle #72, and real NixOS/generated-unit/journal evidence. Passing the public-boundary
regression tests alone must not close the issue.
