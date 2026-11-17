{ ... }: {
  nixlab.project = {
    name = "alpha";
    backend = "incus-compose";
    incusProject = "alpha";
    secrets.file = "secrets.enc.yaml";
  };
}
