{
  description = "Nixlab: NixOS bare-metal Incus host and isolated Compose VM";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/718f670e845328e9c2cecdd832835712b2cbd065";
  inputs.sops-nix = {
    url = "github:Mic92/sops-nix/60182817ee048ac94b4c68ea76d5ef2e8aac730d";
    inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs = { self, nixpkgs, sops-nix }:
    let
      site = import ./site.nix;
      testSite = import ./tests/site.nix;
      mkApps = inventory: nixpkgs.lib.nixosSystem {
        system = inventory.system;
        specialArgs.site = inventory;
        modules = [ sops-nix.nixosModules.sops ./guests/apps.nix ];
      };
      apps = mkApps site;
      testApps = mkApps testSite;
      artifacts = vm: {
        metadata = "${vm.config.system.build.metadata}/tarball/${vm.config.image.fileName}";
        disk = "${vm.config.system.build.qemuImage}/nixos.qcow2";
      };
      mkHost = inventory: vm: extra: nixpkgs.lib.nixosSystem {
        system = inventory.system;
        specialArgs = { site = inventory; appImage = artifacts vm; };
        modules = [ sops-nix.nixosModules.sops ./configuration.nix ] ++ extra;
      };
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in {
      nixosConfigurations = {
        hypervisor = mkHost site apps [ ];
        inherit apps;
        # Evaluation fixtures, never an installation target.
        validation-host = mkHost testSite testApps [ ./tests/hardware.nix ];
        validation-apps = testApps;
        validation-services = mkHost (testSite // {
          backup = testSite.backup // { enable = true; repository = "sftp:backup@example.invalid:/nixlab"; };
          tunnel = testSite.tunnel // { enable = true; uuid = "00000000-0000-4000-8000-000000000001";
            ingress."app.example.invalid" = "http://10.77.0.10:8080"; };
        }) testApps [ ./tests/hardware.nix ];
        validation-demo = mkApps (testSite // { apps = testSite.apps // { enableDemo = true; }; });
        validation-zfs = mkHost (testSite // {
          storage = { driver = "zfs"; source = "testpool/incus";
            confirmedExistingZfsDataset = true; hostId = "1234abcd"; };
        }) testApps [ ./tests/hardware.nix ];
      };
      packages.x86_64-linux = {
        homelab = import ./scripts/homelab.nix { inherit pkgs; };
        apps-image = apps.config.system.build.qemuImage;
      };
      checks.x86_64-linux = {
        host = self.nixosConfigurations.validation-host.config.system.build.toplevel;
        apps = testApps.config.system.build.toplevel;
        helpers = self.packages.x86_64-linux.homelab;
      };
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = with pkgs; [ nixfmt shellcheck python3 incus-lts.client docker-compose ];
      };
    };
}
