{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/storage.nix ../../modules/file-disks.nix ../../modules/mount-schema.nix ../../modules/mounts.nix ];
}
