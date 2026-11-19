# Reproducible upstream incus-compose source build, frozen to audited v1.3.5.
# Nixpkgs used by the host predates this package; never invoke an unpinned
# curl/installer script or silently fall back to a host-provided command.
{ pkgs }:
pkgs.buildGoModule (finalAttrs: {
  pname = "incus-compose";
  version = "1.3.5";
  src = pkgs.fetchFromGitHub {
    owner = "lxc";
    repo = "incus-compose";
    rev = "v${finalAttrs.version}";
    hash = "sha256-WLwhEGBZ3/DMjaVUTwyR+DKoQGRcu4q7qjfiBKDCj9Q=";
  };
  vendorHash = "sha256-A6VSF2gTR9NiLT3yMk1jp5neOqrxvWC5mPvVW1OCS8Y=";
  env.CGO_ENABLED = "0";
  ldflags = [
    "-s" "-w"
    "-X=github.com/lxc/incus-compose/cmd/incus-compose/version.Version=${finalAttrs.version}"
    "-X=github.com/lxc/incus-compose/cmd/incus-compose/DefaultHealthdImage=ghcr.io/lxc/incus-compose/ic-healthd:${finalAttrs.version}"
  ];
  doCheck = false; # Integration is exercised by NixLab's disposable VM gate.
  meta = {
    description = "Pinned daemonless Compose adapter for Incus";
    homepage = "https://github.com/lxc/incus-compose";
    license = pkgs.lib.licenses.asl20;
    platforms = pkgs.lib.platforms.linux;
    mainProgram = "incus-compose";
  };
})
