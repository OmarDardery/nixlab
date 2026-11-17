{ pkgs }:
let
  platform = pkgs.lib.cleanSourceWith {
    src = ../.;
    filter = path: type:
      let relative = pkgs.lib.removePrefix (toString ../. + "/") (toString path);
          top = builtins.head (pkgs.lib.splitString "/" relative);
      in type != "symlink" && builtins.baseNameOf path != "__pycache__"
        && (toString path == toString ../. || builtins.elem top [
          "admin" "installer" "scripts" "helpers" "lib" "modules"
          "guests" "tests" "docs" "examples" "configuration.nix" "configuration-bootstrap.nix"
          "hardware-configuration.nix" "flake.nix" "flake.lock" "site.nix" "site-defaults.nix"
          "README.md" "VERSION" ".gitignore"
        ]);
  };
  source = pkgs.runCommand "nixlab-installer-source" { } ''
    mkdir -p $out
    cp -r ${../installer} $out/installer
    cp -r ${import ../admin/source.nix { inherit pkgs; }} $out/admin
  '';
  runtimeInputs = with pkgs; [ python3 git openssl openssh shadow nix nixos-rebuild iproute2 util-linux kmod coreutils systemd networkmanager incus-lts.client ];
  install = pkgs.writeShellApplication {
  name = "nixlab-install";
  inherit runtimeInputs;
  text = ''
    NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
    export NIXLAB_WORKER_RUNTIME
    exec python3 ${source}/installer/install.py --checkout ${platform} "$@"
  '';
  };
  update = pkgs.writeShellApplication {
    name = "nixlab-update";
    inherit runtimeInputs;
    text = ''
      if [[ "''${1:-}" != --help && "''${1:-}" != -h && "$(id -u)" != 0 ]]; then
        exec /run/wrappers/bin/sudo -- "$0" "$@"
      fi
      NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
    export NIXLAB_WORKER_RUNTIME
      exec python3 ${source}/installer/update.py "$@"
    '';
  };
in pkgs.symlinkJoin {
  name = "nixlab-installer";
  paths = [ install update ];
  passthru.tests.recovery-guard = pkgs.runCommand "nixlab-installer-recovery-guard" { } ''
    export PYTHONPATH=${source}/admin
    export NIXLAB_STATE="$TMPDIR/state"
    ${pkgs.python3}/bin/python - <<'PY'
    import json
    import os
    from pathlib import Path
    from deployments import Deployments
    from recovery_guard import require_normal

    state = Path(os.environ['NIXLAB_STATE'])
    state.mkdir()
    journal = state / 'replacement-recovery.json'
    journal.write_text(json.dumps({'format': 1, 'phase': 'planned'}))
    try:
        Deployments(state=state).submit('a' * 32)
    except ValueError as error:
        assert 'Unfinished recovery journal' in str(error), str(error)
    else:
        raise AssertionError('Packaged launcher allowed deployment during recovery')
    journal.write_text(json.dumps({'format': 1, 'phase': 'complete'}))
    require_normal()
    PY
    touch "$out"
  '';
}

