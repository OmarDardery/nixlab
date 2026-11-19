{ pkgs }:
let
  python = pkgs.python3.withPackages (ps: [ ps.cryptography ]);
  fixture = ./native-secrets-recovery-vm-fixture.py;
  runtime = "PYTHONPATH=${../admin}:${../scripts} ${python}/bin/python3 ${fixture}";
  baseline = { ... }: {
    system.stateVersion = "26.05";
    virtualisation = { memorySize = 1024; diskSize = 4096; };
    networking.useDHCP = false;
    environment.systemPackages = [ python pkgs.age pkgs.sops ];
  };
in
pkgs.testers.runNixOSTest {
  name = "nixlab-secrets-offline-nixos-restore";
  globalTimeout = 600;
  nodes.source = baseline;
  nodes.replacement = { ... }: {
    imports = [ baseline ];
    systemd.services.nixlab-recovery-consumer = {
      description = "Disposable NixLab recovery secret consumer";
      environment.PYTHONPATH = "${../admin}:${../scripts}";
      path = [ pkgs.sops pkgs.age ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "root";
        UMask = "0077";
      };
      script = "exec ${python}/bin/python3 ${fixture} consume";
    };
  };
  testScript = ''
    source.start()
    replacement.start()
    source.wait_for_unit("multi-user.target")
    replacement.wait_for_unit("multi-user.target")

    # No private age keys, restic password or secret values originate in the
    # Nix store; each value is generated at runtime in the first NixOS VM.
    source.succeed("${runtime} produce", timeout=120)
    replacement.succeed("${runtime} recover", timeout=120)

    # Real systemd unit activation, decrypting only inside private /run
    # staging and writing only a SHA-256 digest for external observation.
    replacement.succeed("systemctl start nixlab-recovery-consumer.service", timeout=120)
    replacement.succeed("${runtime} verify-first", timeout=30)

    # Changed encrypted environment variable, then a REAL systemd restart.
    replacement.succeed("${runtime} rotate", timeout=120)
    replacement.succeed("systemctl restart nixlab-recovery-consumer.service", timeout=120)
    replacement.succeed("${runtime} verify-rotated", timeout=30)
    replacement.succeed("systemctl is-active nixlab-recovery-consumer.service")
  '';
}
