{ pkgs }:
let
  platform = pkgs.lib.cleanSourceWith {
    src = ../.;
    filter = path: type:
      let relative = pkgs.lib.removePrefix (toString ../. + "/") (toString path);
          top = builtins.head (pkgs.lib.splitString "/" relative);
      in type != "symlink" && builtins.baseNameOf path != "__pycache__"
        && (toString path == toString ../. || builtins.elem top [
          "admin" "installer" "scripts" "helpers" "lib" "modules" "platform-modules" "iso"
          "guests" "tests" "docs" "examples" "configuration.nix" "configuration-bootstrap.nix"
          "hardware-configuration.nix" "flake.nix" "flake.lock" "site.nix" "site-defaults.nix"
          "README.md" "VERSION" "install.sh" ".gitignore"
        ]);
  };
  source = pkgs.runCommand "nixlab-installer-source" { } ''
  mkdir -p $out
  cp -r ${../installer} $out/installer
  cp -r ${../admin} $out/admin
  chmod -R u+w $out/admin
  cp ${../scripts/preflight.py} $out/admin/preflight.py
  cp ${../scripts/backup.py} $out/admin/backup.py
  cp ${../scripts/locking.py} $out/admin/locking.py
  cp ${../scripts/foundation.py} $out/admin/foundation.py
  cp ${../scripts/incus_client.py} $out/admin/incus_client.py
  cp ${../scripts/backup_health.py} $out/admin/backup_health.py
  cp ${../scripts/reconcile.py} $out/admin/reconcile.py
  cp ${pkgs.writeText "nixlab-default-settings.json" (builtins.toJSON (import ../site-defaults.nix))} $out/admin/default-settings.json
'';
in pkgs.writeShellApplication {
  name = "nixlab-install";
  runtimeInputs = with pkgs; [ python3 git openssl openssh shadow nix nixos-rebuild iproute2 util-linux kmod coreutils systemd networkmanager incus-lts.client ];
  text = ''exec python3 ${source}/installer/install.py --checkout ${platform} "$@"'';
}

