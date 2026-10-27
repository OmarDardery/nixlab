{ lib, pkgs, site, ... }:
{
  services.prometheus = {
    enable = true;
    listenAddress = "127.0.0.1";
    retentionTime = "30d";
    exporters.node = {
      enable = true; listenAddress = "127.0.0.1"; openFirewall = false;
      enabledCollectors = [ "systemd" "textfile" ];
      extraFlags = [ "--collector.textfile.directory=/var/lib/nixlab/metrics" ];
    };
    scrapeConfigs = [
      { job_name = "host-and-incus"; static_configs = [ { targets = [ "127.0.0.1:9100" ]; } ]; }
    ] ++ lib.optional site.apps.enable {
      job_name = "apps-vm"; static_configs = [ { targets = [ "${site.guests.appsAddress}:9100" ]; } ];
    };
    rules = [ ''
      groups:
        - name: nixlab
          rules:
            - alert: NixlabGuestMetricsMissing
              expr: up == 0
              for: 10m
              labels: { severity: warning }
            - alert: NixlabDiskAlmostFull
              expr: node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes < 0.1
              for: 10m
              labels: { severity: critical }
            - alert: NixlabBackupStale
              expr: nixlab_backup_enabled == 1 and on() (time() - nixlab_last_backup_success_seconds > 93600)
              for: 10m
              labels: { severity: critical }
            - alert: NixlabServiceFailed
              expr: node_systemd_unit_state{state="failed"} == 1
              for: 5m
              labels: { severity: warning }
    '' ];
  };
  systemd.tmpfiles.rules = [ "d /var/lib/nixlab/metrics 0755 root root -" ];
  systemd.services.nixlab-metrics = {
    description = "Collect Incus API metrics and backup freshness through local sockets";
    after = [ "incus.service" ];
    requires = [ "incus.service" ];
    path = [ pkgs.curl pkgs.coreutils ];
    serviceConfig = { Type = "oneshot"; UMask = "0022"; };
    script = ''
      metrics=/var/lib/nixlab/metrics
      curl --fail --silent --show-error --max-time 30 --unix-socket /var/lib/incus/unix.socket \
        http://localhost/1.0/metrics > "$metrics/incus.tmp"
      mv "$metrics/incus.tmp" "$metrics/incus.prom"
      last=0
      if test -f /var/lib/nixlab/last-backup-success; then
        read -r last < /var/lib/nixlab/last-backup-success
      fi
      printf 'nixlab_last_backup_success_seconds %s\\nnixlab_backup_enabled %s\\n' "$last" '${if site.backup.enable then "1" else "0"}' > "$metrics/backup.tmp"
      mv "$metrics/backup.tmp" "$metrics/backup.prom"
    '';
  };
  systemd.timers.nixlab-metrics = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "2min"; OnUnitActiveSec = "60s"; };
  };
}
