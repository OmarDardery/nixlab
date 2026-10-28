{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/virtualization.nix ../../modules/incus.nix ../../modules/instance-schema.nix ];
}
