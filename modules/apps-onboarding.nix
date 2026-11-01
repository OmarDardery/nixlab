# First-run VM credentials and a TLS-verified LAN portal for the application dashboard.
{ lib, pkgs, site, appImage ? null, ... }:
let enabled = site.setup.enable && site.apps.enable;
  cidrs = lib.concatMapStringsSep "\n" (c: "allow ${c};") site.management.trustedCIDRs;
in {
  config = lib.mkIf enabled {
    systemd.services.nixlab-apps-onboarding = {
      description = "Provision application VM runtime credentials and separate TLS identity";
      wantedBy = [ "multi-user.target" ];
      after = [ "nixlab-instances.service" ]; requires = [ "nixlab-instances.service" ];
      path = [ pkgs.incus-lts.client pkgs.coreutils pkgs.nix pkgs.python3 ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "5min"; };
      script = ''
        ready=0
        for attempt in $(seq 1 90); do
          if incus exec apps --project lab -- true; then ready=1; break; fi
          sleep 2
        done
        test "$ready" = 1
        ${lib.optionalString (appImage != null) "python3 ${../scripts/deploy-apps.py} ${appImage.system}"}
        incus exec apps --project lab -- install -d -m 0700 /var/lib/nixlab-secrets
        if test -f /var/lib/nixlab-secrets/apps-portainer-password; then
          incus file push /var/lib/nixlab-secrets/apps-portainer-password apps/var/lib/nixlab-secrets/portainer-admin-password --project lab --mode 0600
        fi
        if test -f /var/lib/nixlab-secrets/apps-demo-password; then
          incus file push /var/lib/nixlab-secrets/apps-demo-password apps/var/lib/nixlab-secrets/demo-db-password --project lab --mode 0444
        fi
        incus file push /var/lib/nixlab-admin/apps-tls.crt apps/var/lib/nixlab-secrets/tls.crt --project lab --mode 0644
        incus file push /var/lib/nixlab-admin/apps-tls.key apps/var/lib/nixlab-secrets/tls.key --project lab --mode 0600
        incus exec apps --project lab -- systemctl restart docker-portainer.service
        ${lib.optionalString site.apps.enableDemo "incus exec apps --project lab -- systemctl restart nixlab-demo.service"}
      '';
    };
    systemd.services.nginx.serviceConfig.LoadCredential = [
      "nixlab-tls.crt:/var/lib/nixlab-admin/tls.crt"
      "nixlab-tls.key:/var/lib/nixlab-admin/tls.key"
      "nixlab-ca.crt:/var/lib/nixlab-admin/ca.crt"
    ];
    services.nginx = {
      enable = true;
      # One proxy worker is enough for a private management dashboard.
      appendConfig = "worker_processes 1;";
      eventsConfig = "worker_connections 256;";
      virtualHosts.nixlab-portainer = {
        # Explicit listen.ssl alone does not enable NixOS certificate directives.
        onlySSL = true;
        serverName = site.management.address;
        listen = [ { addr = site.management.address; port = site.apps.portainerPort; ssl = true; } ];
        sslCertificate = "/run/credentials/nginx.service/nixlab-tls.crt";
        sslCertificateKey = "/run/credentials/nginx.service/nixlab-tls.key";
        locations."/" = {
          proxyPass = "https://${site.guests.appsAddress}:9443";
          proxyWebsockets = true;
          extraConfig = ''
            ${cidrs}
            deny all;
            # Preserve the public host *and port* for Portainer's origin checks.
            proxy_set_header Host $host:$server_port;
            proxy_set_header X-Forwarded-Host $host:$server_port;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Port $server_port;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_ssl_verify on;
            proxy_ssl_server_name on;
            proxy_ssl_name apps.internal;
            proxy_ssl_trusted_certificate /run/credentials/nginx.service/nixlab-ca.crt;
            proxy_read_timeout 3600s;
          '';
        };
      };
    };
    networking.firewall.extraInputRules = ''
      iifname ${builtins.toJSON site.management.interface} ip saddr { ${lib.concatStringsSep ", " site.management.trustedCIDRs} } tcp dport ${toString site.apps.portainerPort} accept
    '';
  };
}
