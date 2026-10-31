{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/monitoring.nix ];
}
