{ config, lib, site, ... }:
let
  inherit (lib) mkOption types;
  strings = types.attrsOf types.str;
  declared = site.instances ++ map (i: lib.filterAttrs (_: value: value != null) i)
    (builtins.attrValues config.nixlab.incus.instances);
  identities = map (i: "${i.project}/${i.name}") declared;
in {
  options.nixlab.incus.instances = mkOption {
    default = { };
    description = "Source-owned named instances; legacy UI inventory remains supported.";
    type = types.attrsOf (types.submodule ({ name, ... }: {
      options = {
        name = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]*"; default = name; };
        project = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]*"; default = "lab"; };
        type = mkOption { type = types.enum [ "container" "virtual-machine" ]; default = "container"; };
        image = mkOption { type = types.str; };
        profiles = mkOption { type = types.listOf types.str; default = [ "base" ]; };
        config = mkOption { type = strings; default = { }; };
        devices = mkOption { type = types.attrsOf strings; default = { }; };
        start = mkOption { type = types.nullOr types.bool; default = null; };
        stop = mkOption { type = types.bool; default = false; };
      };
    }));
  };
  config.assertions = [
    { assertion = builtins.length identities == builtins.length (lib.unique identities);
      message = "Instance project/name identities must be unique across machine files and legacy inventory."; }
    { assertion = !(builtins.elem "lab/apps" identities);
      message = "lab/apps is reserved for the applications VM."; }
  ];
}
