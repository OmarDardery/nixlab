{ config, lib, pkgs, site, ... }:
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
      root = { type = "disk"; path = "/"; pool = site.storage.pool; size = "32GiB"; };
      eth0 = { type = "nic"; name = "eth0"; network = site.guests.bridge; };
    };
  };
  declared = site.instances ++ map (i: lib.filterAttrs (_: value: value != null) i)
    (builtins.attrValues config.nixlab.incus.instances);
  # Do not adopt, create or delete the retired Docker/Arcane apps VM.
  # Existing legacy guests remain untouched until an explicit data migration.
  instances = declared;
  inventory = pkgs.writeText "inventory.json" (builtins.toJSON (site // { instances = declared; }));
  desired = pkgs.writeText "instances.json" (builtins.toJSON instances);
  reconcile = pkgs.writeShellApplication {
    name = "nixlab-reconcile";
    runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client ];
    text = ''PYTHONPATH=${../scripts} exec python3 ${../scripts/reconcile.py} ${desired}'';
  };
in {
  nixlab.incus.desiredInstances = instances;
  environment.etc."nixlab/desired-instances.json".source = desired;
  environment.etc."nixlab/owned-infrastructure.json".text = builtins.toJSON config.virtualisation.incus.preseed;
  virtualisation.incus = {
    enable = true;
    ui.enable = true;
    preseed = {
      config."core.https_address" = "0.0.0.0:8443";
      storage_pools = [ { name = site.storage.pool; driver = site.storage.driver;
        config.source = site.storage.source; } ];
      networks = [ {
        name = site.guests.bridge; type = "bridge";
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
    # First-run CLI configuration is not written atomically by Incus. Do not
    # race preseed's local infrastructure checks with client initialization.
    after = [ "incus.service" "incus-preseed.service" ]; requires = [ "incus.service" ];
    before = [ "nixlab-instances.service" ];
    serviceConfig = {
      ExecStart = "${pkgs.incus-lts.client}/bin/incus --force-local monitor --type=lifecycle --all-projects";
      Restart = "on-failure"; RestartSec = "5s";
    };
  };
  systemd.services.incus-preseed.preStart = lib.mkBefore ''
    PYTHONPATH=${../scripts} ${pkgs.python3}/bin/python ${../scripts/preflight.py} ${inventory}
    ${pkgs.python3}/bin/python ${../scripts/infrastructure.py} check ${pkgs.writeText "nixlab-owned-infrastructure.json" (builtins.toJSON config.virtualisation.incus.preseed)}
  '';
  systemd.services.incus-preseed.postStart = ''
    ${pkgs.python3}/bin/python ${../scripts/infrastructure.py} record ${pkgs.writeText "nixlab-owned-infrastructure.json" (builtins.toJSON config.virtualisation.incus.preseed)}
  '';
  systemd.services.incus-preseed.path = [ pkgs.incus-lts.client ];
  systemd.services.nixlab-instances = {
    description = "Reconcile owned Incus instances without deleting guest data";
    wantedBy = [ "multi-user.target" ];
    after = [ "incus-preseed.service" "nixlab-kvm-check.service" ];
    requires = [ "incus-preseed.service" "nixlab-kvm-check.service" ];
    path = [ pkgs.incus-lts.client pkgs.findutils pkgs.python3 ];
    restartTriggers = [ desired ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "30min"; };
    script = "${reconcile}/bin/nixlab-reconcile";
  };
}

