{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/quality-of-life.nix ];
}
