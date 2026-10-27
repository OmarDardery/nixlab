{ pkgs }:
pkgs.writeShellApplication {
  name = "homelab";
  runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client ];
  text = ''exec python3 ${./homelab.py} "$@"'';
}
