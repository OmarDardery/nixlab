# Expensive optional check: real apps VM, real Portainer, and a second NixOS guest.
# The workflow resolves the registry image and passes an immutable local image ID.
{ pkgs, apps, portainerArchive, portainerImage, cpuVendor }:
let
  lib = pkgs.lib;
  base = import ./site.nix;
  site = base // {
    inherit cpuVendor;
    setup = base.setup // { enable = true; };
    apps = base.apps // { inherit portainerImage; };
    management = base.management // { interface = "eth1"; configure = true; };
  };
  initialApps = apps.extendModules { modules = [ {
    nixlab.settings.apps.portainerImage = portainerImage;
    virtualisation.oci-containers.containers.portainer.imageFile = builtins.path { path = portainerArchive; name = "portainer-image.tar"; };
    environment.etc."nixlab-deploy-version".text = "v1";
  } ]; };
  nextApps = initialApps.extendModules { modules = [ ({ lib, ... }: {
    environment.etc."nixlab-deploy-version".text = lib.mkForce "v2";
  }) ]; };
  worker = version: import (pkgs.path + "/nixos/lib/eval-config.nix") {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [ ({ modulesPath, ... }: {
      imports = [ (modulesPath + "/virtualisation/lxc-container.nix") (modulesPath + "/virtualisation/lxc-image-metadata.nix") ];
      networking.hostName = "worker"; networking.useDHCP = false;
      environment.etc."nixlab-deploy-version".text = version;
      system.stateVersion = "26.05";
    }) ];
  };
  oldWorker = worker "v1";
  nextWorker = worker "v2";
  appImage = {
    system = initialApps.config.system.build.toplevel;
    metadata = "${initialApps.config.system.build.metadata}/tarball/${initialApps.config.image.fileName}";
    disk = "${initialApps.config.system.build.qemuImage}/nixos.qcow2";
  };
in pkgs.testers.runNixOSTest {
  name = "nixlab-apps-and-guest-rollout";
  globalTimeout = 1800;
  node.specialArgs = { inherit site appImage; installedNetworking.networkManager = false; };
  nodes.host = { config, ... }: {
    imports = [ ../modules/storage.nix ../modules/networking.nix ../modules/virtualization.nix ../modules/incus.nix ../modules/instance-schema.nix ../modules/deployments.nix ];
    virtualisation = { memorySize = 4096; diskSize = 32768; cores = 2; qemu.options = [ "-cpu host" ]; };
    boot.extraModprobeConfig = "options kvm_intel nested=1\noptions kvm_amd nested=1";
    environment.systemPackages = [ pkgs.python3 pkgs.incus-lts.client pkgs.nix pkgs.openssl ];
    nixlab.incus.instances.worker = { image = "local:rollout-worker"; start = true; config."security.nesting" = "true"; };
    nixlab.deployment.apps.enable = true;
    nixlab.deployment.targets = {
      apps.system = lib.mkForce nextApps.config.system.build.toplevel;
      worker = { system = nextWorker.config.system.build.toplevel; dependsOn = [ "apps" ]; };
    };
    environment.etc."nixlab-test-deployments.json".text = builtins.toJSON config.nixlab.deployment.manifest;
    systemd.services.nixlab-test-worker-image = {
      before = [ "nixlab-instances.service" ]; requiredBy = [ "nixlab-instances.service" ];
      after = [ "incus-preseed.service" ]; requires = [ "incus-preseed.service" ];
      path = [ pkgs.incus-lts.client ]; serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = "incus image import ${oldWorker.config.system.build.metadata}/tarball/${oldWorker.config.image.fileName} ${oldWorker.config.system.build.tarball}/tarball/${oldWorker.config.image.fileName} --alias rollout-worker";
    };
  };
  testScript = ''
    host.start()
    host.wait_for_unit("nixlab-instances.service", timeout=900)
    assert host.succeed("systemctl show nixlab-kvm-check.service --property=Result --value").strip() == "success"
    assert host.succeed("systemctl show nixlab-kvm-check.service --property=ExecMainStatus --value").strip() == "0"
    host.succeed("test -c /dev/kvm")
    host.wait_until_succeeds("timeout --kill-after=5s 15s incus exec apps --project lab --mode=non-interactive --disable-stdin -- test -e /etc/NIXOS", timeout=300)
    host.wait_until_succeeds("timeout --kill-after=5s 15s incus exec worker --project lab --mode=non-interactive --disable-stdin -- systemctl is-active multi-user.target", timeout=180)
    host.succeed("PYTHONPATH=${../admin}:${../scripts} NIXLAB_STATE=/var/lib/nixlab-deployment-test python3 ${./guest-deployments-kvm-fixture.py}", timeout=600)
  '';
}
