{ lib, site, ... }:
{
  assertions = [ { assertion = !site.secrets.enable || site.secrets.hostFile != "";
    message = "sops-nix requires an encrypted host secrets file."; } ];
  sops = lib.mkIf site.secrets.enable {
    defaultSopsFile = ../. + "/${site.secrets.hostFile}";
    age.keyFile = site.secrets.ageKeyFile;
    secrets = (lib.optionalAttrs site.tunnel.enable {
      cloudflared = { key = "cloudflared"; path = site.tunnel.credentialsFile;
        restartUnits = [ "cloudflared-tunnel-${site.tunnel.uuid}.service" ]; };
    }) // (lib.optionalAttrs site.backup.enable {
      restic-password = { path = site.backup.passwordFile; };
      restic-environment = { path = site.backup.environmentFile; };
    });
  };
  systemd.tmpfiles.rules = [ "d /var/lib/nixlab-secrets 0700 root root -" ];
}
