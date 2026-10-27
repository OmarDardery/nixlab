{ lib, site, ... }:
{
  assertions = [ { assertion = !site.secrets.enable || site.secrets.guestFile != "";
    message = "sops-nix requires an encrypted applications VM secrets file."; } ];
  sops = lib.mkIf site.secrets.enable {
    defaultSopsFile = ../. + "/${site.secrets.guestFile}";
    age.keyFile = site.secrets.ageKeyFile;
    secrets = {
      portainer-admin-password = {
        path = "/var/lib/nixlab-secrets/portainer-admin-password";
        restartUnits = [ "docker-portainer.service" ];
      };
    } // lib.optionalAttrs site.apps.enableDemo {
      demo-db-password = {
        path = "/var/lib/nixlab-secrets/demo-db-password";
        mode = "0444"; # readable to database and unprivileged application container through a secret mount
        restartUnits = [ "nixlab-demo.service" ];
      };
    };
  };
  systemd.tmpfiles.rules = [ "d /var/lib/nixlab-secrets 0700 root root -" ];
}
