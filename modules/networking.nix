{ lib, site, ... }:
let
  m = site.management;
  trusted = lib.concatStringsSep ", " m.trustedCIDRs;
  iface = builtins.toJSON m.interface;
in {
  networking.nftables = { enable = true; flushRuleset = false; };
  networking.firewall = {
    enable = true;
    filterForward = true;
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
  networking.useDHCP = lib.mkIf m.configure false;
  networking.interfaces = lib.mkIf m.configure {
    "${m.interface}".ipv4.addresses = [ { address = m.address; prefixLength = m.prefixLength; } ];
  };
  networking.defaultGateway = lib.mkIf m.configure m.gateway;
  networking.nameservers = lib.mkIf m.configure m.dns;
}
