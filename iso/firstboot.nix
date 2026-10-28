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
    path = [ pkgs.coreutils ];
    serviceConfig = {
      Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "2h";
      StandardOutput = "journal+console"; StandardError = "journal+console";
      UMask = "0077";
    };
    script = ''
      resume=()
      if test -f /var/lib/nixlab/config/machine/installation.json; then resume=(--resume); fi
      ${installer}/bin/nixlab-install --checkout ${source} --ref ${lib.escapeShellArg revision} "''${resume[@]}"
      mkdir -p /var/lib/nixlab
      touch /var/lib/nixlab/iso-setup-complete
    '';
  };
}
