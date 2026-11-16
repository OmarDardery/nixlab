{ pkgs }:
let
  defaults = import ../site-defaults.nix;
  settings = defaults // {
    hostName = "nixlab-test"; cpuVendor = "intel";
    setup = defaults.setup // { enable = true; };
    cockpit = defaults.cockpit // { enable = true; };
    management = defaults.management // { interface = "eth0"; address = "127.0.0.1"; trustedCIDRs = [ "127.0.0.0/8" ]; };
  };
in pkgs.testers.runNixOSTest {
  globalTimeout = 600;
  name = "nixlab-portal";
  node.specialArgs = {
    inventory = settings;
    installedBase = { ... }: { system.stateVersion = "26.05"; };
  };
  nodes.machine = { lib, pkgs, ... }: {
    imports = [ ../configuration-bootstrap.nix ../modules/cockpit.nix ../modules/security.nix ];
    virtualisation.memorySize = 2048;
    boot.kernelModules = lib.mkForce [ ];
    environment.systemPackages = [ (pkgs.python3.withPackages (ps: [ ps.websocket-client ])) pkgs.age ];
    environment.etc."nixlab-test-settings.json".text = builtins.toJSON settings;
    environment.etc."nixlab-test-client.py".source = ./portal-client.py;
    environment.etc."nixlab-test-cockpit.py".source = ./cockpit-client.py;
    users.users.operator.initialPassword = "vm-cockpit-password"; # disposable test VM only
    users.users.outsider.isNormalUser = true;
    systemd.services.nixlab-test-fixture = {
      wantedBy = [ "multi-user.target" ]; after = [ "systemd-tmpfiles-setup.service" ];
      before = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" "nixlab-ui.service" ];
      requiredBy = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" "nixlab-ui.service" ];
      path = [ pkgs.openssl ];
      environment.NIXLAB_TEST_SITE_SOURCE = toString (import ./site-source.nix { inherit pkgs; });
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
      script = ''PYTHONPATH=${../admin}:${../scripts} ${pkgs.python3}/bin/python3 ${./portal-fixture.py}'';
    };
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("nixlab-admin-backend.service")
    machine.wait_for_unit("nixlab-ui.service")
    machine.wait_for_open_port(9440)
    assert machine.succeed("systemctl show nixlab-ui -p User --value").strip() == "nixlab-ui"
    assert machine.succeed("stat -c %a /run/nixlab-admin/backend.sock").strip() == "660"
    machine.fail("runuser -u outsider -- python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect(\"/run/nixlab-admin/backend.sock\")'")
    machine.fail("runuser -u nixlab-ui -- cat /var/lib/nixlab-admin/ca.key")
    machine.succeed("python3 /etc/nixlab-test-client.py")
    machine.wait_for_unit("cockpit.socket")
    machine.succeed("python3 /etc/nixlab-test-cockpit.py")
    sshd = machine.succeed("sshd -T -f /etc/ssh/sshd_config")
    assert "passwordauthentication yes" in sshd.lower(), sshd
    # Exercise the existing boot timer, real permissions and recovery-preserving
    # cleanup after interrupted publication. All credentials are disposable.
    machine.succeed("systemctl stop nixlab-rollback-guard.timer nixlab-rollback-guard.service")
    machine.succeed("PYTHONPATH=${../admin}:${../scripts} python3 ${./staged-secret-fixture.py} prepare")
    machine.fail("runuser -u outsider -- test -r /var/lib/nixlab-admin/jobs/0000000000000000000000000000006b/secrets.json")
    machine.fail("runuser -u nixlab-ui -- test -r /var/lib/nixlab-admin/jobs/0000000000000000000000000000006b/secrets.json")
    machine.succeed("PYTHONPATH=${../admin}:${../scripts} python3 ${./enrollment-fixture.py} prepare")
    machine.shutdown()
    machine.start()
    machine.wait_for_unit("nixlab-ui.service")
    machine.wait_for_unit("nixlab-rollback-guard.timer")
    machine.wait_until_succeeds("PYTHONPATH=${../admin}:${../scripts} python3 ${./staged-secret-fixture.py} verify", timeout=90)
    machine.succeed("systemctl is-active nixlab-rollback-guard.timer")
    machine.succeed("PYTHONPATH=${../admin}:${../scripts} python3 ${./enrollment-fixture.py} verify")
  '';
}
