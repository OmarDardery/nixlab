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
in {
  virtualisation.docker = {
    enable = true;
    autoPrune.enable = false;
    daemon.settings = { "log-driver" = "local"; "log-opts" = { "max-size" = "10m"; "max-file" = "3"; }; };
  };
  environment.systemPackages = [ projectTool pkgs.git pkgs.docker-compose ];
  systemd.tmpfiles.rules = [
    "d /srv/compose 0700 root root -"
    "d /srv/compose/demo 0700 root root -"
    "d /srv/portainer 0700 root root -"
    "d /var/lib/nixlab 0700 root root -"
    "d /var/lib/nixlab/dumps 0700 root root -"
  ];
  virtualisation.oci-containers = {
    backend = "docker";
    containers.portainer = {
      image = site.apps.portainerImage;
      ports = [ "${if site.setup.enable then site.guests.appsAddress else "127.0.0.1"}:9443:9443" ];
      volumes = [ "/var/run/docker.sock:/var/run/docker.sock" "/srv/portainer:/data"
        "/var/lib/nixlab-secrets/portainer-admin-password:/run/secrets/admin-password:ro" ]
        ++ lib.optionals site.setup.enable [
          "/var/lib/nixlab-secrets/tls.crt:/run/secrets/tls.crt:ro"
          "/var/lib/nixlab-secrets/tls.key:/run/secrets/tls.key:ro"
        ];
      cmd = [ "--admin-password-file" "/run/secrets/admin-password" "--host" "unix:///var/run/docker.sock" ] ++ lib.optionals site.setup.enable [ "--sslcert" "/run/secrets/tls.crt" "--sslkey" "/run/secrets/tls.key" ];
    };
  };
  systemd.services.docker-portainer.unitConfig.ConditionPathExists = [ "/var/lib/nixlab-secrets/portainer-admin-password" ]
    ++ lib.optionals site.setup.enable [ "/var/lib/nixlab-secrets/tls.crt" "/var/lib/nixlab-secrets/tls.key" ];
  # Published Docker ports traverse FORWARD, not the host INPUT firewall.
  # Gate the actual DNAT flow before starting Portainer, including after Docker restarts.
  systemd.services.nixlab-portainer-firewall = lib.mkIf site.setup.enable {
    description = "Allow only the hypervisor proxy to reach published Portainer";
    requires = [ "docker.service" ]; after = [ "docker.service" ];
    before = [ "docker-portainer.service" ]; requiredBy = [ "docker-portainer.service" ];
    partOf = [ "docker.service" ];
    path = [ pkgs.iptables ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      iptables -w -nL DOCKER-USER >/dev/null
      if ! iptables -w -nL NIXLAB-PORTAINER >/dev/null 2>&1; then
        iptables -w -N NIXLAB-PORTAINER
      fi
      iptables -w -F NIXLAB-PORTAINER
      iptables -w -A NIXLAB-PORTAINER -s ${lib.escapeShellArg (builtins.head (lib.splitString "/" site.guests.gatewayCIDR))} -j RETURN
      iptables -w -A NIXLAB-PORTAINER -j DROP
      if ! iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst ${lib.escapeShellArg site.guests.appsAddress} --ctorigdstport 9443 -j NIXLAB-PORTAINER 2>/dev/null; then
        iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst ${lib.escapeShellArg site.guests.appsAddress} --ctorigdstport 9443 -j NIXLAB-PORTAINER
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
