{ config, lib, pkgs, site, appImage ? null, ... }:
let
  base = {
    name = "base"; project = "lab";
    config = {
      "limits.cpu" = "2"; "limits.memory" = "2GiB";
      "snapshots.schedule" = "@daily";
      "snapshots.expiry" = "7d";
      "snapshots.pattern" = "auto-%Y%m%d-%H%M";
    };
    devices = {
      root = { type = "disk"; path = "/"; pool = "guests"; size = "32GiB"; };
      eth0 = { type = "nic"; name = "eth0"; network = "incusbr0"; };
    };
  };
  declared = site.instances ++ map (i: lib.filterAttrs (_: value: value != null) i)
    (builtins.attrValues config.nixlab.incus.instances);
  instances = lib.optional site.apps.enable {
    name = "apps"; project = "lab"; type = "virtual-machine";
    start = true; # Re-enable the managed applications VM when selected.
    image = "local:nixlab-apps"; profiles = [ "base" ];
    config = { "limits.cpu" = site.apps.cpu; "limits.memory" = site.apps.memory;
      "security.secureboot" = "false"; "boot.autostart" = "true"; };
    devices = {
      root = { type = "disk"; path = "/"; pool = "guests"; size = site.apps.disk; };
      eth0 = { type = "nic"; name = "eth0"; network = "incusbr0"; "ipv4.address" = site.guests.appsAddress; };
    };
  } ++ lib.optional (!site.apps.enable) {
    name = "apps"; project = "lab"; type = "virtual-machine";
    manageExistingOnly = true; start = false; stop = true; profiles = [ "base" ];
    config."boot.autostart" = "false";
  } ++ declared;
  inventory = pkgs.writeText "inventory.json" (builtins.toJSON (site // { instances = declared; }));
  desired = pkgs.writeText "instances.json" (builtins.toJSON instances);
  reconcile = pkgs.writeShellApplication {
    name = "nixlab-reconcile";
    runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client ];
    text = ''exec python3 ${../scripts/reconcile.py} ${desired}'';
  };
in {
  virtualisation.incus = {
    enable = true;
    ui.enable = true;
    preseed = {
      config."core.https_address" = "${site.management.address}:8443";
      storage_pools = [ { name = "guests"; driver = site.storage.driver;
        config.source = site.storage.source; } ];
      networks = [ {
        name = "incusbr0"; type = "bridge";
        config = {
          "ipv4.address" = site.guests.gatewayCIDR;
          "ipv4.dhcp.ranges" = site.guests.dhcpRange;
          "ipv4.nat" = "false"; "ipv4.firewall" = "false";
          "ipv6.address" = "none"; "ipv6.firewall" = "false";
          "dns.domain" = "guests.internal";
        };
      } ];
      projects = [ {
        name = "lab";
        config = { "features.images" = "false"; "features.profiles" = "true";
          "features.networks" = "false"; "features.storage.volumes" = "true"; };
      } ];
      profiles = [ base ];
    };
  };
  systemd.services.nixlab-incus-audit = {
    description = "Record Incus lifecycle events in the persistent journal";
    wantedBy = [ "multi-user.target" ];
    after = [ "incus.service" ]; requires = [ "incus.service" ];
    serviceConfig = {
      ExecStart = "${pkgs.incus-lts.client}/bin/incus monitor --type=lifecycle --all-projects";
      Restart = "on-failure"; RestartSec = "5s";
    };
  };
  systemd.services.incus-preseed.preStart = lib.mkBefore ''
    ${pkgs.python3}/bin/python ${../scripts/preflight.py} ${inventory}
  '';
  systemd.services.nixlab-instances = {
    description = "Reconcile owned Incus instances without deleting guest data";
    wantedBy = [ "multi-user.target" ];
    after = [ "incus-preseed.service" "nixlab-kvm-check.service" ];
    requires = [ "incus-preseed.service" "nixlab-kvm-check.service" ];
    path = [ pkgs.incus-lts.client pkgs.findutils ];
    restartTriggers = [ desired ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "30min"; };
    preStart = lib.optionalString (site.apps.enable && appImage != null) ''
      # Image import updates an alias, never replaces an existing VM or its disk.
      incus image import ${lib.escapeShellArg appImage.metadata} ${lib.escapeShellArg appImage.disk} --alias nixlab-apps --reuse
    '';
    script = "${reconcile}/bin/nixlab-reconcile";
  };
}
