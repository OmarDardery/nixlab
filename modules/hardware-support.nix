{ lib, site, ... }:
{
  # Stable aliases only; actual device assignment and guest UID/GID/mode belong
  # to Incus. No host permission broadening or automatic USB claiming.
  services.udev.extraRules = lib.concatMapStringsSep "\n" (d: ''
    SUBSYSTEM=="tty", ATTRS{idVendor}=="${lib.toLower d.vendorId}", ATTRS{idProduct}=="${lib.toLower d.productId}", ATTRS{serial}=="${d.serial}", SYMLINK+="nixlab/${d.name}", ENV{ID_MM_DEVICE_IGNORE}="1"
  '') site.hardware.serialAliases;
}
