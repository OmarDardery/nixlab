# Evaluation-only filesystem. Never deploy this target.
{ ... }: {
  fileSystems."/" = { device = "/dev/disk/by-label/TEST-ONLY"; fsType = "ext4"; };
  boot.loader.grub.enable = false;
}
