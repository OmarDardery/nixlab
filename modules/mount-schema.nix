# Typed projection of the legacy mount inventory. The setup UI remains its writer.
{ lib, site, ... }:
let
  inherit (lib) mkOption types;
  names = map (mount: mount.name) site.mounts;
  mountType = types.submodule {
    options = {
      name = mkOption { type = types.str; };
      uuid = mkOption { type = types.str; };
      path = mkOption { type = types.str; };
      fsType = mkOption { type = types.enum [ "ext4" "xfs" "btrfs" "vfat" "exfat" "ntfs3" ]; };
      role = mkOption { type = types.enum [ "data" "guests" "pool" "backup" ]; };
      required = mkOption { type = types.bool; };
      readOnly = mkOption { type = types.bool; };
    };
  };
in {
  options.nixlab.storage.mounts = mkOption {
    type = types.attrsOf mountType;
    readOnly = true;
    description = "Typed named projection of the legacy site.mounts inventory.";
  };
  config = {
    assertions = [{
      assertion = builtins.length names == builtins.length (lib.unique names);
      message = "Mount names must be unique before converting the legacy mount inventory.";
    }];
    nixlab.storage.mounts = builtins.listToAttrs (map (mount: {
      name = mount.name;
      value = mount;
    }) site.mounts);
  };
}
