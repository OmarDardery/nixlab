{ config, lib, pkgs, site, ... }:
let cfg = config.nixlab.notifications;
in {
  options.nixlab.notifications = {
    enable = lib.mkEnableOption "failure webhook notifications";
    webhookFile = lib.mkOption { type = lib.types.str; default = "/var/lib/nixlab-secrets/notification-url";
      description = "Root-owned runtime file containing an HTTPS webhook URL; never place its value in Nix."; };
  };
  config = lib.mkIf cfg.enable {
    systemd.services."nixlab-notify@" = {
      description = "Send a bounded Nixlab failure notification";
      wants = [ "network-online.target" ]; after = [ "network-online.target" ];
      unitConfig = { StartLimitIntervalSec = 300; StartLimitBurst = 3; };
      serviceConfig = {
        Type = "oneshot"; TimeoutStartSec = "30s"; Restart = "on-failure"; RestartSec = "10s";
        LoadCredential = [ "webhook:${cfg.webhookFile}" ];
        ExecStart = "${pkgs.python3}/bin/python3 ${../scripts/notify.py} %i";
        NoNewPrivileges = true; PrivateTmp = true; ProtectSystem = "strict"; ProtectHome = true;
      };
    };
    systemd.services.nixlab-instances.onFailure = [ "nixlab-notify@%n.service" ];
    systemd.services.nixlab-kvm-check.onFailure = [ "nixlab-notify@%n.service" ];
    systemd.services.nixlab-rollback-guard.onFailure = lib.mkIf site.setup.enable [ "nixlab-notify@%n.service" ];
    systemd.services.restic-backups-nixlab.onFailure = lib.mkIf site.backup.enable [ "nixlab-notify@%n.service" ];
  };
}
