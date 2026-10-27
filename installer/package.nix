{ pkgs }:
let source = pkgs.runCommand "nixlab-installer-source" { } ''
  mkdir -p $out
  cp -r ${../installer} $out/installer
  cp -r ${../admin} $out/admin
'';
in pkgs.writeShellApplication {
  name = "nixlab-install";
  runtimeInputs = with pkgs; [ python3 git openssl nix nixos-rebuild iproute2 coreutils ];
  text = ''exec python3 ${source}/installer/install.py "$@"'';
}
