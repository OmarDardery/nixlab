{ ... }: {
  nixlab.project = {
    name = "alpha";
    backend = "incus-compose";
    incusProject = "alpha";
    secrets.file = "secrets.enc.yaml";
    secrets.bindings.API_TOKEN = {
      instance = "web";
      backend = "incus-file";
      destination = "/run/secrets/API_TOKEN";
    };
  };
}
