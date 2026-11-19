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
  runtimeInputs = with pkgs; [ (python3.withPackages (ps: [ ps.markdown ])) nix nixos-rebuild git iproute2 util-linux openssl openssh shadow coreutils systemd networkmanager incus-lts.client age parted e2fsprogs pamtester buildah bubblewrap incus-compose ];
  text = ''
    NIXLAB_WORKER_RUNTIME="$(dirname "$(dirname "$(readlink -f "$0")")")"
    export NIXLAB_WORKER_RUNTIME
    export NIXLAB_PROJECT_EVAL_LIB=${../lib/project-workspace.nix}
    export NIXLAB_PROJECT_NIXPKGS_LIB=${pkgs.path}/lib
    export NIXLAB_OCI_BUILDAH_BIN=${pkgs.buildah}/bin/buildah
    export NIXLAB_OCI_BWRAP_BIN=${pkgs.bubblewrap}/bin/bwrap
    export NIXLAB_OCI_COMPOSE_BIN=${pkgs.incus-compose}/bin/incus-compose
    export NIXLAB_OCI_PATH=${pkgs.lib.makeBinPath (with pkgs; [ buildah bubblewrap incus-compose coreutils gnutar gzip xz bash shadow ])}
    exec python3 ${source}/main.py "$@"
  '';
}
