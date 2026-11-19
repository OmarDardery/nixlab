# Administrator modules

All `.nix` files here are imported recursively by the hypervisor configuration.
Files/directories starting with `_` are disabled. Files are privileged NixOS
configuration, not sandboxed plugins. Do not put secrets here.

Use `path:/etc/nixos#hypervisor` when rebuilding so new, untracked files are included.
See `docs/file-modules.md`. The bootstrap generation does not import this directory.
