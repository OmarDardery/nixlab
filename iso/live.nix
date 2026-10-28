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
  guided = pkgs.writeShellApplication {
    name = "nixlab-setup";
    runtimeInputs = with pkgs; [ python3 util-linux parted dosfstools e2fsprogs systemd nixos-install-tools ];
    text = ''exec python3 ${./.}/install_disk.py --module ${firstboot} "$@"'';
  };
in {
  imports = [ (modulesPath + "/installer/cd-dvd/installation-cd-minimal.nix") ];
  networking.hostName = "nixlab-installer";
  services.openssh.enable = lib.mkForce false;
  boot.zfs.forceImportRoot = false;
  # Keep the local screen and a serial console available for headless recovery
  # and for booting this exact image in the disposable CI VM.
  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200n8" ];
  isoImage = {
    edition = lib.mkForce "nixlab";
    configurationName = "Nixlab installer";
    volumeID = "NIXLAB_INSTALL";
    makeEfiBootable = true;
    makeUsbBootable = true;
    squashfsCompression = "zstd -Xcompression-level 6";
  };
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.systemPackages = [ stage guided nixlabInstaller ];
  environment.etc."nixlab-source".source = nixlabSource;
  environment.etc."nixlab-iso-revision".text = nixlabRevision + "\n";
  environment.etc."nixlab-installation.md".source = ../docs/iso.md;
  services.getty.helpLine = lib.mkForce ''
    Nixlab installation media — no disks are modified automatically.
    The local nixos account has passwordless sudo; use sudo -i for a root shell.
    SSH is disabled on this live image. Use the local or serial console.
    Connect to the Internet, then run sudo nixlab-setup for guided installation.
    Read /etc/nixlab-installation.md. Prepare and mount your target, generate its
    NixOS config, then use nixlab-stage-install --confirm-target /mnt.
    Internet access is required for installation and first-boot setup.
  '';
  system.build.nixlabStageInstaller = stage;
  system.build.nixlabGuidedInstaller = guided;
  system.build.nixlabFirstbootModule = firstboot;
}
