{ lib, pkgs, modulesPath, site, ... }:
{
  imports = [ (import ../lib/platform.nix { inherit lib; }).applications ];
  networking.hostName = "apps";
  networking.useDHCP = true;
  time.timeZone = site.maintenance.timeZone;
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  users.users.root = {
    initialHashedPassword = lib.mkForce null;
    hashedPassword = "!";
    openssh.authorizedKeys.keys = site.adminKeys;
  };
  services.getty.helpLine = lib.mkForce "Use Incus authenticated exec/console or SSH keys.";
  services.openssh = {
    enable = true;
    settings = { PasswordAuthentication = false; KbdInteractiveAuthentication = false; PermitRootLogin = "prohibit-password"; };
  };
  # UI ports bind VM loopback; remote management uses SSH forwarding or Incus exec.
  networking.firewall.allowedTCPPorts = [ 22 8080 ];
  services.prometheus.exporters.node = { enable = site.monitoring.history; listenAddress = "0.0.0.0"; openFirewall = false; };
  networking.firewall.extraCommands = ''
    iptables -I nixos-fw -s ${lib.escapeShellArg (builtins.head (lib.splitString "/" site.guests.gatewayCIDR))} -p tcp --dport 9100 -j nixos-fw-accept
  '';
  services.journald.extraConfig = "Storage=persistent\nSystemMaxUse=512M";
  system.stateVersion = "26.05";
}
