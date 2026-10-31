# First switch adds only setup/recovery services to the installed base.
{ lib, site, ... }:
{
  imports = [ (import ./lib/platform.nix { inherit lib; }).bootstrap ];
  boot.kernelModules = [ (if site.cpuVendor == "amd" then "kvm-amd" else "kvm-intel") ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  system.stateVersion = lib.mkDefault "26.05";
}
