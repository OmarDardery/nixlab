{ pkgs }:
let
  base = import ./site.nix;
  site = base // {
    setup = base.setup // { enable = true; };
    guests = base.guests // { appsAddress = "10.77.0.10"; gatewayCIDR = "10.77.0.1/24"; };
  };
  web = pkgs.writeTextDir "www/index.html" "NIXLAB_PROXY_ONLY";
  image = pkgs.dockerTools.buildLayeredImage {
    name = "nixlab-firewall-fixture"; tag = "test";
    contents = [ pkgs.busybox web ];
    config.Cmd = [ "${pkgs.busybox}/bin/httpd" "-f" "-p" "8080" "-h" "${web}/www" ];
  };
in pkgs.testers.runNixOSTest {
  globalTimeout = 600;
  name = "nixlab-management-network";
  # The apps module supplies a guest-scoped upstream dhcpcd package fix.
  node.pkgsReadOnly = false;
  defaults = {
    virtualisation.vlans = [ 1 ];
    networking.useDHCP = false;
    environment.systemPackages = [ pkgs.curl ];
  };
  nodes = {
    apps = { lib, ... }: {
      imports = [ ../modules/docker-host.nix ];
      _module.args.site = site;
      virtualisation.memorySize = 1536;
      networking.useDHCP = lib.mkForce true;
      # Remove the driver's static address so it cannot mask a lost DHCP lease.
      networking.interfaces.eth1.ipv4.addresses = lib.mkForce [ ];
      networking.interfaces.eth1.ipv6.addresses = lib.mkForce [ ];
      virtualisation.additionalPaths = [ image ];
    };
    proxy = { ... }: {
      virtualisation.memorySize = 512;
      networking.interfaces.eth1.ipv4.addresses = [ { address = "10.77.0.1"; prefixLength = 24; } ];
      networking.interfaces.eth1.ipv6.addresses = [ { address = "fd77::1"; prefixLength = 64; } ];
      services.dnsmasq = {
        enable = true;
        settings = {
          interface = "eth1"; bind-interfaces = true;
          dhcp-range = "10.77.0.10,10.77.0.10,255.255.255.0,1h";
          dhcp-option = "option:router,10.77.0.1";
        };
      };
      services.radvd = {
        enable = true;
        config = ''
          interface eth1 {
            AdvSendAdvert on;
            MinRtrAdvInterval 3;
            MaxRtrAdvInterval 5;
            prefix fd77::/64 { AdvOnLink on; AdvAutonomous on; };
          };
        '';
      };
      boot.kernel.sysctl."net.ipv6.conf.all.forwarding" = 1;
      networking.firewall.allowedUDPPorts = [ 53 67 ];
    };
    peer = { ... }: {
      virtualisation.memorySize = 512;
      networking.interfaces.eth1.ipv4.addresses = [ { address = "10.77.0.20"; prefixLength = 24; } ];
    };
  };
  testScript = ''
    import time
    start_all()
    proxy.wait_for_unit("dnsmasq.service")
    proxy.wait_for_unit("radvd.service")
    apps.wait_for_unit("dhcpcd.service")
    apps.wait_until_succeeds("ip -4 address show dev eth1 | grep '10.77.0.10/24'", timeout=60)
    apps.wait_until_succeeds("ip -6 address show dev eth1 scope global | grep 'fd77:'", timeout=60)
    dhcp_pid = apps.succeed("systemctl show dhcpcd -p MainPID --value").strip()
    assert dhcp_pid.isdigit() and int(dhcp_pid) > 0, dhcp_pid
    apps.wait_for_unit("docker.service")
    apps.succeed("systemctl start nixlab-portainer-firewall")
    apps.succeed("docker load -i ${image}")
    apps.succeed("docker run -d --restart=always --name fixture -p 10.77.0.10:9443:8080 nixlab-firewall-fixture:test")
    proxy.wait_until_succeeds("curl --fail --max-time 3 http://10.77.0.10:9443 | grep NIXLAB_PROXY_ONLY", timeout=60)
    peer.fail("curl --fail --max-time 3 http://10.77.0.10:9443")
    # Exercise real Docker bridges/peers and ordinary interface removal while
    # a router advertisement exists on the uplink (upstream #614 trigger).
    for i in range(3):
        apps.succeed(f"docker network create churn{i}")
        apps.succeed(f"docker run --rm --network churn{i} nixlab-firewall-fixture:test ${pkgs.busybox}/bin/true")
        apps.succeed(f"docker network rm churn{i}")
        apps.succeed(f"ip link add test{i}a type veth peer name test{i}b")
        apps.succeed(f"ip link set test{i}a up")
        apps.succeed(f"ip link set test{i}b up")
        time.sleep(3)
        apps.succeed(f"ip link set test{i}a down")
        apps.succeed(f"ip link delete test{i}a")
    apps.succeed("systemctl restart docker")
    apps.wait_for_unit("nixlab-portainer-firewall.service")
    proxy.wait_until_succeeds("curl --fail --max-time 3 http://10.77.0.10:9443 | grep NIXLAB_PROXY_ONLY", timeout=60)
    peer.fail("curl --fail --max-time 3 http://10.77.0.10:9443")
    # RTR_CARRIER_EXPIRE is 17 seconds in the pinned client. Observe beyond
    # that timer after churn/restart; auto-restart must not disguise a crash.
    time.sleep(20)
    assert apps.succeed("systemctl show dhcpcd -p MainPID --value").strip() == dhcp_pid
    assert apps.succeed("systemctl show dhcpcd -p NRestarts --value").strip() == "0"
    apps.succeed("systemctl is-active dhcpcd.service")
    apps.succeed("ip -4 address show dev eth1 | grep '10.77.0.10/24'")
    apps.succeed("ip -6 address show dev eth1 scope global | grep 'fd77:'")
  '';
}
