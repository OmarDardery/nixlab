{ config, lib, inventory ? import ../site.nix, ... }:
{
  options.nixlab.settings = lib.mkOption {
    type = import ../lib/settings.nix { inherit lib; returnType = true; };
    default = import ../site-defaults.nix;
    description = "Typed host and application inventory, editable through NixOS modules.";
  };
  config = {
    assertions = let
      ports = [ 22 8080 8443 9100 ]
        ++ lib.optional config.nixlab.settings.setup.enable config.nixlab.settings.setup.port
        ++ lib.optional config.nixlab.settings.cockpit.enable config.nixlab.settings.cockpit.port
        ++ lib.optional (config.nixlab.settings.apps.enable && config.nixlab.settings.setup.enable) config.nixlab.settings.apps.portainerPort;
    in [ { assertion = builtins.length ports == builtins.length (lib.unique ports);
      message = "Management and application listeners must use distinct ports."; } ];
    # Per-leaf defaults preserve unrelated UI values when one file overrides a setting.
    nixlab.settings = lib.mapAttrsRecursive (_: value: lib.mkDefault value) inventory;
    _module.args.site = import ../lib/settings.nix { inherit lib; values = config.nixlab.settings; };
    environment.etc."nixlab/effective-settings.json".text = builtins.toJSON config.nixlab.settings;
  };
}
