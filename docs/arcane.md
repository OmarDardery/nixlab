# Arcane application manager

NixLab runs Arcane in the isolated Incus applications VM. The manager uses `/srv/arcane` for its database and persistent encryption key, and discovers Compose projects in `/srv/compose` at the same absolute container path. The host exposes its HTTPS dashboard only to trusted management networks through a TLS-verified WebSocket-capable proxy.

Arcane has an independent administrator identity. Supply its initial password in the authenticated portal. Local-root recovery uses `sudo nixlab-admin arcane reset-password` at a private terminal and the upstream interactive recovery command; passwords are never command arguments.

The runtime is replaced directly. Existing Docker workloads and unrelated data directories are preserved. No inventory or stack migration tool is required or provided. Backups must include the Arcane database, encryption key, Compose definitions and application volumes.

Upstream: https://getarcane.app/docs/get-started/installation


The official v3.0.1 manager is pinned by its multi-architecture digest. Initial passwords must contain 16–256 characters including uppercase, lowercase, a number and a symbol, preserving Arcane's upstream character requirements. The encryption key is published atomically once at runtime, outside the Nix store, with mode 0600; an invalid or missing backup must be restored rather than rotating a key for an existing database. Keep the key with protected backups of `/srv/arcane`.

When reading older configuration files, NixLab accepts the retired dashboard port as a compatibility alias and drops its old image reference. Only Arcane's new `arcaneImage` and `arcanePort` settings are exposed. This normalization does not export or import application data. Existing data directories are not deleted; Arcane uses its own independent database.

First-boot plaintext uses `/run/nixlab-secrets/apps-arcane-password` on the host and `/run/nixlab-arcane/password` in the guest. Both are consumed on handoff/verification and expire after 15 minutes; neither is backed up. Reviewed transaction input is removed when published during activation. Initialized databases start without a bootstrap file; missing/corrupt established state blocks enrollment. SOPS continues managing durable service secrets, but Arcane initial credentials use this transient enrollment flow instead of recurring SOPS decryption.

The Services page offers separate initial passwords by default and explicit opt-in NixLab password reuse after current-password verification. It also offers an independently reauthenticated supplied-password reset for existing Arcane. The guest checkpoints its SQLite database outside `/srv/arcane`, restricts it to root, and records a nonsecret pending marker. Failed resets restore the checkpoint; reboot restores interrupted work before Arcane starts. Docker volumes, Compose definitions and the encryption key are preserved. The checkpoint contains password hashes and must remain private.


## Retirement inventory (read-only)

Arcane and the Docker applications VM are being retired from **new**
installations. Existing installations must keep their workloads intact until
there is an explicit, tested Incus/Incus Compose replacement.

A privileged administrator can inspect the legacy VM without changing its
power state:

```sh
sudo nixlab-admin legacy-apps inventory
```

The report lists `lab/apps` existence, status, disk references and,
**only if already running**, bounded Docker container, Compose stack and named
volume names. It does not export data, inspect environment variables, read
credentials, stop or start the VM, or assert a successful backup. Missing
Docker/guest access is reported as unavailable, **not an empty workload list**.
It reports `safeToDelete=false` regardless of VM presence. Treat names and
disk paths as private operational metadata.

Before migrating, make application-consistent backups of actual Docker named
volumes and databases; export the stopped Incus VM plus separately attached
custom volumes to an encrypted, **off-host** destination. Restore and verify
on an isolated Incus target before any manual decommissioning. Disabling the
legacy apps setting must not imply VM deletion or a successful data migration.
