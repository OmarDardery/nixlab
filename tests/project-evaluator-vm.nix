{ pkgs }:
let
  fixture = flakeText: pkgs.runCommand "nixlab-project-eval-fixture" { } ''
    mkdir -p "$out"
    cat > "$out/flake.nix" <<'EOF'
${flakeText}
EOF
    cat > "$out/flake.lock" <<'EOF'
{"nodes":{"root":{"inputs":{}}},"root":"root","version":7}
EOF
  '';
  good = fixture ''
    {
      outputs = { self }: {
        nixlabProjects = { schemaVersion = 1; projects = {}; };
      };
    }
  '';
  denied = fixture ''
    {
      outputs = { self }: {
        nixlabProjects = builtins.readFile /etc/shadow;
      };
    }
  '';
in pkgs.testers.runNixOSTest {
  name = "nixlab-project-evaluator";
  globalTimeout = 420;
  nodes.machine = { ... }: {
    system.stateVersion = "26.05";
    virtualisation.memorySize = 2048;
    environment.systemPackages = [ pkgs.python3 pkgs.nix pkgs.systemd ];
    nix.settings.experimental-features = [ "nix-command" "flakes" ];
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")
    try:
        machine.succeed("PYTHONPATH=${../admin}:${../scripts} ${pkgs.python3}/bin/python3 ${./project-evaluator-fixture.py} ${good} ${denied}")
    except Exception:
        print(machine.execute("journalctl -b --no-pager -n 150")[1])
        raise
  '';
}
