{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/virtualization.nix ../../modules/incus.nix ];
}
