{ config, lib, site, ... }:
let
  inherit (lib) mkOption types;
  disks = builtins.attrValues config.nixlab.storage.disks;
  all = site.mounts ++ disks;
  unique = key: builtins.length all == builtins.length (lib.unique (map (d: d.${key}) all));
in {
  options.nixlab.storage.disks = mkOption {
    default = { };
    description = "Administrator-owned additional existing filesystems; separate from UI-owned legacy mounts.";
    type = types.attrsOf (types.submodule ({ name, ... }: {
      options = {
        name = mkOption { type = types.str; default = name; readOnly = true; };
        uuid = mkOption { type = types.strMatching "[A-Za-z0-9-]{4,80}"; };
        path = mkOption { type = types.str; default = "/srv/nixlab/${name}"; };
        fsType = mkOption { type = types.enum [ "ext4" "xfs" "btrfs" "vfat" "exfat" "ntfs3" ]; };
        role = mkOption { type = types.enum [ "data" "pool" ]; default = "data"; };
        required = mkOption { type = types.bool; default = true; };
        readOnly = mkOption { type = types.bool; default = false; };
      };
    }));
  };
  config.assertions = [
    { assertion = unique "name" && unique "path" && unique "uuid";
      message = "File disks and UI mounts must have distinct names, paths and UUIDs."; }
    { assertion = lib.all (d: builtins.match "[a-z][a-z0-9-]{0,31}" d.name != null && d.path == "/srv/nixlab/${d.name}") disks;
      message = "File disks must mount at /srv/nixlab/<lowercase-name>."; }
    { assertion = lib.all (d: d.role != "pool" || (d.required && !d.readOnly && builtins.elem d.fsType [ "ext4" "xfs" "btrfs" ])) disks;
      message = "Incus pools require writable, required Linux filesystems."; }
  ];
}
