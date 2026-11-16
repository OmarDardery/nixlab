# Import only in the dedicated applications VM.
{ lib, pkgs, site, ... }:
let
  python = pkgs.python3.withPackages (ps: [ ps.psycopg ps.redis ]);
  demoImage = pkgs.dockerTools.buildLayeredImage {
    name = "nixlab-demo"; tag = "1";
    contents = [ pkgs.cacert ];
    config = {
      Cmd = [ "${python}/bin/python" "${../examples/demo/app.py}" ];
      Env = [ "PYTHONUNBUFFERED=1" ];
      User = "65534:65534";
      ExposedPorts."8080/tcp" = { };
      Healthcheck = {
        Test = [ "CMD" "${python}/bin/python" "-c" "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)" ];
        Interval = 30000000000; Timeout = 10000000000; Retries = 3;
      };
    };
  };
  projectTool = pkgs.writeShellApplication {
    name = "nixlab-project";
    runtimeInputs = [ pkgs.python3 pkgs.docker pkgs.docker-compose pkgs.util-linux ];
    text = ''PYTHONPATH=${../scripts} exec python3 ${../scripts/project.py} "$@"'';
  };
  credentialTool = pkgs.writeShellApplication {
    name = "nixlab-arcane-credentials";
    runtimeInputs = [ pkgs.python3 pkgs.docker pkgs.systemd ];
    text = ''
      export ARCANE_ORIGIN=${lib.escapeShellArg (if site.setup.enable then "https://${site.management.address}:${toString site.apps.arcanePort}" else "http://localhost:9443")}
      exec python3 ${../scripts/arcane_credentials.py} "$@"
    '';
  };
in {
  # Guest-only backport: an IPv6 expiry timer can outlive a removed interface
  # (upstream #614). Remove when pinned dhcpcd contains commit 0c1f40749f1d.
  nixpkgs.overlays = [ (_: previous: {
    dhcpcd = previous.dhcpcd.overrideAttrs (old: {
      patches = (old.patches or [ ]) ++ [ ./patches/dhcpcd-ipv6nd-interface-lifetime.patch ];
    });
  }) ];
  # Docker owns these bridges and peer links; guest DHCP owns the uplink.
  networking.dhcpcd.denyInterfaces = [ "docker*" "br-*" "veth*" ];
  virtualisation.docker = {
    enable = true;
    autoPrune.enable = false;
    daemon.settings = { "log-driver" = "local"; "log-opts" = { "max-size" = "10m"; "max-file" = "3"; }; };
  };
  environment.systemPackages = [ projectTool credentialTool pkgs.git pkgs.docker-compose ];
  networking.hosts.${site.guests.appsAddress} = [ "apps.internal" ];
  systemd.tmpfiles.rules = [
    "d /srv/compose 0700 root root -"
    "d /srv/compose/demo 0700 root root -"
    "d /srv/arcane 0700 root root -"
    "d /var/lib/nixlab 0700 root root -"
    "d /var/lib/nixlab/dumps 0700 root root -"
  ];
  virtualisation.oci-containers = {
    backend = "docker";
    containers.arcane = {
      image = site.apps.arcaneImage;
      ports = [ "${if site.setup.enable then site.guests.appsAddress else "127.0.0.1"}:9443:3552" ];
      volumes = [ "/var/run/docker.sock:/var/run/docker.sock" "/srv/arcane:/app/data" "/srv/compose:/srv/compose"
        "/srv/arcane/encryption-key:/run/secrets/encryption-key:ro"
        "/run/nixlab-arcane:/run/nixlab-arcane:ro" ]
        ++ lib.optionals site.setup.enable [
          "/var/lib/nixlab-secrets/tls.crt:/run/secrets/tls.crt:ro"
          "/var/lib/nixlab-secrets/tls.key:/run/secrets/tls.key:ro"
        ];
      environmentFiles = [ "/run/nixlab-arcane/environment" ];
      environment = {
        ARCANE_ENCRYPTION_KEY_FILE = "/run/secrets/encryption-key";
        ARCANE_DEFAULT_ADMIN_USERNAME = "admin";
        ARCANE_PROJECTS_DIRECTORY = "/srv/compose";
        ARCANE_APP_URL = if site.setup.enable then "https://${site.management.address}:${toString site.apps.arcanePort}" else "http://localhost:9443";
        ARCANE_TLS_ENABLED = if site.setup.enable then "true" else "false";
        PUID = "0"; PGID = "0";
      } // lib.optionalAttrs site.setup.enable {
        ARCANE_TLS_CERT_FILE = "/run/secrets/tls.crt";
        ARCANE_TLS_KEY_FILE = "/run/secrets/tls.key";
        ARCANE_TRUSTED_PROXIES = builtins.head (lib.splitString "/" site.guests.gatewayCIDR);
      };
    };
  };
  systemd.services.nixlab-arcane-key = {
    before = [ "docker-arcane.service" ]; requiredBy = [ "docker-arcane.service" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
    after = [ "systemd-tmpfiles-setup.service" ];
    script = ''${pkgs.python3}/bin/python3 ${../scripts/arcane_key.py} /srv/arcane/encryption-key'';
  };
  systemd.services.nixlab-arcane-prepare = {
    before = [ "docker-arcane.service" ]; requiredBy = [ "docker-arcane.service" ];
    after = [ "systemd-tmpfiles-setup.service" "nixlab-arcane-key.service" ];
    serviceConfig = { Type = "oneshot"; UMask = "0077"; };
    script = ''
      ${credentialTool}/bin/nixlab-arcane-credentials restore
      ${credentialTool}/bin/nixlab-arcane-credentials prepare
    '';
  };
  systemd.services.nixlab-arcane-confirm = {
    wantedBy = [ "multi-user.target" ]; after = [ "docker-arcane.service" ];
    requires = [ "docker-arcane.service" ]; partOf = [ "docker-arcane.service" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; TimeoutStartSec = 120; };
    script = "${credentialTool}/bin/nixlab-arcane-credentials confirm";
  };
  systemd.services.nixlab-arcane-expire = {
    serviceConfig.Type = "oneshot";
    script = "${credentialTool}/bin/nixlab-arcane-credentials expire";
  };
  systemd.timers.nixlab-arcane-expire = {
    wantedBy = [ "timers.target" ]; timerConfig = { OnBootSec = "1min"; OnUnitActiveSec = "1min"; };
  };
  systemd.services.docker-arcane.unitConfig.ConditionPathExists = lib.optionals site.setup.enable [ "/var/lib/nixlab-secrets/tls.crt" "/var/lib/nixlab-secrets/tls.key" ];
  # Published Docker ports traverse FORWARD, not the host INPUT firewall.
  # Gate the actual DNAT flow before starting Arcane, including after Docker restarts.
  systemd.services.nixlab-arcane-firewall = lib.mkIf site.setup.enable {
    description = "Allow only the hypervisor proxy to reach published Arcane";
    requires = [ "docker.service" ]; after = [ "docker.service" ];
    before = [ "docker-arcane.service" ]; requiredBy = [ "docker-arcane.service" ];
    partOf = [ "docker.service" ];
    path = [ pkgs.iptables ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      iptables -w -nL DOCKER-USER >/dev/null
      if ! iptables -w -nL NIXLAB-ARCANE >/dev/null 2>&1; then
        iptables -w -N NIXLAB-ARCANE
      fi
      iptables -w -F NIXLAB-ARCANE
      iptables -w -A NIXLAB-ARCANE -s ${lib.escapeShellArg (builtins.head (lib.splitString "/" site.guests.gatewayCIDR))} -j RETURN
      iptables -w -A NIXLAB-ARCANE -j DROP
      if ! iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst ${lib.escapeShellArg site.guests.appsAddress} --ctorigdstport 9443 -j NIXLAB-ARCANE 2>/dev/null; then
        iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst ${lib.escapeShellArg site.guests.appsAddress} --ctorigdstport 9443 -j NIXLAB-ARCANE
      fi
    '';
  };
  systemd.services.nixlab-demo = lib.mkIf site.apps.enableDemo {
    description = "Deploy version-controlled demo Compose project";
    wantedBy = [ "multi-user.target" ];
    requires = [ "docker.service" ]; after = [ "docker.service" ];
    restartTriggers = [ ../examples/demo/compose.yaml demoImage ];
    path = [ pkgs.docker projectTool pkgs.coreutils ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    unitConfig.ConditionPathExists = "/var/lib/nixlab-secrets/demo-db-password";
    script = ''
      test -s /var/lib/nixlab-secrets/demo-db-password
      # A managed immutable Compose file; edit Git/Nix, not this UI copy.
      ln -sfn ${../examples/demo/compose.yaml} /srv/compose/demo/compose.yaml
      docker load --input ${demoImage}
      nixlab-project deploy demo
    '';
  };
}

