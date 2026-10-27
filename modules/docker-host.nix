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
    text = ''exec python3 ${../scripts/project.py} "$@"'';
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
      ports = [ "127.0.0.1:9443:9443" ];
      volumes = [ "/var/run/docker.sock:/var/run/docker.sock" "/srv/portainer:/data"
        "/var/lib/nixlab-secrets/portainer-admin-password:/run/secrets/admin-password:ro" ];
      cmd = [ "--admin-password-file" "/run/secrets/admin-password" "--host" "unix:///var/run/docker.sock" ];
    };
  };
  systemd.services.docker-portainer.unitConfig.ConditionPathExists = "/var/lib/nixlab-secrets/portainer-admin-password";
  systemd.services.nixlab-demo = lib.mkIf site.apps.enableDemo {
    description = "Deploy version-controlled demo Compose project";
    wantedBy = [ "multi-user.target" ];
    requires = [ "docker.service" ]; after = [ "docker.service" ];
    restartTriggers = [ ../examples/demo/compose.yaml demoImage ];
    path = [ pkgs.docker projectTool pkgs.coreutils ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      test -s /var/lib/nixlab-secrets/demo-db-password
      # A managed immutable Compose file; edit Git/Nix, not this UI copy.
      ln -sfn ${../examples/demo/compose.yaml} /srv/compose/demo/compose.yaml
      docker load --input ${demoImage}
      nixlab-project deploy demo
    '';
  };
}
