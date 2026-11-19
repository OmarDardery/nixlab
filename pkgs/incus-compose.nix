# Immutable upstream GitHub release, official asset SHA-256 verified by Nix.
# Current locked Nixpkgs Go compiler is behind upstream's Go patch minimum.
# Do not weaken the upstream go.mod security/toolchain requirement to build it.
{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "incus-compose";
  version = "1.4.1";
  src = pkgs.fetchurl {
    url = "https://github.com/lxc/incus-compose/releases/download/v1.4.1/incus-compose_1.4.1_linux_amd64.tar.gz";
    sha256 = "fc9a25bbe8dada4d0710dc64e6116e2d0ac73e9f08d5a09fc10c04a3128b9cf2";
  };
  dontUnpack = true;
  nativeBuildInputs = [ pkgs.gnutar pkgs.gzip pkgs.findutils ];
  installPhase = ''
    runHook preInstall
    mkdir -p extracted "$out/bin"
    tar -xzf "$src" -C extracted
    binary="$(find extracted -type f -name incus-compose -print -quit)"
    test -n "$binary"
    install -m 0755 "$binary" "$out/bin/incus-compose"
    runHook postInstall
  '';
  meta = {
    description = "Checksum-pinned Incus Compose OCI orchestrator";
    homepage = "https://github.com/lxc/incus-compose";
    license = pkgs.lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
    mainProgram = "incus-compose";
  };
}
