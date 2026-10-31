{ pkgs }:
let
  lib = pkgs.lib;
  base = import ./site.nix;
  site = base // {
    apps = base.apps // { enable = false; };
    management = base.management // { interface = "eth1"; configure = true; };
    connectivity = base.connectivity // { trustedLANAccess = true; };
  };
  init = pkgs.pkgsStatic.stdenv.mkDerivation {
    name = "nixlab-fixture-init";
    dontUnpack = true;
    buildPhase = ''
      cat > init.c <<'EOF'
      #include <signal.h>
      #include <unistd.h>
      #include <sys/wait.h>
      static void stop(int s) { _exit(0); }
      static void reap(int s) { while (waitpid(-1, 0, WNOHANG) > 0) {} }
      int main(void) {
        signal(SIGPWR, stop); signal(SIGTERM, stop); signal(SIGCHLD, reap);
        for (;;) pause();
      }
      EOF
      $CC -static init.c -o init
    '';
    installPhase = "mkdir -p $out; cp init $out/init";
  };
  image = pkgs.runCommand "nixlab-fixture-container" { nativeBuildInputs = [ pkgs.gnutar pkgs.xz ]; } ''
    mkdir -p rootfs/{bin,sbin,etc,proc,sys,dev,tmp,run,var/lib/test,root} $out
    cp ${pkgs.pkgsStatic.busybox}/bin/busybox rootfs/bin/busybox
    for name in sh ip cat mkdir sleep nc httpd test; do ln -s busybox rootfs/bin/$name; done
    cp ${init}/init rootfs/sbin/init
    chmod 1777 rootfs/tmp
    cat > metadata.yaml <<'EOF'
    architecture: x86_64
    creation_date: 1
    properties:
      description: Nixlab disposable integration fixture
      os: fixture
    EOF
    tar --owner=0 --group=0 --numeric-owner -cJf $out/image.tar.xz metadata.yaml rootfs
  '';
in pkgs.testers.runNixOSTest {
  name = "nixlab-host-foundation";
  globalTimeout = 900;
  node.specialArgs = { inherit site; appImage = null; installedNetworking.networkManager = false; };
  nodes.host = { ... }: {
    imports = [ ../modules/storage.nix ../modules/networking.nix ../modules/incus.nix ../modules/instance-schema.nix ];
    virtualisation = { memorySize = 2048; diskSize = 4096; };
    networking.hostName = "foundation-test";
    services.openssh = { enable = true; openFirewall = false; };
    # This fixture exercises real containers; it makes no KVM/VM-guest claim.
    systemd.services.nixlab-kvm-check = { serviceConfig.Type = "oneshot"; script = "true"; };
    environment.systemPackages = [ pkgs.python3 pkgs.incus-lts.client pkgs.util-linux ];
  };
  nodes.client = { ... }: {
    networking.interfaces.eth1.ipv4.addresses = [ { address = "192.0.2.11"; prefixLength = 24; } ];
    networking.interfaces.eth1.ipv4.routes = [ { address = "10.77.0.0"; prefixLength = 24; via = "192.0.2.10"; } ];
    environment.systemPackages = [ pkgs.curl ];
  };
  testScript = ''
    import json
    start_all()
    host.wait_for_unit("incus-preseed.service")
    host.wait_for_unit("nixlab-instances.service")
    client.wait_for_unit("network.target")
    host.succeed("incus image import ${image}/image.tar.xz --alias fixture")
    host.succeed("incus init fixture native --project lab --profile base --config limits.cpu=1")
    host.succeed("incus start native --project lab")
    host.succeed("incus exec native --project lab --disable-stdin -- /bin/sh -c 'ip link set eth0 up; ip addr add 10.77.0.20/24 dev eth0; ip route add default via 10.77.0.1'")
    host.succeed("incus exec native --project lab --disable-stdin -- /bin/sh -c 'echo preserved > /var/lib/test/index.html'")
    host.succeed("incus exec native --project lab --disable-stdin -- /bin/httpd -p 8080 -h /var/lib/test")
    client.wait_until_succeeds("curl --fail --max-time 3 http://10.77.0.20:8080 | grep preserved")
    host.fail("incus exec native --project lab --disable-stdin -- /bin/sh -c 'nc -w 2 192.0.2.10 22 </dev/null'")

    # Incus-owned instances/profiles remain mutable across platform reconciliation.
    host.succeed("incus profile create native-profile --project lab")
    host.succeed("incus profile set native-profile user.example=native --project lab")
    host.succeed("systemctl restart incus-preseed.service nixlab-instances.service")
    assert host.succeed("incus config get native limits.cpu --project lab").strip() == "1"
    assert host.succeed("incus profile get native-profile user.example --project lab").strip() == "native"

    # Synthetic mknod does not produce a USB uevent. Exercise explicit Incus
    # attachment/removal across restarts; physical hotplug is a hardware trial.
    host.succeed("mkdir -p /dev/nixlab")
    host.succeed("mknod /dev/nixlab/example c 1 3")
    host.succeed("timeout --kill-after=5s 180 incus stop native --project lab --timeout 120", timeout=200)
    host.succeed("timeout --kill-after=5s 30 incus config device add native serial unix-char source=/dev/nixlab/example path=/dev/example required=false gid=20 mode=0660 --project lab", timeout=45)
    host.succeed("timeout --kill-after=5s 120 incus start native --project lab", timeout=140)
    host.succeed("timeout --kill-after=5s 30 incus exec native --project lab --disable-stdin -- /bin/test -c /dev/example", timeout=45)
    host.succeed("timeout --kill-after=5s 30 incus exec native --project lab --disable-stdin --user 1000 --group 20 -- /bin/sh -c 'echo allowed >/dev/example'", timeout=45)
    host.succeed("timeout --kill-after=5s 180 incus stop native --project lab --timeout 120", timeout=200)
    host.succeed("timeout --kill-after=5s 30 incus config device remove native serial --project lab", timeout=45)
    host.succeed("timeout --kill-after=5s 120 incus start native --project lab", timeout=140)
    host.succeed("sh -c 'timeout --kill-after=5s 30 incus exec native --project lab --disable-stdin -- /bin/test -c /dev/example; code=$?; test \"$code\" -eq 1'", timeout=45)
    host.succeed("timeout --kill-after=5s 180 incus stop native --project lab --timeout 120", timeout=200)
    host.succeed("timeout --kill-after=5s 30 incus config device add native serial unix-char source=/dev/nixlab/example path=/dev/example required=false gid=20 mode=0660 --project lab", timeout=45)
    host.succeed("timeout --kill-after=5s 120 incus start native --project lab", timeout=140)
    host.succeed("timeout --kill-after=5s 30 incus exec native --project lab --disable-stdin -- /bin/test -c /dev/example", timeout=45)

    host.succeed("PYTHONPATH=${../scripts} NIXLAB_EXPORT_DIRECTORY=/var/lib/nixlab/test-exports python3 ${../scripts/backup.py}", timeout=300)
    assert json.loads(host.succeed("incus query '/1.0/instances/native/state?project=lab'"))["status"] == "Running"
    latest = json.loads(host.succeed("cat /var/lib/nixlab/test-exports/latest.json"))["generation"]
    directory = "/var/lib/nixlab/test-exports/generations/" + latest
    manifest = json.loads(host.succeed("cat " + directory + "/inventory.json"))
    archive = directory + "/" + manifest["guests"][0]["archive"]
    host.succeed("incus import " + archive + " restored --project lab --config boot.autostart=false")
    host.succeed("PYTHONPATH=${../admin}:${../scripts} python3 -c 'import operations; operations.reset_restore_macs(\"lab\", \"restored\")'")
    host.succeed("incus start restored --project lab")
    assert host.succeed("incus config get native volatile.eth0.hwaddr --project lab").strip() != host.succeed("incus config get restored volatile.eth0.hwaddr --project lab").strip()
    assert host.succeed("incus exec restored --project lab --disable-stdin -- /bin/cat /var/lib/test/index.html").strip() == "preserved"
  '';
}
