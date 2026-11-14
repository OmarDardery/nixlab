# Optional, expensive qualification test. The OCI archive is fetched/resolved by
# the workflow before building, so the nested VM itself requires no registry.
{ pkgs, apps, sopsModule, portainerArchive, portainerImage, cpuVendor, revision, trial ? "manual" }:
let
  base = import ./site.nix;
  site = base // {
    inherit cpuVendor;
    setup = base.setup // { enable = true; };
    cockpit = base.cockpit // { enable = true; };
    management = base.management // { interface = "eth1"; configure = true; };
  };
  guest = apps.extendModules {
    modules = [ {
      nixlab.settings.apps.portainerImage = portainerImage;
      virtualisation.oci-containers.containers.portainer.imageFile = portainerArchive;
      virtualisation.oci-containers.containers.portainer.pull = "never";
    } ];
  };
  appImage = {
    system = guest.config.system.build.toplevel;
    metadata = "${guest.config.system.build.metadata}/tarball/${guest.config.image.fileName}";
    disk = "${guest.config.system.build.qemuImage}/nixos.qcow2";
  };
in pkgs.testers.runNixOSTest {
  # Give each qualification attempt fresh disks and execution while retaining
  # cached guest closures. Reusing a successful test output is not a new drill.
  name = "nixlab-real-apps-commissioning-${trial}";
  globalTimeout = 1800;
  node.specialArgs = {
    inventory = site; inherit appImage;
    installedNetworking.networkManager = false; installedBase = null;
  };
  nodes.host = { ... }: {
    imports = [ sopsModule ../configuration.nix ];
    virtualisation = {
      memorySize = 4096; diskSize = 24576; cores = 2;
      qemu.options = [ "-cpu host" ];
    };
    boot.extraModprobeConfig = "options kvm_intel nested=1\noptions kvm_amd nested=1";
    environment.etc."nixlab-test-settings.json".text = builtins.toJSON site;
    systemd.services.nixlab-test-credentials = {
      wantedBy = [ "multi-user.target" ];
      before = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" ];
      requiredBy = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" ];
      after = [ "systemd-tmpfiles-setup.service" ];
      path = [ pkgs.openssl ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        PYTHONPATH=${../admin}:${../scripts} ${pkgs.python3}/bin/python ${./portal-fixture.py}
        install -d -m 0700 /var/lib/nixlab-secrets
        test -s /var/lib/nixlab-secrets/apps-portainer-password || \
          (umask 077; openssl rand -hex 24 > /var/lib/nixlab-secrets/apps-portainer-password)
        cp ${../flake.lock} /var/lib/nixlab/config/flake.lock
        printf '%s\n' '${builtins.toJSON { inherit revision; version = builtins.replaceStrings [ "\n" ] [ "" ] (builtins.readFile ../VERSION); }}' > /var/lib/nixlab/config/platform-release.json
      '';
    };
  };
  testScript = ''
    import json
    host.start(allow_reboot=True)
    host.wait_for_unit("nixlab-kvm-check.service")
    for boot in range(3):
        host.wait_for_unit("nixlab-instances.service", timeout=600)
        host.wait_for_unit("nixlab-apps-onboarding.service", timeout=600)
        host.wait_until_succeeds("nixlab-admin doctor", timeout=180)
        assert json.loads(host.succeed("incus query '/1.0/instances/apps?project=lab'"))["type"] == "virtual-machine"
        host.succeed("incus exec apps --project lab -- systemctl is-active docker.service")
        # A real Portainer container, real persisted credentials and its actual
        # /api/auth response are checked by commissioning.collect.
        report = json.loads(host.succeed("nixlab-admin commissioning collect --mode fresh --environment nested-integration"))
        states = {item["check"]: item["status"] for item in report["checks"]}
        for check in ("management", "kvm", "apps-vm", "apps-auth"):
            assert states[check] == "passed", (check, states)
        assert report["qualification"] == "unqualified"
        if boot < 2:
            host.reboot()
    host.succeed("nixlab-admin commissioning export --mode fresh --environment nested-integration > /tmp/nested-apps.json")
    host.copy_from_vm("/tmp/nested-apps.json")
  '';
}
