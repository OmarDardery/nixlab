{ lib, site, installedNetworking ? { networkManager = false; }, ... }:
let
  m = site.management;
  trusted = lib.concatStringsSep ", " m.trustedCIDRs;
  iface = builtins.toJSON m.interface;
  guestIfaces = lib.concatMapStringsSep ", " builtins.toJSON site.connectivity.guestInterfaces;
  isBridge = lib.any (b: b.name == m.interface) site.connectivity.bridges;
  vlan = lib.findFirst (v: v.name == m.interface) null site.connectivity.vlans;
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
      iifname { ${guestIfaces} } udp dport { 53, 67 } accept
      iifname { ${guestIfaces} } tcp dport 53 accept
    '';
    extraForwardRules = ''
      iifname "incusbr0" ip saddr != ${site.guests.cidr} drop
      iifname { ${guestIfaces} } oifname { ${guestIfaces} } accept
      ${lib.concatMapStringsSep "\n" (rule: ''
        iifname { ${guestIfaces} } ip daddr ${rule.cidr} ${rule.protocol} dport { ${lib.concatMapStringsSep ", " toString rule.ports} } accept
      '') site.connectivity.privateAccess}
      iifname { ${guestIfaces} } ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16 } drop
      iifname { ${guestIfaces} } oifname ${iface} meta nfproto ipv4 accept
    '' + lib.optionalString site.connectivity.trustedLANAccess ''
      # Incus owns guest forwarding/ACLs; this is the outer host trust boundary.
      iifname ${iface} ip saddr { ${trusted} } oifname { ${guestIfaces} } accept
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
      connection = { id = "nixlab-management"; type = if isBridge then "bridge" else if vlan != null then "vlan" else "ethernet";
        interface-name = m.interface; autoconnect = true; autoconnect-priority = 200; };
      ipv4 = { method = "manual"; address1 = "${m.address}/${toString m.prefixLength},${m.gateway}"; dns = lib.concatStringsSep ";" m.dns; };
      ipv6.method = "auto";
    } // lib.optionalAttrs isBridge { bridge.stp = false; }
      // lib.optionalAttrs (vlan != null) { vlan = { id = vlan.id; parent = vlan.interface; }; };
  };
}
