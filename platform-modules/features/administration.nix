{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/admin-ui.nix ../../modules/apps-onboarding.nix ];
  nixosModules.bootstrap.imports = [ ../../modules/admin-ui.nix ];
}
