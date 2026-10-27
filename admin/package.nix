{ pkgs }:
let
  source = pkgs.runCommand "nixlab-admin-source" { } ''
    mkdir -p $out
    cp -r ${./.}/. $out/
    cp ${../scripts/preflight.py} $out/preflight.py
  '';
in pkgs.writeShellApplication {
  name = "nixlab-admin";
  runtimeInputs = with pkgs; [ python3 nix nixos-rebuild git iproute2 util-linux openssl openssh coreutils systemd networkmanager incus-lts.client ];
  text = ''exec python3 ${source}/main.py "$@"'';
}
