{ lib, site, ... }:
{
  assertions = [ { assertion = !site.tunnel.enable || (site.tunnel.uuid != "" && site.tunnel.ingress != { });
    message = "A tunnel UUID and application-only ingress mapping are required."; } ];
  services.cloudflared = lib.mkIf site.tunnel.enable {
    enable = true;
    tunnels."${site.tunnel.uuid}" = {
      credentialsFile = site.tunnel.credentialsFile;
      default = "http_status:404";
      ingress = site.tunnel.ingress;
    };
  };
  systemd.services = lib.mkIf site.tunnel.enable {
    "cloudflared-tunnel-${site.tunnel.uuid}".serviceConfig.RestartSec = "10s";
  };
}
