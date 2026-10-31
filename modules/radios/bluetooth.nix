{ lib, site, ... }:
{
  hardware.bluetooth = {
    enable = lib.mkForce site.radios.bluetooth.enable;
    powerOnBoot = lib.mkForce site.radios.bluetooth.enable;
  };
}
