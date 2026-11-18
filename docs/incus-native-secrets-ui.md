# NixLab Secrets UI — SOPS/age and sops-nix contract

**Status:** implementation specification, not an existing feature. Tracks [#134](https://github.com/OmarDardery/nixlab/issues/134) and the [Incus-native plan](incus-native-plan.md).

## Decision

NixLab has **one Secrets page in the existing Web UI**, not OpenBao, Infisical, Vault, a Docker secret service or a second secrets control plane. SOPS with age encrypts versioned project files; `sops-nix` delivers those files to **NixOS services**. NixLab's privileged broker coordinates reviewed writes and, for non-NixOS Incus OCI workloads, an independently qualified **scoped runtime file** adapter. The Web UI never receives a decryption identity.

**Important boundary:** `sops-nix` is a NixOS activation integration, not a general OCI secret-injection engine. Editing SOPS ciphertext does not automatically restart an OCI guest or activate NixOS. A separate reviewed project apply must propagate the new revision safely.

## Declarative state and ownership

```text
/srv/nixlab/projects/<project>/
  default.nix                   # secret references/consumers only; never values
  secrets.enc.yaml              # SOPS ciphertext (safe for encrypted source sync)
  .sops.yaml                    # approved age recipient policy (no private key)
/var/lib/nixlab/keys/projects.agekey
                                # 0400, root; never Git, Nix store, image or guest
/var/lib/nixlab-admin/          # locked journals, audit, reviewed transactions
/run/nixlab/project-secrets/    # protected temporary runtime delivery (tmpfs)
```

The existing `nixlab.project.secrets.file = "secrets.enc.yaml"` typed field is the *ciphertext path*. Extend the typed project schema with references that contain only key names and consumer identities; the Nix evaluator cannot request plaintext. The source-revision metadata identifies ciphertext SHA-256, recipient-policy identity, names of referenced keys, consuming workloads and a revision/deployment status. **Never** expose ciphertext-derived plaintext values through pure Nix evaluation.

Project directories can be Git-synced using **ciphertext only**. `.sops.yaml` contains public recipients. An approved age identity is generated **once**, atomically, only after the user explicitly chooses generate or restore. A missing or damaged key must block secret operations instead of silently regenerating. Additional recipients/rekeys require a separate reviewed rotation. Root is the common trust boundary: one host identity can decrypt all projects; per-project isolation is enforced by broker authorization, separately restricted runtime files, and workload ownership.

## Single Web UI information architecture

**Projects → `<project>` → Environment & secrets**

| Screen/field | Behavior |
|---|---|
| Variables | Two types: ordinary nonsecret configuration and encrypted secrets. Every key has a declared consumer; no implicit injection into all guests |
| Secrets table | Secret **name**, configured/present state, last changed timestamp, recipient status, consumer(s), drift/error. **Never values** |
| Add/edit/rotate/delete | Masked write-only value input and per-key review. Show affected workloads and restart/rotation impact |
| Import | Parse `.env` locally in a dedicated secrets import workflow; do not publish, persist or display a raw plaintext `.env` as project source |
| Review | Immutable source/ciphertext revision, affected keys (names only), recipient policy, intended delivery targets, independent backup coverage, required reloads |
| Recovery | Identity present/missing, encrypted source synchronized, offline recovery key verified, secret delivery health. No private-key download or plaintext reveal endpoint |

All actual secret mutations require an authenticated admin session, CSRF/origin checks, **fresh password reauthentication**, an explicit acknowledgement and the existing root broker's lock/revision checks. Editors can manage nonsecret project files without receiving the host's age identity. Viewer/operator roles cannot read secret plaintext or perform secret writes.

## Encryption and mutation protocol

1. The Web UI sends the new value only over the authenticated HTTPS mutation path (never as URL/query or GET); clear the DOM input immediately on submission.
2. The broker checks project and target-key authorization, allowed key syntax/size, current ciphertext revision, age recipient policy and installed key provenance. Reject unknown recipient or missing/corrupt identity. No concurrent unreviewed overwrite.
3. The broker may decrypt the existing file **only in private root memory**, update the requested leaf in memory and invoke pinned `sops` using **stdin/stdout pipes**, with secret material excluded from command-line arguments, environment values, subprocess error responses, process logs and temporary persistent files. Do not use `sops set` with the plaintext value as a CLI argument. A constrained alternative is a root-only tmpfs file with deterministic cleanup and strict access checks.
4. Verify the resulting SOPS envelope, public recipients and decryptability with the expected installed identity. Stage encrypted ciphertext in a private transaction; review includes only key names, fingerprints/digests, recipient identity and affected consumers.
5. On reauthenticated approval, atomically publish **ciphertext only** with an immutable revision and audit event, preserving the previous encrypted generation. Do not silently apply it to workloads; a separate reviewed deployment handles runtime changes.
6. A failed, interrupted or stale operation preserves the previous published ciphertext and service configuration. Audit never contains values. No plaintext secret is copied into Docker build context or Nix store.

The UI itself is not the encryption boundary; the trusted broker/age identity and verified, versioned ciphertext are.

## Delivery semantics

- **NixOS host and NixOS guests:** use the existing `sops-nix` integration, `sops.age.keyFile`, `sops.secrets.<name>.sopsFile`, secret `key`/runtime `path`, owner/mode and `restartUnits` / `reloadUnits` where appropriate. Secret values never appear in Nix option strings. Guest private keys, if any, have an explicit guest-owned provenance; **never copy the host master key into a guest**.
- **Incus OCI / Incus Compose:** `sops-nix` does **not** run inside generic OCI images. The NixLab broker decrypts only the approved referenced values for a scoped delivery adapter, into tmpfs/protected files and appropriate guest-owned paths after a verified Incus ownership/identity check. No age private key in the guest. No values in Incus `environment.*`, `user.*`, profiles, device metadata, logs or `incus config show`. Avoid assuming plain environment variables are secret; prefer explicit file-secret consumers. Recheck actual Incus persistence, visibility and cleanup in an integration test before shipping.
- **Original Compose/Dockerfiles:** preserve byte-exact source and relative paths. An original `env_file: .env` or `${VAR}` reference is mapped, where supported, to a generated *runtime-only* protected file; it must never silently rewrite the original Compose source. Fail closed on unsupported `build.secrets`/`build.ssh` and any case that would expose values in image layers, build args or an Incus-visible environment setting.
- **Rotation/restart:** updating ciphertext is not the same as applying it. Show intended service restarts, preserve last healthy deployed revision, and require explicit approval. An independent host restore must include SOPS ciphertext **and** the separately retained age identity.

## Acceptance tests before enabling secret writes

- No plaintext in tracked source, package derivations, Nix store, HTTP GET, browser page/DOM after input clearing, API errors, traces, stdout/stderr, audit, or backup-source snapshots.
- Generate-once identity and explicit restore are repeatable; missing/corrupt or wrong identity **fails closed**; multi-recipient changes and rotations are reviewed.
- Admin-only creation/rotation, CSRF, password reauth, stale revision rejection, different-project access denial, expired session and concurrent writes.
- SOPS decrypt/re-encrypt with special characters, multiline and binary-safe inputs, corrupt envelope, wrong recipient, interrupted write and atomic rollback.
- NixOS service receives the expected scoped runtime secret; no host private key or other project's secret enters its environment.
- Incus OCI service receives a scoped file only where qualified, with no plaintext in Incus API/config/history; reload/rollback and crash cleanup are verified.
- Independent host restore from **encrypted source and a separately protected offline recovery kit**, followed by verified decrypt and service restart. Protect the restic unlock material in the same independently restorable kit without creating a circular dependency.

## Implementation sequence

1. Document the UI/write-only contract and threat boundaries (this document); keep #134 open.
2. Add a metadata-only Secrets panel, stable per-project secret reference schema and read-only broker projection; do not decrypt on GET.
3. Add one-time age identity setup/import with explicit offline recovery and key-provenance checks.
4. Add strictly bounded SOPS create/edit/delete/rotate, immutable ciphertext revisions and separate review/apply through the broker.
5. Wire NixOS `sops-nix` runtime paths and independently qualify Incus OCI/Compose file-based delivery.
6. Test off-host restore, rekey and leak prevention; only then mark the #134 gate complete and permit native project deployment requiring secrets.

Useful upstream references: [sops-nix README](https://github.com/Mic92/sops-nix/blob/master/README.md), [SOPS reference](https://getsops.io/docs/reference/), and [age](https://age-encryption.org/).
