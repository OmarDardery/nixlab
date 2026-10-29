# Nix evaluates the source; the setup UI edits only the typed JSON overlay.
let
  machine = import ./lib/machine.nix ./.;
  defaults = import ./site-defaults.nix;
  stored = if builtins.pathExists machine.settings
    then builtins.fromJSON (builtins.readFile machine.settings) else { };
  override = if (stored.ready or false) && !((stored.monitoring or { }) ? collection)
    then stored // { monitoring = (stored.monitoring or { }) // { collection = true; }; }
    else stored;
  merge = a: b: a // builtins.mapAttrs (key: value:
    if builtins.isAttrs value && builtins.hasAttr key a && builtins.isAttrs a.${key}
    then merge a.${key} value else value) b;
in merge defaults override
