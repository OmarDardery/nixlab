{ pkgs }:
let
  lib = pkgs.lib;
  base = import ./site.nix;
  site = base // {
    apps = base.apps // { enable = false; };
    management = base.management // { interface = "eth1"; configure = true; };
  };
  guest = version: import (pkgs.path + "/nixos/lib/eval-config.nix") {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [ ({ modulesPath, ... }: {
      imports = [ (modulesPath + "/virtualisation/lxc-container.nix") (modulesPath + "/virtualisation/lxc-image-metadata.nix") ];
      networking.hostName = "deploy-fixture";
      networking.useDHCP = false;
      system.stateVersion = "26.05";
      environment.etc."nixlab-deploy-version".text = version;
      systemd.services.nixlab-test-data = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
        script = "mkdir -p /var/lib/nixlab; test -e /var/lib/nixlab/preserved || echo original-data > /var/lib/nixlab/preserved";
      };
    }) ];
  };
  old = guest "v1";
  target = guest "v2";
  unhealthy = pkgs.writeShellApplication { name = "nixlab-guest-health"; text = "exit 1"; };
in pkgs.testers.runNixOSTest {
  name = "nixlab-guest-deployments";
  globalTimeout = 1200;
  node.specialArgs = { inherit site; appImage = null; installedNetworking.networkManager = false; };
  nodes.host = { config, ... }: {
    imports = [ ../modules/storage.nix ../modules/networking.nix ../modules/incus.nix ../modules/instance-schema.nix ../modules/deployments.nix ];
    virtualisation = { memorySize = 4096; diskSize = 16384; cores = 2; };
    systemd.services.nixlab-kvm-check = { serviceConfig.Type = "oneshot"; script = "true"; };
    environment.systemPackages = [ pkgs.python3 pkgs.incus-lts.client pkgs.nix pkgs.git ];
    nixlab.incus.instances = lib.genAttrs [ "primary" "worker" ] (_: {
      image = "local:deploy-fixture"; start = true;
      config = { "boot.autostart" = "true"; "security.nesting" = "true"; };
    });
    nixlab.deployment.targets = {
      primary = { system = target.config.system.build.toplevel; healthUnits = [ "nixlab-test-data.service" ]; healthTimeout = 5; };
      worker = { system = target.config.system.build.toplevel; dependsOn = [ "primary" ]; healthUnits = [ "nixlab-test-data.service" ]; };
    };
    environment.etc."nixlab-test-deployments.json".text = builtins.toJSON config.nixlab.deployment.manifest;
    environment.etc."nixlab-test-deployment-paths.json".text = builtins.toJSON {
      previous = old.config.system.build.toplevel; failedProbe = unhealthy;
    };
    # Import the initial image before the source-owned instance reconciler.
    systemd.services.nixlab-test-image = {
      before = [ "nixlab-instances.service" ]; requiredBy = [ "nixlab-instances.service" ];
      after = [ "incus-preseed.service" ]; requires = [ "incus-preseed.service" ];
      path = [ pkgs.incus-lts.client ]; serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = "incus image import ${old.config.system.build.metadata}/tarball/${old.config.image.fileName} ${old.config.system.build.tarball}/tarball/${old.config.image.fileName} --alias deploy-fixture";
    };
  };
  testScript = ''
    host.start()
    host.wait_for_unit("nixlab-instances.service", timeout=300)
    for name in ("primary", "worker"):
        host.wait_until_succeeds(f"timeout --kill-after=5s 15s incus exec {name} --project lab --mode=non-interactive --disable-stdin -- systemctl is-active nixlab-test-data.service", timeout=180)
    run = "PYTHONPATH=${../admin}:${../scripts} NIXLAB_STATE=/var/lib/nixlab-deployment-test python3 ${./guest-deployment-fixture.py}"
    host.succeed(run + " failure", timeout=300)
    # Kill the real export/import worker, retain its journal, and reboot the controller.
    host.succeed(run + " queue")
    host.succeed("systemd-run --unit=interrupted-guest-deployment --property=KillMode=control-group " + run.replace("PYTHONPATH=", "env PYTHONPATH=", 1) + " run")
    host.wait_until_succeeds("python3 -c 'import json; p=json.load(open(\"/var/lib/nixlab-deployment-test/jobs/" + "b" * 32 + "/deployment.json\")); assert p[\"targets\"][\"primary\"][\"phase\"] == \"transferring\"'", timeout=120)
    host.succeed("systemctl kill --signal=KILL interrupted-guest-deployment.service")
    host.reboot()
    host.wait_for_unit("nixlab-instances.service", timeout=300)
    for name in ("primary", "worker"):
        host.wait_until_succeeds(f"timeout --kill-after=5s 15s incus exec {name} --project lab --mode=non-interactive --disable-stdin -- systemctl is-active nixlab-test-data.service", timeout=180)
    host.wait_until_succeeds(run + " resume", timeout=360)
    host.succeed(run + " verify", timeout=60)
  '';
}
