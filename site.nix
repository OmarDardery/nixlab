# Nix evaluates the source; the setup UI edits only the typed JSON overlay.
let
  machine = import ./lib/machine.nix ./.;
  defaults = import ./site-defaults.nix;
  override = if builtins.pathExists machine.settings
    then builtins.fromJSON (builtins.readFile machine.settings) else { };
  merge = a: b: a // builtins.mapAttrs (key: value:
    if builtins.isAttrs value && builtins.hasAttr key a && builtins.isAttrs a.${key}
    then merge a.${key} value else value) b;
in merge defaults override
