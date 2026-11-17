# Independent declarative NixLab workspace (foundation only)

This example is a **template**, not a deployed workload. Copy its contents to
`/srv/nixlab/projects/` on a host where that directory is outside the NixLab
OS checkout. It evaluates Nix source only, and does **not** start Incus instances
or rebuild NixOS.

```sh
# After reviewing the source and before allowing a project to be applied:
cd /srv/nixlab/projects
nix flake lock --flake path:$PWD
nix eval --json path:$PWD#nixlabProjects
```

Commit or independently back up **flake.lock** with the source. Never copy a
runtime credential, private age key, plaintext `.env`, Incus volume or mutable
application data into this source tree: the Nix flake source is copied to the Nix
store and may be publicly readable on a multi-user machine. A SOPS-encrypted
`secrets.enc.yaml` is acceptable after ciphertext and recipients are reviewed.

The first project entrypoint is `sample/default.nix`. `modules/**/*.nix` are
loaded automatically in deterministic order, except directories/files prefixed
with an underscore or dot. Other `.nix` files do not import automatically.
Project names and Incus project claims must be unique.

The `scripts/project_workspace.py` validator checks file types, symlinks,
source size and lock presence before a *future* privileged broker evaluates an
uploaded workspace. Running `nix eval` manually on untrusted Nix files is
**not** a security boundary; review and sandbox the input.

**Not yet supported:** browser creation, project revisions, NixLab project
review/apply, Incus Compose and secret injection. These are tracked separately
in issues #135, #133, #134 and #128.
