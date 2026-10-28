{ source, installer, revision }:
{ lib, pkgs, ... }:
{
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  systemd.services.nixlab-firstboot = {
    description = "Convert the installed NixOS system to Nixlab setup";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ]; after = [ "network-online.target" ];
    unitConfig.ConditionPathExists = "!/var/lib/nixlab/iso-setup-complete";
    restartIfChanged = false; stopIfChanged = false;
    path = [ pkgs.coreutils pkgs.systemd ];
    serviceConfig = {
      # Do not hold multi-user.target in an activating state while rebuilding:
      # switch-to-configuration may need to start that same target.
      Type = "exec"; RuntimeMaxSec = "2h";
      StandardOutput = "journal+console"; StandardError = "journal+console";
      UMask = "0077";
    };
    script = ''
      # Type=exec makes this service started before waiting for boot to settle.
      # A degraded system can still expose its console and be inspected; the
      # converter performs its own prerequisites and leaves failures resumable.
      ${pkgs.systemd}/bin/systemctl is-system-running --wait >/dev/null || true
      resume=()
      if test -f /var/lib/nixlab/config/machine/installation.json; then resume=(--resume); fi
      ${installer}/bin/nixlab-install --checkout ${source} --ref ${lib.escapeShellArg revision} "''${resume[@]}"
      mkdir -p /var/lib/nixlab
      touch /var/lib/nixlab/iso-setup-complete
    '';
  };
}
