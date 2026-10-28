{ lib, pkgs, site, ... }:
{
  boot.kernelModules = [ (if site.cpuVendor == "amd" then "kvm-amd" else "kvm-intel") ];
  virtualisation.libvirtd.enable = lib.mkForce false;
  virtualisation.docker.enable = lib.mkForce false;
  systemd.services.nixlab-kvm-check = {
    description = "Require usable hardware virtualization before provisioning VMs";
    wantedBy = [ "multi-user.target" ];
    before = [ "nixlab-instances.service" ];
    serviceConfig.Type = "oneshot";
    script = ''
      test -c /dev/kvm
      ${pkgs.python3}/bin/python - <<'PYCODE'
      import os, fcntl
      fd = os.open('/dev/kvm', os.O_RDWR)
      assert fcntl.ioctl(fd, 0xAE00, 0) == 12, 'KVM API unavailable'
      os.close(fd)
      PYCODE
    '';
  };
}
