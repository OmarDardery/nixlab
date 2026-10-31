{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/installed-base.nix ];
  nixosModules.bootstrap.imports = [ ../../modules/installed-base.nix ];
}
