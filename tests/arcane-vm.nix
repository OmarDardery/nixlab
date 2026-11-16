# Actual upstream OCI image, fetched by CI before sandboxed VM execution.
{ pkgs, arcaneArchive, arcaneImage }:
let
  defaults = import ../site-defaults.nix;
  site = defaults // {
    setup = defaults.setup // { enable = true; };
    apps = defaults.apps // { enable = true; arcanePort = 9443; inherit arcaneImage; };
    management = defaults.management // { address = "127.0.0.2"; };
    guests = defaults.guests // { appsAddress = "127.0.0.2"; gatewayCIDR = "127.0.0.1/8"; };
  };
  probeImage = pkgs.dockerTools.buildLayeredImage {
    name = "nixlab-arcane-probe"; tag = "1";
    contents = [ pkgs.busybox ];
    config.Cmd = [ "/bin/sh" "-c" "mkdir -p /data; test -e /data/record || echo persistent-arcane-test > /data/record; sleep 3600" ];
  };
in pkgs.testers.runNixOSTest {
  name = "nixlab-arcane-runtime";
  globalTimeout = 600;
  node.pkgsReadOnly = false;
  nodes.machine = { ... }: {
    imports = [ ../modules/docker-host.nix ];
    _module.args.site = site;
    virtualisation.memorySize = 2048;
    virtualisation.diskSize = 8192;
    environment.systemPackages = [ pkgs.python3 pkgs.openssl ];
    virtualisation.oci-containers.containers.arcane = { imageFile = arcaneArchive; pull = "never"; };
    systemd.services.arcane-test-inputs = {
      requires = [ "docker.service" ]; after = [ "docker.service" "systemd-tmpfiles-setup.service" ];
      before = [ "docker-arcane.service" ]; requiredBy = [ "docker-arcane.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
      path = [ pkgs.python3 pkgs.openssl pkgs.docker ];
      script = ''
        python3 - <<'PY'
        from pathlib import Path
        import secrets, subprocess
        root=Path('/var/lib/nixlab-arcane-test');root.mkdir(mode=0o700,exist_ok=True)
        secret=Path('/var/lib/nixlab-secrets');secret.mkdir(mode=0o700,exist_ok=True)
        if not (secret/'arcane-admin-password').exists():
            value=secrets.token_hex(24)+'Aa1!'
            (secret/'arcane-admin-password').write_text(value)
            (root/'login-password').write_text(value)
        if not (secret/'tls.crt').exists():
            subprocess.run(['openssl','req','-new','-newkey','rsa:2048','-nodes','-x509','-days','1',
                '-subj','/CN=apps.internal','-addext','subjectAltName=DNS:apps.internal,IP:127.0.0.2',
                '-keyout',str(secret/'tls.key'),'-out',str(secret/'tls.crt')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        PY
        docker load --input ${probeImage} >/dev/null
      '';
    };
  };
  testScript = ''
    machine.start()
    machine.wait_for_unit("docker-arcane.service")
    machine.wait_for_open_port(9443, "127.0.0.2")
    machine.succeed("python3 ${./arcane-client.py} prepare")
    for boot in range(2):
        machine.reboot()
        machine.wait_for_unit("docker-arcane.service")
        machine.wait_for_open_port(9443, "127.0.0.2")
        machine.succeed("python3 ${./arcane-client.py} verify")
    machine.succeed("umask 077; systemctl stop docker-arcane.service; tar -C /srv/arcane -czf /var/lib/arcane-protected-backup.tgz .; mv /srv/arcane /srv/arcane-before-restore; install -d -m 0700 /srv/arcane; tar -C /srv/arcane -xzf /var/lib/arcane-protected-backup.tgz; systemctl start docker-arcane.service")
    machine.wait_for_open_port(9443, "127.0.0.2")
    machine.succeed("python3 ${./arcane-client.py} verify")
  '';
}
