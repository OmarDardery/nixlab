{ pkgs }:
let
  pins = builtins.fromJSON (builtins.readFile ./sources.json);
  fetch = pin: pkgs.fetchFromGitHub {
    owner = "community-scripts"; repo = builtins.elemAt (pkgs.lib.splitString "/" pin.repository) 1;
    rev = pin.revision; hash = pin.hash;
  };
  core = fetch pins.core;
  scripts = fetch pins.scripts;
  runner = pkgs.runCommand "nixlab-helper-runner" { } ''
    mkdir -p $out
    cp ${./runner.py} $out/runner.py
    cp ${../scripts/locking.py} $out/locking.py
  '';
in pkgs.writeShellApplication {
  name = "nixlab-helpers";
  runtimeInputs = with pkgs; [ python3 bash incus-lts.client curl wget jq gawk gnused gnugrep coreutils
    findutils util-linux newt ncurses git openssl gnutar gzip xz unzip procps iproute2 iputils hostname systemd ];
  text = ''
    export NIXLAB_HELPER_ENGINE=${core}
    export NIXLAB_HELPER_SCRIPTS=${scripts}
    export NIXLAB_HELPER_METADATA=${./.}
    exec python3 ${runner}/runner.py "$@"
  '';
}
