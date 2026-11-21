# NixLab is deprecated — use [Nixus](https://github.com/OmarDardery/Nixus)

> [!WARNING]
> **This repository is no longer developed.** All NixLab work continues in
> **[OmarDardery/Nixus](https://github.com/OmarDardery/Nixus)**. Do not install or
> update a host from this repository.

Nixus is the successor to NixLab: a NixOS host with Incus, Cockpit and its own
web console. It provides:

- Declarative Incus projects for native containers, VMs and Compose applications.
- Builds from your original Dockerfiles, run offline and deployed with `incus-compose`.
- SOPS/age encrypted secrets.
- Declarative off-host backups.

It has no Docker daemon, Portainer or Arcane.

## Install Nixus

On an installed NixOS server:

```sh
sudo nix --extra-experimental-features 'nix-command flakes' \
  run github:OmarDardery/Nixus#install
```

See the [Nixus documentation](https://github.com/OmarDardery/Nixus/tree/main/docs),
starting with [Install Nixus](https://github.com/OmarDardery/Nixus/blob/main/docs/plain-nixos.md).

## Issues and history

- Open issues were copied to Nixus and are tracked under
  [OmarDardery/Nixus#1](https://github.com/OmarDardery/Nixus/issues/1). Please file
  new issues and pull requests in Nixus.
- The last maintained NixLab code is preserved on the
  [`archive/pre-incus-native-main-2026-11-17`](https://github.com/OmarDardery/nixlab/tree/archive/pre-incus-native-main-2026-11-17)
  branch, for reference only.
- An existing NixLab host keeps its Incus guests, disks and volumes. Take a
  verified off-host backup before moving it to Nixus; see the
  [Nixus recovery guide](https://github.com/OmarDardery/Nixus/blob/main/docs/recovery.md).
