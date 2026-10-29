{ lib, pkgs, site, ... }:
let
  admin = import ../admin/package.nix { inherit pkgs; };
  backup = pkgs.writeShellApplication {
    name = "nixlab-export";
    runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client ];
    text = ''PYTHONPATH=${../scripts} exec python3 ${../scripts/backup.py} "$@"'';
  };
  prepare = pkgs.writeShellApplication {
    name = "nixlab-backup-prepare";
    runtimeInputs = [ pkgs.util-linux pkgs.coreutils ];
    text = ''
      mkdir -p /var/lib/nixlab-admin
      exec 9>/var/lib/nixlab-admin/mutation.lock
      flock 9
      ${backup}/bin/nixlab-export --with-recovery
    '';
  };
in {
  assertions = [ { assertion = !site.backup.enable || site.backup.repository != "";
    message = "Set an independent restic repository before enabling backups."; } ];
  services.restic.backups.nixlab = lib.mkIf site.backup.enable {
    repository = site.backup.repository;
    passwordFile = site.backup.passwordFile;
    environmentFile = site.backup.environmentFile;
    initialize = site.backup.initialize;
    paths = [ site.backup.stagingDirectory ];
    exclude = [ "${site.backup.stagingDirectory}/.incomplete" ];
    backupPrepareCommand = "${prepare}/bin/nixlab-backup-prepare";
    backupCleanupCommand = "${backup}/bin/nixlab-export --recover";
    timerConfig = { OnCalendar = site.backup.schedule; Persistent = true; RandomizedDelaySec = "10min"; };
    pruneOpts = [ "--keep-daily 7" "--keep-weekly 4" "--keep-monthly 6" ];
  };
  systemd.services = {
    restic-backups-nixlab = lib.mkIf site.backup.enable {
      after = [ "incus-preseed.service" "nixlab-instances.service" ];
      requires = [ "incus.service" ];
      environment.NIXLAB_EXPORT_DIRECTORY = site.backup.stagingDirectory;
      environment.NIXLAB_DEMO_BACKUP = if site.apps.enableDemo then "1" else "0";
      environment.NIXLAB_RECOVERY_TOOL = "${admin}/bin/nixlab-admin";
      environment.NIXLAB_BACKUP_POLICY = pkgs.writeText "nixlab-backup-policy.json" (builtins.toJSON {
        inherit (site.backup) groups exclude reserveMiB;
      });
      onFailure = [ "nixlab-backup-failed.service" ];
      serviceConfig = {
        TimeoutStartSec = "12h";
        # Cancellation must leave time for the exporter to restart its group.
        TimeoutStopSec = "10min";
      };
      postStart = ''
        # A deliberately excluded workload must not make the full-host freshness
        # indicator green. Detailed coverage remains in backup-status.json.
        if ${pkgs.python3}/bin/python3 -c 'import json; import sys; sys.exit(json.load(open("/var/lib/nixlab/backup-status.json"))["phase"] != "complete")'; then
          date +%s > /var/lib/nixlab/last-backup-success
        fi
      '';
    };
    nixlab-backup-check = lib.mkIf site.backup.enable {
      description = "Verify independent restic repository integrity";
      environment.RESTIC_REPOSITORY = site.backup.repository;
      environment.RESTIC_PASSWORD_FILE = site.backup.passwordFile;
      serviceConfig = {
        Type = "oneshot"; EnvironmentFile = site.backup.environmentFile;
        ExecStart = "${pkgs.restic}/bin/restic check"; TimeoutStartSec = "12h";
      };
    };
    nixlab-backup-recover = {
      wantedBy = [ "multi-user.target" ];
      after = [ "incus-preseed.service" ];
      requires = [ "incus-preseed.service" ];
      unitConfig.ConditionPathExists = "/var/lib/nixlab/restart-pending.json";
      serviceConfig.Type = "oneshot";
      script = "${backup}/bin/nixlab-export --recover";
    };
    nixlab-backup-failed = lib.mkIf site.backup.enable {
      serviceConfig.Type = "oneshot";
      script = ''${pkgs.util-linux}/bin/logger -p daemon.err -t nixlab "Backup failed: inspect journalctl -u restic-backups-nixlab; verify guest recovery"'';
    };
  };
}
