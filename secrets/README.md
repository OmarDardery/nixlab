Commit encrypted sops YAML only. Never commit age private identities, tunnel plaintext,
password files, database dumps or restic repository keys. Configure recipients for the
host and VM separately; see `docs/deployment.md`. Files here are not valid secret data
until encrypted by the administrator with real recipients.
