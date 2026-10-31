{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/networking.nix ];
}
