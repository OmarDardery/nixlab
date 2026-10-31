{ lib, pkgs, site, installedNetworking ? { networkManager = false; }, ... }:
let
  c = site.connectivity;
  nm = installedNetworking.networkManager;
  wifi = site.radios.wifi;
  managementProfiles = if site.management.interface == wifi.interface
    then lib.optional (site.management.configure && wifi.enable && wifi.manageProfile) "nixlab-wifi"
    else lib.optional site.management.configure "nixlab-management";
  bridgeNames = map (b: b.name) c.bridges;
  ports = lib.concatMap (b: b.interfaces) c.bridges;
  bridgeFor = name: lib.findFirst (b: builtins.elem name b.interfaces) null c.bridges;
  portConnection = name: {
    id = "nixlab-port-${name}"; interface-name = name;
    autoconnect = true; autoconnect-priority = 200;
  } // lib.optionalAttrs (bridgeFor name != null) {
    master = (bridgeFor name).name; slave-type = "bridge";
  };
  vlanProfiles = builtins.listToAttrs (map (v: {
    name = "nixlab-vlan-${v.name}";
    value = {
      connection = (portConnection v.name) // { id = "nixlab-vlan-${v.name}"; type = "vlan"; };
      vlan = { id = v.id; parent = v.interface; };
    } // lib.optionalAttrs (bridgeFor v.name == null) { ipv4.method = "disabled"; ipv6.method = "disabled"; };
  }) (builtins.filter (v: v.name != site.management.interface) c.vlans));
  ethernetProfiles = builtins.listToAttrs (map (name: {
    name = "nixlab-port-${name}";
    value.connection = (portConnection name) // { type = "ethernet"; };
  }) (builtins.filter (name: !(builtins.elem name (map (v: v.name) c.vlans))) ports));
  bridgeProfiles = builtins.listToAttrs (map (b: {
    name = "nixlab-bridge-${b.name}";
    value = {
      connection = { id = "nixlab-bridge-${b.name}"; type = "bridge"; interface-name = b.name; autoconnect = true; autoconnect-priority = 200; };
      bridge.stp = false;
      ipv4.method = "disabled"; ipv6.method = "disabled";
    };
  }) (builtins.filter (b: b.name != site.management.interface) c.bridges));
  activate = managementProfiles ++ builtins.attrNames bridgeProfiles
    ++ builtins.attrNames vlanProfiles ++ builtins.attrNames ethernetProfiles;
in {
  assertions = [
    { assertion = builtins.length ports == builtins.length (lib.unique ports);
      message = "An Ethernet/VLAN interface may belong to only one host bridge."; }
    { assertion = !(builtins.elem site.management.interface ports);
      message = "Move management addressing onto the bridge before enslaving its port."; }
    { assertion = lib.all (name: !(builtins.elem name c.guestInterfaces)) (bridgeNames ++ ports ++ [ site.management.interface ]);
      message = "Private guest firewall interfaces must be separate from host/LAN interfaces."; }
  ];
  networking.bridges = lib.mkIf (!nm) (builtins.listToAttrs (map (b: {
    name = b.name; value.interfaces = b.interfaces;
  }) c.bridges));
  networking.vlans = lib.mkIf (!nm) (builtins.listToAttrs (map (v: {
    name = v.name; value = { inherit (v) id interface; };
  }) c.vlans));
  # Bridge ports must not keep DHCP addresses from their previous standalone role.
  networking.interfaces = lib.mkIf (!nm) (builtins.listToAttrs (map (name: {
    inherit name; value = { useDHCP = lib.mkForce false; ipv4.addresses = lib.mkForce [ ]; ipv6.addresses = lib.mkForce [ ]; };
  }) ports));
  networking.networkmanager.ensureProfiles.profiles = lib.mkIf nm (vlanProfiles // ethernetProfiles // bridgeProfiles);
  networking.networkmanager.unmanaged = map (name: "interface-name:${name}")
    (c.guestInterfaces ++ lib.optionals (!nm) (bridgeNames ++ map (v: v.name) c.vlans ++ ports));
  systemd.services.nixlab-connectivity = lib.mkIf (nm && activate != [ ]) {
    description = "Activate reviewed Ethernet/VLAN profiles";
    wantedBy = [ "multi-user.target" ];
    after = [ "NetworkManager.service" "NetworkManager-ensure-profiles.service" ];
    requires = [ "NetworkManager.service" "NetworkManager-ensure-profiles.service" ];
    before = [ "incus-preseed.service" ];
    path = [ pkgs.networkmanager ];
    restartTriggers = [ (pkgs.writeText "nixlab-connectivity.json" (builtins.toJSON { connectivity = c; management = site.management; })) ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "2min"; };
    script = lib.concatMapStringsSep "\n" (name: "nmcli --wait 20 connection up " + lib.escapeShellArg name) activate;
  };
}
