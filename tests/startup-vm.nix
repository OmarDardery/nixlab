{ pkgs }:
let
  defaults = import ../site-defaults.nix;
  settings = defaults // {
    ready = true; hostName = "nixlab-startup"; cpuVendor = "intel";
    setup = defaults.setup // { enable = true; };
    apps = defaults.apps // { enable = true; portainerPort = 9443; };
    cockpit = defaults.cockpit // { enable = true; };
    management = defaults.management // {
      interface = "eth1"; address = "192.0.2.10"; trustedCIDRs = [ "192.0.2.0/24" ];
    };
    guests = defaults.guests // { appsAddress = "10.77.0.10"; };
  };
  fixture = pkgs.writeText "management-backend.py" ''
    import http.server, ssl
    from pathlib import Path
    import runtime as rt
    import sys
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204 if self.path == '/api/users/admin/check' else 200)
            self.end_headers()
            if self.path != '/api/users/admin/check': self.wfile.write(b'{"fixture":"NIXLAB_PROXY_READY"}')
        def log_message(self, *args): pass
    port = int(sys.argv[1])
    server = http.server.HTTPServer(('10.77.0.10' if port == 9443 else '0.0.0.0', port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    prefix = 'apps-tls' if port == 9443 else 'tls'
    context.load_cert_chain(rt.STATE / (prefix + '.crt'), rt.STATE / (prefix + '.key'))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
  '';
in pkgs.testers.runNixOSTest {
  name = "nixlab-dhcp-management-reboot";
  globalTimeout = 600;
  defaults.virtualisation.vlans = [ 1 ];
  nodes.lan = { ... }: {
    networking.useDHCP = false;
    networking.interfaces.eth1.ipv4.addresses = [ { address = "192.0.2.1"; prefixLength = 24; } ];
    services.dnsmasq = {
      enable = true;
      settings = { interface = "eth1"; bind-interfaces = true;
        dhcp-range = "192.0.2.20,192.0.2.20,255.255.255.0,1h";
        dhcp-option = "option:router,192.0.2.1";
      };
    };
    networking.firewall.allowedUDPPorts = [ 53 67 ];
    environment.systemPackages = [ pkgs.curl ];
  };
  nodes.machine = { lib, ... }: {
    imports = [ ../modules/admin-ui.nix ../modules/apps-onboarding.nix ../modules/cockpit.nix ];
    _module.args.site = settings;
    virtualisation.memorySize = 1536;
    networking.useDHCP = false;
    networking.networkmanager = {
      enable = true;
      ensureProfiles.profiles.nixlab-test = {
        connection = { id = "nixlab-test"; type = "ethernet"; interface-name = "eth1"; };
        ipv4.method = "auto"; ipv6.method = "disabled";
      };
    };
    networking.nftables.enable = true;
    networking.firewall.allowedTCPPorts = [ 8443 ];
    networking.interfaces.lo.ipv4.addresses = [ { address = "10.77.0.10"; prefixLength = 32; } ];
    environment.systemPackages = [ pkgs.curl pkgs.python3 ];
    environment.etc."nixlab-test-settings.json".text = builtins.toJSON settings;
    environment.etc."nixlab/effective-settings.json".text = builtins.toJSON settings;
    systemd.services.nixlab-test-fixture = {
      wantedBy = [ "multi-user.target" ];
      before = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" ];
      requiredBy = [ "nixlab-management-identity.service" "nixlab-admin-backend.service" ];
      after = [ "systemd-tmpfiles-setup.service" ];
      path = [ pkgs.openssl ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''PYTHONPATH=${../admin}:${../scripts} ${pkgs.python3}/bin/python ${./portal-fixture.py}'';
    };
    # Controlled TLS backends exercise the real nginx configuration with apps enabled.
    # VM deployment/reconciliation is covered independently by the foundation tests.
    systemd.services.nixlab-apps-onboarding = lib.mkForce {
      wantedBy = [ "multi-user.target" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = "true";
    };
    systemd.services.fixture-portainer = {
      wantedBy = [ "multi-user.target" ];
      requires = [ "nixlab-management-identity.service" ];
      after = [ "nixlab-management-identity.service" "network-online.target" ];
      environment.PYTHONPATH = "${../admin}:${../scripts}";
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python ${fixture} 9443";
    };
    systemd.services.fixture-incus = {
      wantedBy = [ "multi-user.target" ];
      requires = [ "nixlab-management-identity.service" ];
      after = [ "nixlab-management-identity.service" ];
      environment.PYTHONPATH = "${../admin}:${../scripts}";
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python ${fixture} 8443";
    };
  };
  testScript = ''
    import json
    start_all()
    lan.wait_for_unit("dnsmasq.service")
    for boot in range(2):
        machine.wait_for_unit("nixlab-management-identity.service")
        machine.wait_for_unit("cockpit.socket")
        machine.wait_for_unit("nginx.service")
        addresses = json.loads(machine.succeed("ip -j -4 address show dev eth1"))
        address = next(a["local"] for link in addresses for a in link["addr_info"] if a["scope"] == "global")
        machine.wait_until_succeeds(f"curl --fail --cacert /var/lib/nixlab-admin/ca.crt https://{address}:9443/api/status | grep NIXLAB_PROXY_READY")
        lan.wait_until_succeeds(f"curl --fail --insecure https://{address}:9090/ping")
        machine.wait_until_succeeds("nixlab-admin doctor")
        if boot == 0:
            machine.reboot()
  '';
}
