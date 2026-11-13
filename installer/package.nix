{ pkgs }:
let
  platform = pkgs.lib.cleanSourceWith {
    src = ../.;
    filter = path: type:
      let relative = pkgs.lib.removePrefix (toString ../. + "/") (toString path);
          top = builtins.head (pkgs.lib.splitString "/" relative);
      in type != "symlink" && builtins.baseNameOf path != "__pycache__"
        && (toString path == toString ../. || builtins.elem top [
          "admin" "installer" "scripts" "helpers" "lib" "modules" "platform-modules"
          "guests" "tests" "docs" "examples" "configuration.nix" "configuration-bootstrap.nix"
          "hardware-configuration.nix" "flake.nix" "flake.lock" "site.nix" "site-defaults.nix"
          "README.md" "VERSION" ".gitignore"
        ]);
  };
  source = pkgs.runCommand "nixlab-installer-source" { } ''
  mkdir -p $out
  cp -r ${../installer} $out/installer
  cp -r ${../admin} $out/admin
  chmod -R u+w $out/admin
  cp ${../scripts/preflight.py} $out/admin/preflight.py
  cp ${../scripts/backup.py} $out/admin/backup.py
  cp ${../scripts/backup_plan.py} $out/admin/backup_plan.py
  cp ${../scripts/locking.py} $out/admin/locking.py
  cp ${../scripts/foundation.py} $out/admin/foundation.py
  cp ${../scripts/incus_client.py} $out/admin/incus_client.py
  cp ${../scripts/backup_health.py} $out/admin/backup_health.py
  cp ${../scripts/reconcile.py} $out/admin/reconcile.py
  cp ${../scripts/infrastructure.py} $out/admin/infrastructure.py
  cp ${../scripts/guest_deploy.py} $out/admin/guest_deploy.py
  cp ${pkgs.writeText "nixlab-default-settings.json" (builtins.toJSON (import ../site-defaults.nix))} $out/admin/default-settings.json
'';
  runtimeInputs = with pkgs; [ python3 git openssl openssh shadow nix nixos-rebuild iproute2 util-linux kmod coreutils systemd networkmanager incus-lts.client ];
  install = pkgs.writeShellApplication {
  name = "nixlab-install";
  inherit runtimeInputs;
  text = ''
    export NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
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
      export NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
      exec python3 ${source}/installer/update.py "$@"
    '';
  };
in pkgs.symlinkJoin {
  name = "nixlab-installer";
  paths = [ install update ];
}

