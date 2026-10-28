{
  description = "Nixlab: NixOS bare-metal Incus host and isolated Compose VM";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  inputs.sops-nix = {
    url = "github:Mic92/sops-nix/60182817ee048ac94b4c68ea76d5ef2e8aac730d";
    inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs = { self, nixpkgs, sops-nix }:
    let
      checkSettings = values: import ./lib/settings.nix { lib = nixpkgs.lib; inherit values; };
      site = checkSettings (import ./site.nix);
      testSite = import ./tests/site.nix;
      mkApps = inventory: nixpkgs.lib.nixosSystem {
        system = inventory.system;
        specialArgs.inventory = checkSettings inventory;
        modules = [ sops-nix.nixosModules.sops ./guests/apps.nix ];
      };
      bootstrap = nixpkgs.lib.nixosSystem {
        system = site.system; specialArgs = { inventory = site; installedBase = null; };
        modules = [ ./configuration-bootstrap.nix ];
      };
      installedNetworking = if builtins.pathExists (import ./lib/machine.nix ./.).installed
        then { networkManager = bootstrap.config.networking.networkmanager.enable; }
        else { networkManager = false; };
      apps = mkApps site;
      testApps = mkApps testSite;
      artifacts = vm: {
        system = vm.config.system.build.toplevel;
        metadata = "${vm.config.system.build.metadata}/tarball/${vm.config.image.fileName}";
        disk = "${vm.config.system.build.qemuImage}/nixos.qcow2";
      };
      mkHost = inventory: vm: extra: nixpkgs.lib.nixosSystem {
        system = inventory.system;
        specialArgs = { inventory = checkSettings inventory; appImage = artifacts vm; inherit installedNetworking; installedBase = null; };
        modules = [ sops-nix.nixosModules.sops ./configuration.nix ] ++ extra;
      };
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in {
      nixosModules = import ./lib/platform.nix { lib = nixpkgs.lib; };
      nixosConfigurations = {
        hypervisor = mkHost site apps [ ];
        inherit apps bootstrap;
        # Evaluation fixtures, never an installation target.
        validation-minimal = mkHost (testSite // { apps = testSite.apps // { enable = false; }; }) testApps [ ./tests/hardware.nix ];
        validation-file-disk = mkHost testSite testApps
          ([ ./tests/hardware.nix ] ++ (import ./lib/site-modules.nix ./tests/module-tree));
        validation-host = mkHost testSite testApps [ ./tests/hardware.nix ];
        validation-apps = testApps;
        validation-services = mkHost (testSite // {
          monitoring = testSite.monitoring // { history = true; };
          backup = testSite.backup // { enable = true; repository = "sftp:backup@example.invalid:/nixlab"; };
          tunnel = testSite.tunnel // { enable = true; uuid = "00000000-0000-4000-8000-000000000001";
            ingress."app.example.invalid" = "http://10.77.0.10:8080"; };
        }) testApps [ ./tests/hardware.nix ];
        validation-bootstrap = nixpkgs.lib.nixosSystem {
          system = testSite.system;
          specialArgs = { inventory = testSite // { setup = testSite.setup // { enable = true; }; }; installedBase = ./tests/installed-base.nix; };
          modules = [ ./configuration-bootstrap.nix ];
        };
        validation-inherited = nixpkgs.lib.nixosSystem {
          system = testSite.system;
          specialArgs = { inventory = testSite; appImage = artifacts testApps; installedBase = ./tests/installed-base.nix; installedNetworking.networkManager = true; };
          modules = [ sops-nix.nixosModules.sops ./configuration.nix ];
        };
        validation-setup-apps = mkApps (testSite // { setup = testSite.setup // { enable = true; }; });
        validation-setup = mkHost (testSite // {
          setup = testSite.setup // { enable = true; };
          management = testSite.management // { configure = true; };
          radios = { wifi = { enable = true; interface = "wlan0"; ssid = "TEST-ONLY"; manageProfile = true; }; bluetooth.enable = true; };
          mounts = [
            { name = "guests"; uuid = "11111111-1111-1111-1111-111111111111"; path = "/srv/nixlab/guests"; fsType = "ext4"; role = "guests"; required = true; readOnly = false; }
            { name = "archive"; uuid = "22222222-2222-2222-2222-222222222222"; path = "/srv/nixlab/archive"; fsType = "xfs"; role = "pool"; required = true; readOnly = false; }
          ];
          storage = testSite.storage // { source = "/srv/nixlab/guests/incus"; };
        }) (mkApps (testSite // { setup = testSite.setup // { enable = true; }; })) [ ./tests/hardware.nix ];
        validation-demo = mkApps (testSite // { apps = testSite.apps // { enableDemo = true; }; });
        validation-zfs = mkHost (testSite // {
          storage = { driver = "zfs"; source = "testpool/incus";
            confirmedExistingZfsDataset = true; hostId = "1234abcd"; };
        }) testApps [ ./tests/hardware.nix ];
      };
      packages.x86_64-linux = {
        installer = import ./installer/package.nix { inherit pkgs; };
        admin = import ./admin/package.nix { inherit pkgs; };
        homelab = import ./scripts/homelab.nix { inherit pkgs; };
        apps-image = apps.config.system.build.qemuImage;
      };
      apps.x86_64-linux.install = { type = "app"; program = "${self.packages.x86_64-linux.installer}/bin/nixlab-install"; };
      nixlabMigrationChecks = import ./tests/settings-checks.nix { lib = nixpkgs.lib; };
      nixlabDefaults = import ./site-defaults.nix;
      nixlabDesiredSettings = site;
      nixlabSettings = self.nixosConfigurations.hypervisor.config.nixlab.settings;
      nixlabOverview = {
        stateVersion = bootstrap.config.system.stateVersion;
        boot = { systemdBoot = bootstrap.config.boot.loader.systemd-boot.enable; grub = bootstrap.config.boot.loader.grub.enable; };
        networkManager = installedNetworking.networkManager;
        wpaProfiles = bootstrap.config.networking.wireless.networks != { };
        fileSystems = builtins.mapAttrs (_: fs: { inherit (fs) device fsType options; }) bootstrap.config.fileSystems;
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
