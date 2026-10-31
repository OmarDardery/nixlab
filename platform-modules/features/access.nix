{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/security.nix ../../modules/secrets.nix ];
}
