# Nix evaluates the source; the setup UI edits only the typed JSON overlay.
let
  defaults = import ./site-defaults.nix;
  override = if builtins.pathExists ./local/settings.json
    then builtins.fromJSON (builtins.readFile ./local/settings.json) else { };
  merge = a: b: a // builtins.mapAttrs (key: value:
    if builtins.isAttrs value && builtins.hasAttr key a && builtins.isAttrs a.${key}
    then merge a.${key} value else value) b;
in merge defaults override
