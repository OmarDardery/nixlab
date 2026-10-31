# A checked compatibility schema: the JSON UI and Nix use the same inventory shape.
{ lib, values ? { }, returnType ? false }:
let
  inherit (lib) mkOption types;
  defaults = import ../site-defaults.nix;
  strings = types.attrsOf types.str;
  foundationTypes = import ./foundation-types.nix { inherit lib; };
  instance = types.submodule {
    options = {
      name = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]*"; };
      project = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]*"; default = "lab"; };
      type = mkOption { type = types.enum [ "container" "virtual-machine" ]; default = "container"; };
      image = mkOption { type = types.nullOr types.str; default = null; };
      profiles = mkOption { type = types.listOf types.str; default = [ "base" ]; };
      config = mkOption { type = strings; default = { }; };
      devices = mkOption { type = types.attrsOf strings; default = { }; };
      start = mkOption { type = types.nullOr types.bool; default = null; };
      stop = mkOption { type = types.bool; default = false; };
      manageExistingOnly = mkOption { type = types.bool; default = false; };
    };
  };
  mount = types.submodule {
    options = {
      name = mkOption { type = types.strMatching "[a-z][a-z0-9-]{0,31}"; };
      uuid = mkOption { type = types.strMatching "[A-Za-z0-9-]{4,80}"; };
      path = mkOption { type = types.str; };
      fsType = mkOption { type = types.enum [ "ext4" "xfs" "btrfs" "vfat" "exfat" "ntfs3" ]; };
      role = mkOption { type = types.enum [ "data" "guests" "pool" "backup" ]; };
      required = mkOption { type = types.bool; };
      readOnly = mkOption { type = types.bool; };
    };
  };
  makeType = path: value:
    if builtins.hasAttr path foundationTypes then foundationTypes.${path}
    else if builtins.elem path [ "cockpit.port" "apps.portainerPort" "setup.port" ] then types.port
    else if path == "instances" then types.listOf instance
    else if path == "mounts" then types.listOf mount
    else if path == "tunnel.ingress" then strings
    else if builtins.isAttrs value then types.submodule {
      options = lib.mapAttrs (key: default: mkOption {
        type = makeType (if path == "" then key else path + "." + key) default;
        inherit default;
      }) value;
    }
    else if builtins.isList value then types.listOf types.str
    else if builtins.isBool value then types.bool
    else if builtins.isInt value then types.int
    else types.str;
  checked = (lib.evalModules {
    modules = [ { options.settings = mkOption { type = makeType "" defaults; default = defaults; }; }
      { settings = values; } ];
  }).config.settings;
in if returnType then makeType "" defaults else checked // {
  instances = map (i: lib.filterAttrs (_: value: value != null) i) checked.instances;
}
