{ config, lib, site, ... }:
{
  options.nixlab.recovery.enable = lib.mkOption {
    type = lib.types.bool;
    default = false;
    description = "Replacement-host recovery: suppress automatic guest creation, onboarding, backups and public ingress.";
  };
  config = lib.mkIf config.nixlab.recovery.enable {
    environment.etc."nixlab/recovery-mode".text = "1\n";
    systemd.services = lib.genAttrs [
      "nixlab-instances" "nixlab-apps-onboarding" "nixlab-backup-recover"
      "restic-backups-nixlab" "nixlab-backup-check"
      "cloudflared-tunnel-${site.tunnel.uuid}"
    ] (_: { enable = lib.mkForce false; });
    systemd.timers.restic-backups-nixlab.enable = lib.mkForce false;
  };
}
