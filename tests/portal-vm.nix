{ pkgs }:
let
  defaults = import ../site-defaults.nix;
  settings = defaults // {
    hostName = "nixlab-test"; cpuVendor = "intel";
    setup = defaults.setup // { enable = true; };
    management = defaults.management // { interface = "eth0"; address = "127.0.0.1"; trustedCIDRs = [ "127.0.0.0/8" ]; };
  };
in pkgs.testers.runNixOSTest {
  name = "nixlab-portal";
  node.specialArgs = {
    inventory = settings;
    installedBase = { ... }: { system.stateVersion = "26.05"; };
  };
  nodes.machine = { lib, pkgs, ... }: {
    imports = [ ../configuration-bootstrap.nix ];
    virtualisation.memorySize = 2048;
    boot.kernelModules = lib.mkForce [ ];
    environment.systemPackages = [ pkgs.python3 pkgs.age ];
    environment.etc."nixlab-test-settings.json".text = builtins.toJSON settings;
    environment.etc."nixlab-test-client.py".source = ./portal-client.py;
    users.users.outsider.isNormalUser = true;
    systemd.services.nixlab-test-fixture = {
      wantedBy = [ "multi-user.target" ]; after = [ "systemd-tmpfiles-setup.service" ];
      before = [ "nixlab-admin-backend.service" "nixlab-ui.service" ];
      requiredBy = [ "nixlab-admin-backend.service" "nixlab-ui.service" ];
      path = [ pkgs.openssl ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
      script = ''PYTHONPATH=${../admin} ${pkgs.python3}/bin/python3 ${./portal-fixture.py}'';
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
  '';
}
