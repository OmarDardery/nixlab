{ config, lib, site, ... }:
{
  assertions = [
    { assertion = builtins.elem site.storage.driver [ "dir" "zfs" ]; message = "Supported pool drivers: dir or an explicitly pre-created ZFS dataset."; }
    { assertion = site.storage.driver != "zfs" || (site.storage.confirmedExistingZfsDataset && site.storage.hostId != "");
      message = "ZFS requires explicit initialization confirmation and a unique networking.hostId."; }
  ];
  boot.zfs.extraPools = lib.mkIf (site.storage.driver == "zfs") [ (builtins.head (lib.splitString "/" site.storage.source)) ];
  systemd.services.incus.after = lib.mkIf (site.storage.driver == "zfs") [ "zfs-import.target" ];
  boot.zfs.forceImportRoot = lib.mkIf (site.storage.driver == "zfs") false;
  boot.supportedFilesystems = lib.mkIf (site.storage.driver == "zfs") [ "zfs" ];
  networking.hostId = lib.mkIf (site.storage.driver == "zfs") site.storage.hostId;
  services.zfs.autoScrub.enable = site.storage.driver == "zfs";
  systemd.tmpfiles.rules = [ "d /var/lib/nixlab 0711 root root -" "d /var/lib/nixlab/exports 0700 root root -" ];
  systemd.services.incus-preseed.preStart = lib.mkIf (site.storage.driver == "zfs") ''
    # Refuse to initialize a missing dataset; do not let Incus create a pool from a disk path.
    ${config.boot.zfs.package}/bin/zfs list ${lib.escapeShellArg site.storage.source}
  '';
}
