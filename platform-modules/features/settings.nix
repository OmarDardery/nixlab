{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/settings.nix ];
  nixosModules.bootstrap.imports = [ ../../modules/settings.nix ];
  nixosModules.applications.imports = [ ../../modules/settings.nix ];
}
