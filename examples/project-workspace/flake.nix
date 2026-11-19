{
  description = "Portable, declarative NixLab project workspace";
  inputs.nixlab.url = "github:OmarDardery/nixlab";
  outputs = { self, nixlab, ... }: {
    # Resolve against the exact pinned NixLab revision in flake.lock.
    # Project source changes evaluate independently of the NixOS host flake.
    nixlabProjects = nixlab.lib.evalProjects { root = ./.; };
  };
}
