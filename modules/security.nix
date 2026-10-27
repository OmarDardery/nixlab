{ lib, site, ... }:
{
  security.apparmor.enable = true;
  services.openssh = {
    enable = true;
    openFirewall = lib.mkForce false;
    settings = { PasswordAuthentication = lib.mkForce false; KbdInteractiveAuthentication = lib.mkForce false;
      PermitRootLogin = lib.mkForce "no"; X11Forwarding = false; };
  };
  users.users.operator = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keys = site.adminKeys;
  };
  # Key-authenticated operator is a trusted infrastructure administrator.
  # sudo operations are journaled; neither control socket is delegated to ordinary users.
  security.sudo.extraRules = [ { users = [ "operator" ]; commands = [ { command = "ALL"; options = [ "NOPASSWD" ]; } ]; } ];
  services.journald.extraConfig = ''
    Storage=persistent
    SystemMaxUse=1G
    MaxRetentionSec=30day
  '';
  system.autoUpgrade.enable = false; # review pinned-input updates and reboot maintenance explicitly
}
