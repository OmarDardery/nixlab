{ lib }:
let
  inherit (lib) mkOption types;
  iface = types.strMatching "[A-Za-z0-9_.-]{1,15}";
  record = options: types.submodule { inherit options; };
  option = type: mkOption { inherit type; };
in {
  "connectivity.bridges" = types.listOf (record {
    name = option iface;
    interfaces = option (types.listOf iface);
  });
  "connectivity.vlans" = types.listOf (record {
    name = option iface;
    interface = option iface;
    id = option (types.ints.between 1 4094);
  });
  "connectivity.privateAccess" = types.listOf (record {
    cidr = option types.str;
    protocol = option (types.enum [ "tcp" "udp" ]);
    ports = option (types.listOf types.port);
  });
  "hardware.serialAliases" = types.listOf (record {
    name = option (types.strMatching "[a-z][a-z0-9-]{0,31}");
    vendorId = option (types.strMatching "[0-9a-fA-F]{4}");
    productId = option (types.strMatching "[0-9a-fA-F]{4}");
    serial = option (types.strMatching "[A-Za-z0-9_.:+-]{1,128}");
  });
  "backup.groups" = types.listOf (types.listOf types.str);
}
