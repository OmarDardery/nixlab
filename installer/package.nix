{ pkgs }:
let source = pkgs.runCommand "nixlab-installer-source" { } ''
  mkdir -p $out
  cp -r ${../installer} $out/installer
  cp -r ${../admin} $out/admin
  chmod -R u+w $out/admin
  cp ${../scripts/preflight.py} $out/admin/preflight.py
  cp ${../scripts/backup.py} $out/admin/backup.py
  cp ${../scripts/locking.py} $out/admin/locking.py
'';
in pkgs.writeShellApplication {
  name = "nixlab-install";
  runtimeInputs = with pkgs; [ python3 git openssl nix nixos-rebuild iproute2 coreutils systemd ];
  text = ''exec python3 ${source}/installer/install.py "$@"'';
}
