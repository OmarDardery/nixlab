# A previous conventional NixOS installation; evaluation only, never deploy.
{ ... }: {
  system.stateVersion = "25.11";
  boot.loader.grub.enable = false;
  networking.hostName = "old-host";
  networking.networkmanager.enable = true;
  hardware.bluetooth.enable = true;
  networking.firewall.allowedTCPPorts = [ 22 80 ];
  services.openssh.settings.PasswordAuthentication = true;
  fileSystems."/" = { device = "/dev/disk/by-label/TEST-ONLY"; fsType = "ext4"; };
}
