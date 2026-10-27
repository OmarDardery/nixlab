{ lib, pkgs, site ? import ./site.nix, ... }:
{
  imports = [
    ./modules/installed-base.nix
    ./modules/virtualization.nix
    ./modules/incus.nix
    ./modules/networking.nix
    ./modules/storage.nix
    ./modules/security.nix
    ./modules/secrets.nix
    ./modules/cloudflared.nix
    ./modules/backups.nix
    ./modules/monitoring.nix
    ./modules/mounts.nix
    ./modules/radios/wifi.nix
    ./modules/radios/bluetooth.nix
    ./modules/quality-of-life.nix
    ./modules/admin-ui.nix
    ./modules/apps-onboarding.nix
  ];
  assertions = [
    { assertion = site.ready; message = "Complete site.nix from real hardware/network inventory and set ready=true."; }
    { assertion = site.adminKeys != [ ]; message = "At least one administrator SSH public key is required."; }
    { assertion = builtins.elem site.cpuVendor [ "intel" "amd" ]; message = "Set cpuVendor from the bare-metal host."; }
    { assertion = site.management.interface != "" && site.management.address != "" && site.management.trustedCIDRs != [ ];
      message = "Management interface, address and trusted source CIDRs must be explicit."; }
    { assertion = site.guests.cidr != "" && site.guests.gatewayCIDR != "" && site.guests.appsAddress != "" && site.guests.dhcpRange != "";
      message = "Choose a non-overlapping guest subnet, bridge address, DHCP range and reserved apps IP."; }
  ];
  networking.hostName = lib.mkForce site.hostName;
  time.timeZone = lib.mkForce site.maintenance.timeZone;
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  nix.gc = { automatic = true; dates = "weekly"; options = "--delete-older-than 30d"; };
  environment.systemPackages = [
    (import ./scripts/homelab.nix { inherit pkgs; }) pkgs.jq pkgs.git pkgs.curl
  ];
  environment.etc."nixlab/client.json".text = builtins.toJSON {
    remote = "local"; project = "lab"; appsInstance = "apps";
    exportDirectory = site.backup.stagingDirectory;
  };
  system.stateVersion = lib.mkDefault "26.05";
}
