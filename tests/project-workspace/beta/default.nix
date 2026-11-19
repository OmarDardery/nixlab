{ ... }: {
  nixlab.project = {
    name = "beta";
    backend = "incus-native";
    backup.consistency = "quiesce";
    claims = [ "instance/beta-guest" ];
  };
}
