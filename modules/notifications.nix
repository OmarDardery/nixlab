{ config, lib, pkgs, site, ... }:
let cfg = config.nixlab.notifications;
in {
  options.nixlab.notifications = {
    enable = lib.mkEnableOption "failure webhook notifications";
    webhookFile = lib.mkOption { type = lib.types.str; default = "/var/lib/nixlab-secrets/notification-url";
      description = "Root-owned runtime file containing an HTTPS webhook URL; never place its value in Nix."; };
  };
  config = lib.mkIf cfg.enable {
    assertions = [ { assertion = lib.hasPrefix "/" cfg.webhookFile && !(lib.hasPrefix "/nix/store/" cfg.webhookFile);
      message = "Notification credentials must come from an absolute runtime file outside the Nix store."; } ];
    environment.etc."nixlab/notification-settings.json".text = builtins.toJSON { inherit (cfg) webhookFile; };
    systemd.services."nixlab-notify@" = {
      description = "Send a bounded Nixlab failure notification";
      wants = [ "network-online.target" ]; after = [ "network-online.target" ];
      environment.SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
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
    systemd.services.nixlab-rollback-guard = lib.mkIf site.setup.enable { onFailure = [ "nixlab-notify@%n.service" ]; };
    systemd.services.restic-backups-nixlab = lib.mkIf site.backup.enable { onFailure = [ "nixlab-notify@%n.service" ]; };
  };
}
