{ ... }: {
  nixosModules.applications = { modulesPath, ... }: {
    imports = [ ../../modules/guest-secrets.nix ../../modules/docker-host.nix
      (modulesPath + "/virtualisation/incus-virtual-machine.nix") ];
  };
}
