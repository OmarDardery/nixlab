{ pkgs }:
let
  tool = import ../admin/package.nix { inherit pkgs; };
in pkgs.testers.runNixOSTest {
  name = "nixlab-oci-isolated-builder";
  globalTimeout = 900;
  nodes.host = { ... }: {
    virtualisation = { memorySize = 3072; diskSize = 7168; };
    users.groups.nixlab-oci = { };
    users.users.nixlab-oci = {
      isSystemUser = true;
      group = "nixlab-oci";
      home = "/var/empty";
      createHome = false;
    };
    systemd.tmpfiles.rules = [
      "d /srv/nixlab 0700 root root -"
      "d /var/lib/nixlab-oci 0711 root root -"
      "d /var/lib/nixlab-oci/jobs 0711 root root -"
    ];
    environment.systemPackages = [ tool pkgs.python3 pkgs.nix pkgs.buildah
      pkgs.bubblewrap pkgs.incus-compose pkgs.util-linux ];
    environment.variables = {
      NIXLAB_OCI_COMPOSE_BIN = "${pkgs.incus-compose}/bin/incus-compose";
      NIXLAB_OCI_BUILDAH_BIN = "${pkgs.buildah}/bin/buildah";
      NIXLAB_OCI_BWRAP_BIN = "${pkgs.bubblewrap}/bin/bwrap";
      NIXLAB_OCI_PATH = pkgs.lib.makeBinPath (with pkgs; [
        buildah bubblewrap incus-compose coreutils gnutar gzip xz bash shadow
      ]);
    };
  };
  testScript = ''
    host.start()
    host.wait_for_unit("multi-user.target")
    host.succeed("PYTHONPATH=${../admin}:${../scripts} python3 ${./oci-build-integration.py}", timeout=720)
  '';
}
