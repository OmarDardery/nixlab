# Actual guest-only sops-nix activation with a runtime-generated guest age key.
# No private key or plaintext is ever included in the Nix store or test source.
{ pkgs, sops-nix }:
let
  python = pkgs.python3;
  fixture = ./native-guest-sops-vm-fixture.py;
  run = phase: "PYTHONPATH=${../admin} ${python}/bin/python3 ${fixture} ${phase}";
  fragment = import ../lib/project-guest-secrets.nix { lib = pkgs.lib; };
in
pkgs.testers.runNixOSTest {
  name = "nixlab-guest-owned-sops-activation";
  globalTimeout = 600;
  nodes.guest = { ... }: {
    system.stateVersion = "26.05";
    virtualisation = { memorySize = 1024; diskSize = 4096; };
    networking.useDHCP = false;
    imports = [
      sops-nix.nixosModules.sops
      (fragment {
        project = "alpha";
        instance = "web";
        sopsFile = "/var/lib/nixlab-guest-sops/alpha/secrets.enc.yaml";
        guestAgeKeyFile = "/var/lib/sops-nix/guest.agekey";
        bindings = {
          TOKEN = {
            key = "API_TOKEN";
            instance = "web";
            backend = "nixos-sops";
            destination = "/run/secrets/TOKEN";
            restartUnits = [ "nixlab-guest-sops-consumer.service" ];
          };
        };
      })
    ];
    # The test creates a fresh guest key and encrypts a randomly generated
    # secret on the GUEST before sops-install-secrets starts for the first time.
    systemd.services.nixlab-guest-sops-provision = {
      description = "Test-only guest SOPS enrollment before activation";
      wantedBy = [ "sysinit.target" ];
      requiredBy = [ "sops-install-secrets.service" ];
      before = [ "sops-install-secrets.service" ];
      after = [ "local-fs.target" ];
      unitConfig.DefaultDependencies = "no";
      path = [ pkgs.age pkgs.sops ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
      script = "exec ${run "provision"}";
    };
    systemd.services.nixlab-guest-sops-consumer = {
      description = "Real guest SOPS secret consumer with automatic restart";
      wantedBy = [ "multi-user.target" ];
      requires = [ "sops-install-secrets.service" ];
      after = [ "sops-install-secrets.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
      script = "exec ${run "consume"}";
    };
    environment.systemPackages = [ python pkgs.age pkgs.sops ];
  };
  testScript = ''
    guest.start()
    guest.wait_for_unit("multi-user.target")
    guest.wait_for_unit("sops-install-secrets.service")
    guest.wait_for_unit("nixlab-guest-sops-consumer.service")
    guest.succeed("${run "verify-first"}", timeout=60)

    guest.succeed("${run "rotate"}", timeout=60)
    # A real sops-nix systemd activation must propagate the changed value
    # and trigger the declared consumer restart; no manual consumer restart.
    guest.succeed("systemctl restart sops-install-secrets.service", timeout=120)
    guest.succeed("${run "verify-rotated"}", timeout=60)
    guest.succeed("systemctl is-active nixlab-guest-sops-consumer.service")
  '';
}
