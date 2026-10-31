{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/cockpit.nix ];
}
