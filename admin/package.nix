{ pkgs }:
let
  source = pkgs.runCommand "nixlab-admin-source" { } ''
    cp -r ${import ./source.nix { inherit pkgs; }} $out
    chmod -R u+w $out
    cp -r ${../docs} $out/docs
    cp ${../helpers/sources.json} $out/helper-sources.json
    cp ${../helpers/compatibility.json} $out/helper-compatibility.json
    cp ${../VERSION} $out/VERSION
  '';
in pkgs.writeShellApplication {
  name = "nixlab-admin";
  runtimeInputs = with pkgs; [ (python3.withPackages (ps: [ ps.markdown ])) nix nixos-rebuild git iproute2 util-linux openssl openssh shadow coreutils systemd networkmanager incus-lts.client age parted e2fsprogs ];
  text = ''
    NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
    export NIXLAB_WORKER_RUNTIME
    exec python3 ${source}/main.py "$@"
  '';
}
