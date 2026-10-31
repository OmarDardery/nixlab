{ lib, pkgs, site, installedNetworking ? { networkManager = false; }, ... }:
let w = site.radios.wifi; m = site.management;
  static = m.configure && m.interface == w.interface;
in {
  # NetworkManager owns Wi-Fi; preserve its existing wired ownership when installed.
  systemd.services.nixlab-radio-state = {
    wantedBy = [ "multi-user.target" ]; after = [ "systemd-rfkill.service" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      ${pkgs.util-linux}/bin/rfkill ${if w.enable then "unblock" else "block"} wifi
      ${pkgs.util-linux}/bin/rfkill ${if site.radios.bluetooth.enable then "unblock" else "block"} bluetooth
    '';
  };
  networking.wireless.enable = lib.mkForce false;
  networking.networkmanager = {
    enable = lib.mkIf w.enable (lib.mkForce true);
    wifi.backend = lib.mkIf w.enable (lib.mkForce "iwd");
    unmanaged = [ "interface-name:incusbr0" "interface-name:veth*" "interface-name:tap*" ]
      ++ lib.optional (!w.enable) "type:wifi"
      ++ lib.optional (w.enable && !installedNetworking.networkManager) "type:ethernet";
    ensureProfiles = lib.mkIf (w.enable && w.manageProfile) {
      environmentFiles = [ "/var/lib/nixlab-secrets/wifi.env" ];
      profiles.nixlab-wifi = {
        connection = { id = "nixlab-wifi"; type = "wifi"; interface-name = w.interface; autoconnect = true; autoconnect-priority = 100; };
        wifi = { mode = "infrastructure"; ssid = w.ssid; };
        wifi-security = { key-mgmt = "wpa-psk"; psk = "$NIXLAB_WIFI_PSK"; };
        ipv4 = if static then {
          method = "manual"; address1 = "${m.address}/${toString m.prefixLength},${m.gateway}";
          dns = lib.concatStringsSep ";" m.dns;
        } else { method = "auto"; };
        ipv6.method = "auto";
      };
    };
  };
}
