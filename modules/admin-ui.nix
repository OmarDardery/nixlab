{ config, lib, pkgs, site, ... }:
let
  enabled = site.setup.enable;
  tool = import ../admin/package.nix { inherit pkgs; };
  install = if builtins.pathExists (import ../lib/machine.nix ../.).installation
    then builtins.fromJSON (builtins.readFile (import ../lib/machine.nix ../.).installation)
    else { interface = site.management.interface; trustedCIDRs = site.management.trustedCIDRs; };
in {
  config = lib.mkIf enabled {
    users.groups.nixlab-ui = { };
    users.users.nixlab-ui = { isSystemUser = true; group = "nixlab-ui"; };
    environment.systemPackages = [ tool ];
    systemd.tmpfiles.rules = [
      "d /var/lib/nixlab-admin 0710 root nixlab-ui -"
      "d /var/lib/nixlab-admin/jobs 0700 root root -"
      "d /run/nixlab-admin 0750 root nixlab-ui -"
    ];
    # During bootstrap retain the existing firewall implementation. The HTTPS process
    # also enforces source CIDRs; after conversion the host uses nftables.
    networking.firewall.interfaces."${if site.ready then site.management.interface else install.interface}".allowedTCPPorts = [ site.setup.port ];
    systemd.services.nixlab-admin-backend = {
      description = "Nixlab typed configuration and transaction broker";
      wantedBy = [ "multi-user.target" ]; after = [ "local-fs.target" ];
      path = [ pkgs.networkmanager ];
      environment.NIXLAB_ROOT = "/var/lib/nixlab/config";
      serviceConfig = {
        ExecStart = "${tool}/bin/nixlab-admin backend";
        Restart = "on-failure"; RestartSec = "3s";
        UMask = "0027"; RuntimeDirectory = "nixlab-admin"; RuntimeDirectoryMode = "0750";
        RuntimeDirectoryPreserve = "yes"; Group = "nixlab-ui";
      };
      restartIfChanged = false; stopIfChanged = false;
    };
    systemd.services.nixlab-ui = {
      description = "Nixlab HTTPS first-run setup and host settings";
      wantedBy = [ "multi-user.target" ];
      after = [ "nixlab-admin-backend.service" ]; requires = [ "nixlab-admin-backend.service" ];
      environment = { NIXLAB_ROOT = "/var/lib/nixlab/config"; NIXLAB_PORT = toString site.setup.port; };
      serviceConfig = {
        ExecStart = "${tool}/bin/nixlab-admin web";
        User = "nixlab-ui"; Group = "nixlab-ui";
        LoadCredential = [ "tls.crt:/var/lib/nixlab-admin/tls.crt" "tls.key:/var/lib/nixlab-admin/tls.key" ];
        Restart = "on-failure"; RestartSec = "3s";
        NoNewPrivileges = true; PrivateTmp = true; ProtectHome = true;
        ProtectSystem = "strict"; ProtectKernelTunables = true; ProtectKernelModules = true;
        ProtectControlGroups = true; RestrictSUIDSGID = true;
        CapabilityBoundingSet = ""; RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
      };
      restartIfChanged = false; stopIfChanged = false;
    };
    systemd.services.nixlab-rollback-guard = {
      description = "Recover an unconfirmed Nixlab activation";
      environment.NIXLAB_ROOT = "/var/lib/nixlab/config";
      path = [ pkgs.networkmanager ];
      serviceConfig = { Type = "oneshot"; ExecStart = "${tool}/bin/nixlab-admin guard"; };
    };
    systemd.services.incus = lib.mkIf config.virtualisation.incus.enable {
      serviceConfig.BindReadOnlyPaths = lib.mkIf (config.virtualisation.incus.useACMEHost == null) [
        "/var/lib/nixlab-admin/tls.crt:/var/lib/incus/server.crt"
        "/var/lib/nixlab-admin/tls.key:/var/lib/incus/server.key"
      ];
    };
    systemd.services.nixlab-tls-renew = {
      environment.NIXLAB_ROOT = "/var/lib/nixlab/config";
      serviceConfig = { Type = "oneshot"; ExecStart = "${tool}/bin/nixlab-admin renew"; };
    };
    systemd.timers.nixlab-tls-renew = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = "monthly"; Persistent = true; };
    };
    systemd.timers.nixlab-rollback-guard = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "20s"; OnUnitActiveSec = "15s"; AccuracySec = "1s"; };
    };
  };
}
