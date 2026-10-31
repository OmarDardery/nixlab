{ ... }: {
  nixosModules.hypervisor.imports = [ ../../modules/cloudflared.nix ];
}
