# Explicit role composition, retaining the former evaluated import order.
{
  bootstrap.imports = [
    ../modules/settings.nix
    ../modules/installed-base.nix
    ../modules/admin-ui.nix
  ];

  hypervisor.imports = [
    ../modules/virtualization.nix
    ../modules/incus.nix
    ../modules/instance-schema.nix
    ../modules/deployments.nix
    ../modules/storage.nix
    ../modules/file-disks.nix
    ../modules/mount-schema.nix
    ../modules/mounts.nix
    ../modules/settings.nix
    ../modules/radios/wifi.nix
    ../modules/radios/bluetooth.nix
    ../modules/notifications.nix
    ../modules/networking.nix
    ../modules/monitoring.nix
    ../modules/quality-of-life.nix
    ../modules/installed-base.nix
    ../modules/cockpit.nix
    ../modules/connectivity.nix
    ../modules/hardware-support.nix
    ../modules/helpers.nix
    ../modules/cloudflared.nix
    ../modules/backups.nix
    ../modules/admin-ui.nix
    ../modules/apps-onboarding.nix
    ../modules/security.nix
    ../modules/secrets.nix
  ];

  applications = { modulesPath, ... }: {
    imports = [
      ../modules/settings.nix
      ../modules/guest-secrets.nix
      ../modules/docker-host.nix
      (modulesPath + "/virtualisation/incus-virtual-machine.nix")
    ];
  };
}
