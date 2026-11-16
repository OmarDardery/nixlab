# Arcane application manager

NixLab runs Arcane in the isolated Incus applications VM. The manager uses `/srv/arcane` for its database and persistent encryption key, and discovers Compose projects in `/srv/compose` at the same absolute container path. The host exposes its HTTPS dashboard only to trusted management networks through a TLS-verified WebSocket-capable proxy.

Arcane has an independent administrator identity. Supply its initial password in the authenticated portal. Local-root recovery uses `sudo nixlab-admin arcane reset-password` at a private terminal and the upstream interactive recovery command; passwords are never command arguments.

The runtime is replaced directly. Existing Docker workloads and unrelated data directories are preserved. No inventory or stack migration tool is required or provided. Backups must include the Arcane database, encryption key, Compose definitions and application volumes.

Upstream: https://getarcane.app/docs/get-started/installation


The official v3.0.1 manager is pinned by its multi-architecture digest. Initial passwords must contain 16–256 characters including uppercase, lowercase, a number and a symbol, preserving Arcane's upstream character requirements. The encryption key is published atomically once at runtime, outside the Nix store, with mode 0600; an invalid or missing backup must be restored rather than rotating a key for an existing database. Keep the key with protected backups of `/srv/arcane`.

When reading older configuration files, NixLab accepts the retired dashboard port as a compatibility alias and drops its old image reference. Only Arcane's new `arcaneImage` and `arcanePort` settings are exposed. This normalization does not export or import application data. Existing data directories are not deleted; Arcane uses its own independent database.
