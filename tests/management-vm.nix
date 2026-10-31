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
  defaults = {
    virtualisation.vlans = [ 1 ];
    networking.useDHCP = false;
    environment.systemPackages = [ pkgs.curl ];
  };
  nodes = {
    apps = { ... }: {
      imports = [ ../modules/docker-host.nix ];
      _module.args.site = site;
      virtualisation.memorySize = 1536;
      networking.interfaces.eth1.ipv4.addresses = [ { address = "10.77.0.10"; prefixLength = 24; } ];
      virtualisation.additionalPaths = [ image ];
    };
    proxy = { ... }: {
      virtualisation.memorySize = 512;
      networking.interfaces.eth1.ipv4.addresses = [ { address = "10.77.0.1"; prefixLength = 24; } ];
    };
    peer = { ... }: {
      virtualisation.memorySize = 512;
      networking.interfaces.eth1.ipv4.addresses = [ { address = "10.77.0.20"; prefixLength = 24; } ];
    };
  };
  testScript = ''
    start_all()
    apps.wait_for_unit("docker.service")
    apps.succeed("systemctl start nixlab-portainer-firewall")
    apps.succeed("docker load -i ${image}")
    apps.succeed("docker run -d --restart=always --name fixture -p 10.77.0.10:9443:8080 nixlab-firewall-fixture:test")
    proxy.wait_until_succeeds("curl --fail --max-time 3 http://10.77.0.10:9443 | grep NIXLAB_PROXY_ONLY", timeout=60)
    peer.fail("curl --fail --max-time 3 http://10.77.0.10:9443")
    apps.succeed("systemctl restart docker")
    apps.wait_for_unit("nixlab-portainer-firewall.service")
    proxy.wait_until_succeeds("curl --fail --max-time 3 http://10.77.0.10:9443 | grep NIXLAB_PROXY_ONLY", timeout=60)
    peer.fail("curl --fail --max-time 3 http://10.77.0.10:9443")
  '';
}
