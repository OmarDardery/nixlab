{ config, lib, inventory ? import ../site.nix, ... }:
{
  options.nixlab.settings = lib.mkOption {
    type = import ../lib/settings.nix { inherit lib; returnType = true; };
    default = import ../site-defaults.nix;
    description = "Typed host and application inventory, editable through NixOS modules.";
  };
  config = {
    # Per-leaf defaults preserve unrelated UI values when one file overrides a setting.
    nixlab.settings = lib.mapAttrsRecursive (_: value: lib.mkDefault value) inventory;
    _module.args.site = import ../lib/settings.nix { inherit lib; values = config.nixlab.settings; };
    environment.etc."nixlab/effective-settings.json".text = builtins.toJSON config.nixlab.settings;
  };
}
