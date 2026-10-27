# First switch adds only setup/recovery services to the installed base.
{ lib, site, ... }:
{
  imports = [ ./modules/installed-base.nix ./modules/admin-ui.nix ];
  boot.kernelModules = [ (if site.cpuVendor == "amd" then "kvm-amd" else "kvm-intel") ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  system.stateVersion = lib.mkDefault "26.05";
}
