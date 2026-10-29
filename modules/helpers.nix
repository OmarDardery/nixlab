{ lib, pkgs, site, ... }:
{
  environment.systemPackages = lib.optional site.helpers.enable (import ../helpers/package.nix { inherit pkgs; });
}
