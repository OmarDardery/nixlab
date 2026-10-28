{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/radios/wifi.nix ../../modules/radios/bluetooth.nix ];
}
