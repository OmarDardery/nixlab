{ config, lib, site, ... }:
let
  c = site.cockpit;
  trusted = lib.concatStringsSep ", " site.management.trustedCIDRs;
in {
  config = lib.mkIf c.enable {
    services.cockpit = {
      enable = true;
      inherit (c) port;
      openFirewall = false;
      showBanner = false;
      allowed-origins = [ "https://${site.management.address}:${toString c.port}" ];
      settings.WebService = { LoginTo = false; AllowMultiHost = false; AllowUnencrypted = false; };
    };
    systemd.sockets.cockpit.listenStreams = lib.mkForce [ "" "${site.management.address}:${toString c.port}" ];
    networking.firewall.extraInputRules = ''
      iifname ${builtins.toJSON site.management.interface} ip saddr { ${trusted} } tcp dport ${toString c.port} accept
    '';
    environment.etc = {
      "cockpit/disallowed-users".text = "root\n";
    } // lib.optionalAttrs site.setup.enable {
      # Runtime symlinks: private material is never copied into the Nix store.
      "cockpit/ws-certs.d/90-nixlab.cert".source = "/var/lib/nixlab-admin/tls.crt";
      "cockpit/ws-certs.d/90-nixlab.key".source = "/var/lib/nixlab-admin/tls.key";
    };
    users.users.operator.hashedPasswordFile = lib.mkIf c.managePassword
      (if site.secrets.enable then config.sops.secrets.cockpit-operator-password.path else "/var/lib/nixlab-secrets/cockpit-operator-password");
    assertions = [
      { assertion = !c.managePassword || config.users.mutableUsers;
        message = "Cockpit password enrollment requires mutable local accounts; manage immutable accounts in your source instead."; }
    ];
  };
}
