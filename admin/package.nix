{ pkgs }:
let
  source = pkgs.runCommand "nixlab-admin-source" { } ''
    mkdir -p $out
    cp -r ${./.}/. $out/
    chmod -R u+w $out
    cp ${../scripts/preflight.py} $out/preflight.py
    cp ${../scripts/backup.py} $out/backup.py
    cp ${../scripts/reconcile.py} $out/reconcile.py
    cp ${../scripts/locking.py} $out/locking.py
    cp ${../iso/install_disk.py} $out/disk_tools.py
    cp ${pkgs.writeText "nixlab-default-settings.json" (builtins.toJSON (import ../site-defaults.nix))} $out/default-settings.json
    cp -r ${../docs} $out/docs
  '';
in pkgs.writeShellApplication {
  name = "nixlab-admin";
  runtimeInputs = with pkgs; [ (python3.withPackages (ps: [ ps.markdown ])) nix nixos-rebuild git iproute2 util-linux openssl openssh coreutils systemd networkmanager incus-lts.client age parted e2fsprogs ];
  text = ''exec python3 ${source}/main.py "$@"'';
}
