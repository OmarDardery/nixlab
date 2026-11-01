{ pkgs }:
let command = pkgs.writeShellApplication {
  name = "nixlab";
  runtimeInputs = [ pkgs.python3 pkgs.incus-lts.client (import ../admin/package.nix { inherit pkgs; }) ];
  text = ''exec python3 ${./homelab.py} "$@"'';
};
in pkgs.symlinkJoin {
  name = "nixlab-cli";
  paths = [ command ];
  postBuild = ''ln -s nixlab $out/bin/homelab'';
}

