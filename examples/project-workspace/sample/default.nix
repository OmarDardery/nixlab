{ ... }: {
  nixlab.project = {
    name = "sample";
    backend = "incus-native";
    incusProject = "sample";
    resources = { cpus = 2; memory = "1GiB"; };
    # Only encrypted resource path references are allowed in the inventory.
    secrets.file = null;
    backup = { enable = true; consistency = "stop"; };
  };
}
