# The installer preserves local .nix modules, users, bootloader and filesystem config.
{ installedBase ? null, ... }:
{
  imports = if installedBase != null then [ installedBase ] else if builtins.pathExists (import ../lib/machine.nix ../.).installed
    then [ (import ../lib/machine.nix ../.).installed ] else [ ../hardware-configuration.nix ];
}
