{ ... }: {
  nixosModules.hypervisor.imports = [
    ../../modules/connectivity.nix
    ../../modules/hardware-support.nix
    ../../modules/helpers.nix
  ];
}
