{ lib, pkgs, site, ... }:
{
  services.fstrim.enable = lib.mkForce site.maintenance.trim;
  nix.optimise = { automatic = site.maintenance.optimiseStore; dates = [ "weekly" ]; };
  environment.systemPackages = with pkgs; [ git htop tmux pciutils usbutils lm_sensors smartmontools ];
  # Keep previous generations; no automatic flake changes, reboot, disk wipe or volume prune.
  boot.loader.timeout = lib.mkDefault 5;
  services.logind.settings.Login = lib.mkIf site.maintenance.laptopMode {
    HandleLidSwitch = "ignore";
    HandleLidSwitchExternalPower = "ignore";
    HandleLidSwitchDocked = "ignore";
  };
}
