{ lib, pkgs, site, ... }:
let
  backup = pkgs.writeShellApplication {
    name = "nixlab-export";
    runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client ];
    text = ''exec python3 ${../scripts/backup.py} "$@"'';
  };
in {
  assertions = [ { assertion = !site.backup.enable || site.backup.repository != "";
    message = "Set an independent restic repository before enabling backups."; } ];
  services.restic.backups.nixlab = lib.mkIf site.backup.enable {
    repository = site.backup.repository;
    passwordFile = site.backup.passwordFile;
    environmentFile = site.backup.environmentFile;
    initialize = site.backup.initialize;
    paths = [ "/var/lib/nixlab/exports" ];
    backupPrepareCommand = "${backup}/bin/nixlab-export";
    backupCleanupCommand = "${backup}/bin/nixlab-export --recover";
    timerConfig = { OnCalendar = "*-*-* 03:00:00"; Persistent = true; RandomizedDelaySec = "10min"; };
    pruneOpts = [ "--keep-daily 7" "--keep-weekly 4" "--keep-monthly 6" ];
  };
  systemd.services = lib.mkIf site.backup.enable {
    restic-backups-nixlab = {
      after = [ "incus-preseed.service" "nixlab-instances.service" ];
      requires = [ "incus.service" ];
      environment.NIXLAB_DEMO_BACKUP = if site.apps.enableDemo then "1" else "0";
      onFailure = [ "nixlab-backup-failed.service" ];
      serviceConfig = {
        TimeoutStartSec = "12h";
      };
      postStart = ''
        date +%s > /var/lib/nixlab/last-backup-success
      '';
    };
    nixlab-backup-recover = {
      wantedBy = [ "multi-user.target" ];
      after = [ "incus-preseed.service" ];
      requires = [ "incus-preseed.service" ];
      serviceConfig.Type = "oneshot";
      script = "${backup}/bin/nixlab-export --recover";
    };
    nixlab-backup-failed = {
      serviceConfig.Type = "oneshot";
      script = ''${pkgs.util-linux}/bin/logger -p daemon.err -t nixlab "Backup failed: inspect journalctl -u restic-backups-nixlab; verify guest recovery"'';
    };
  };
}
