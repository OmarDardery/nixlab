{ config, lib, pkgs, site, ... }:
let
  mounts = site.mounts;
  pools = builtins.filter (m: builtins.elem m.role [ "guests" "pool" ]) mounts;
  guestPaths = map (m: m.path) pools;
  backupPaths = map (m: m.path) (builtins.filter (m: m.role == "backup") mounts);
in {
  fileSystems = builtins.listToAttrs (map (m: {
    name = m.path;
    value = {
      device = "/dev/disk/by-uuid/${m.uuid}"; fsType = m.fsType;
      options = [ "nodev" "nosuid" "nofail" "x-systemd.device-timeout=10s" ] ++ lib.optional m.readOnly "ro";
    };
  }) mounts);
  systemd.targets.nixlab-storage = {
    wantedBy = [ "multi-user.target" ];
    unitConfig.RequiresMountsFor = map (m: m.path) (builtins.filter (m: m.required) mounts);
  };
  systemd.services.incus.unitConfig.RequiresMountsFor = guestPaths;
  systemd.services.incus-preseed = {
    unitConfig.RequiresMountsFor = guestPaths;
    preStart = lib.mkBefore (lib.concatMapStringsSep "\n" (m: ''
      ${pkgs.util-linux}/bin/mountpoint -q ${lib.escapeShellArg m.path}
      ${pkgs.coreutils}/bin/mkdir -p ${lib.escapeShellArg (m.path + "/incus")}
    '') pools);
  };
  virtualisation.incus.preseed.storage_pools = map (m: {
    name = "disk-${m.name}"; driver = "dir"; config.source = m.path + "/incus";
  }) (builtins.filter (m: m.role == "pool") mounts);
  systemd.services.restic-backups-nixlab = lib.mkIf site.backup.enable {
    unitConfig.RequiresMountsFor = backupPaths;
  };
  systemd.tmpfiles.rules = [ "d ${site.backup.stagingDirectory} 0700 root root -" ];
}
