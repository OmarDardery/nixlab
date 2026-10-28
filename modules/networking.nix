{ lib, site, installedNetworking ? { networkManager = false; }, ... }:
let
  m = site.management;
  trusted = lib.concatStringsSep ", " m.trustedCIDRs;
  iface = builtins.toJSON m.interface;
  nmWired = installedNetworking.networkManager && m.interface != site.radios.wifi.interface;
  useNM = nmWired || (site.radios.wifi.enable && m.interface == site.radios.wifi.interface);
in {
  networking.nftables = { enable = true; flushRuleset = false; };
  networking.firewall = {
    enable = true;
    filterForward = true;
    allowedTCPPorts = lib.mkForce [ ];
    allowedUDPPorts = lib.mkForce [ ];
    trustedInterfaces = lib.mkForce [ ];
    allowPing = false;
    # Interface trust is deliberately not granted to the guest bridge.
    extraInputRules = ''
      iifname ${iface} ip saddr { ${trusted} } icmp type echo-request accept
      iifname ${iface} ip saddr { ${trusted} } tcp dport { 22, 8443 } accept
      iifname "incusbr0" udp dport { 53, 67 } accept
      iifname "incusbr0" tcp dport 53 accept
    '';
    extraForwardRules = ''
      iifname "incusbr0" oifname "incusbr0" accept
      iifname "incusbr0" ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16 } drop
      iifname "incusbr0" oifname ${iface} ip saddr ${site.guests.cidr} accept
      # LAN clients require a route to the guest subnet; only the demo endpoint is allowed.
      iifname ${iface} ip saddr { ${trusted} } ip daddr ${site.guests.appsAddress} tcp dport 8080 accept
    '';
  };
  networking.nat = {
    enable = true;
    externalInterface = m.interface;
    internalInterfaces = [ "incusbr0" ];
    internalIPs = [ site.guests.cidr ];
  };
  # Opt-in change: never enslave or rename an existing management NIC on rebuild.
  networking.useDHCP = lib.mkIf (m.configure && !useNM) (lib.mkForce false);
  networking.interfaces = lib.mkIf (m.configure && !useNM) {
    "${m.interface}" = {
      useDHCP = lib.mkForce false;
      ipv4.addresses = lib.mkForce [ { address = m.address; prefixLength = m.prefixLength; } ];
    };
  };
  networking.defaultGateway = lib.mkIf (m.configure && !useNM) (lib.mkForce m.gateway);
  networking.nameservers = lib.mkIf (m.configure && !useNM) (lib.mkForce m.dns);
  networking.networkmanager.ensureProfiles.profiles = lib.mkIf (m.configure && nmWired) {
    nixlab-management = {
      connection = { id = "nixlab-management"; type = "ethernet"; interface-name = m.interface; autoconnect = true; autoconnect-priority = 100; };
      ipv4 = { method = "manual"; address1 = "${m.address}/${toString m.prefixLength},${m.gateway}"; dns = lib.concatStringsSep ";" m.dns; };
      ipv6.method = "auto";
    };
  };
}
