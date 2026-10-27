# The installer preserves local .nix modules, users, bootloader and filesystem config.
{ installedBase ? null, ... }:
{
  imports = if installedBase != null then [ installedBase ] else if builtins.pathExists ../installed-system/configuration.nix
    then [ ../installed-system/configuration.nix ] else [ ../hardware-configuration.nix ];
}
