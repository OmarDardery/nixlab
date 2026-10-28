{ lib, pkgs, modulesPath, nixlabSource, nixlabInstaller, nixlabRevision, ... }:
let
  firstboot = pkgs.writeText "nixlab-firstboot.nix" ''
    import ${./firstboot.nix} {
      source = ${nixlabSource};
      installer = ${nixlabInstaller};
      revision = ${builtins.toJSON nixlabRevision};
    }
  '';
  stage = pkgs.writeShellApplication {
    name = "nixlab-stage-install";
    runtimeInputs = [ pkgs.python3 pkgs.util-linux ];
    text = ''exec python3 ${./stage.py} --module ${firstboot} "$@"'';
  };
in {
  imports = [ (modulesPath + "/installer/cd-dvd/installation-cd-minimal.nix") ];
  networking.hostName = "nixlab-installer";
  services.openssh.enable = lib.mkForce false;
  boot.zfs.forceImportRoot = false;
  isoImage = {
    edition = lib.mkForce "nixlab";
    configurationName = "Nixlab installer";
    volumeID = "NIXLAB_INSTALL";
    makeEfiBootable = true;
    makeUsbBootable = true;
    squashfsCompression = "zstd -Xcompression-level 6";
  };
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.systemPackages = [ stage nixlabInstaller ];
  environment.etc."nixlab-source".source = nixlabSource;
  environment.etc."nixlab-iso-revision".text = nixlabRevision + "\n";
  environment.etc."nixlab-installation.md".source = ../docs/iso.md;
  services.getty.helpLine = lib.mkAfter ''
    Nixlab installation media — no disks are modified automatically.
    Read /etc/nixlab-installation.md. Prepare and mount your target, generate its
    NixOS config, then use nixlab-stage-install --confirm-target /mnt.
    Internet access is required for installation and first-boot setup.
  '';
  system.build.nixlabStageInstaller = stage;
  system.build.nixlabFirstbootModule = firstboot;
}
