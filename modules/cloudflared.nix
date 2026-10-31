{ lib, site, ... }:
{
  assertions = [ { assertion = !site.tunnel.enable || (site.tunnel.uuid != "" && site.tunnel.ingress != { });
    message = "A tunnel UUID and application-only ingress mapping are required."; }
    { assertion = !site.tunnel.enable || lib.all (target:
        builtins.match "https?://[^/]+:(${lib.concatMapStringsSep "|" toString (lib.unique [ 22 8443 9090 9100 9440 9441 9443 site.setup.port site.cockpit.port site.apps.portainerPort ])})([/?.#].*)?" target == null
      ) (builtins.attrValues site.tunnel.ingress);
      message = "Cockpit, Incus, Portainer and other management listeners cannot be public tunnel origins."; }
  ];
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
