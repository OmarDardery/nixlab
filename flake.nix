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
      # Evaluate host policy without guest image artifacts, then build the guest
      # from the same effective settings. File overrides therefore reach both roles.
      policy = mkHost site null [ ];
      apps = mkApps policy.config.nixlab.settings;
      coupledPolicy = mkHost testSite null [ ./tests/hardware.nix ./tests/module-tree/typed-settings.nix ];
      coupledApps = mkApps coupledPolicy.config.nixlab.settings;
      testApps = mkApps testSite;
      artifacts = vm: {
        system = vm.config.system.build.toplevel;
        metadata = "${vm.config.system.build.metadata}/tarball/${vm.config.image.fileName}";
        disk = "${vm.config.system.build.qemuImage}/nixos.qcow2";
      };
      mkHost = inventory: vm: extra: mkHostNetwork inventory vm extra installedNetworking;
      mkHostNetwork = inventory: vm: extra: networking: nixpkgs.lib.nixosSystem {
        system = inventory.system;
        specialArgs = { inventory = checkSettings inventory; appImage = if vm == null then null else artifacts vm; installedNetworking = networking; installedBase = null; };
        modules = [ sops-nix.nixosModules.sops ./configuration.nix ] ++ extra;
      };
      bundledSource = nixpkgs.lib.cleanSourceWith {
        src = self.outPath;
        filter = path: type:
          let relative = nixpkgs.lib.removePrefix (toString self.outPath + "/") (toString path);
              top = builtins.head (nixpkgs.lib.splitString "/" relative);
          in type != "symlink" && builtins.baseNameOf path != "__pycache__" && (toString path == toString self.outPath || builtins.elem top [ "admin" "configuration.nix" "configuration-bootstrap.nix"
            "docs" "examples" "flake.lock" "flake.nix" "guests" "installer" "iso" "lib"
            "modules" "platform-modules" "scripts" "helpers" "site-defaults.nix" "site.nix" "tests"
            "README.md" "VERSION" "install.sh" "platform-release.json" ".gitignore" ]);
      };
      isoInstaller = import ./installer/package.nix { inherit pkgs; };
      firstbootModule = import ./iso/firstboot.nix {
        source = bundledSource; installer = isoInstaller; revision = self.rev or "unversioned";
      };
      installerIso = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = { nixlabSource = bundledSource; nixlabInstaller = isoInstaller;
          nixlabRevision = self.rev or "unversioned"; };
        modules = [ ./iso/live.nix ];
      };
      pkgs = import nixpkgs { system = "x86_64-linux"; };
      ethernetSite = testSite // {
        apps = testSite.apps // { enable = false; };
        management = testSite.management // { interface = "br-lan"; configure = true; };
        connectivity = testSite.connectivity // {
          bridges = [ { name = "br-lan"; interfaces = [ "eth0" ]; } { name = "br-apps"; interfaces = [ "vlan20" ]; } ];
          vlans = [ { name = "vlan20"; interface = "eth1"; id = 20; } ];
          guestInterfaces = [ "incusbr0" "incus-private" ]; trustedLANAccess = true;
          privateAccess = [ { cidr = "192.0.2.53/32"; protocol = "udp"; ports = [ 53 ]; } ];
        };
        hardware.serialAliases = [ { name = "example"; vendorId = "1234"; productId = "5678"; serial = "FIXTURE-ONLY"; } ];
      };
    in {
      nixosModules = import ./lib/platform.nix { lib = nixpkgs.lib; };
      nixosConfigurations = {
        hypervisor = mkHost site apps [ ];
        inherit apps bootstrap;
        installer-iso = installerIso;
        validation-iso-firstboot = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [ ./tests/hardware.nix firstbootModule { system.stateVersion = "26.05"; } ];
        };
        # Evaluation fixtures, never an installation target.
        validation-minimal = mkHost (testSite // { apps = testSite.apps // { enable = false; }; }) testApps [ ./tests/hardware.nix ];
        validation-file-disk = mkHost testSite testApps
          ([ ./tests/hardware.nix ] ++ (import ./lib/site-modules.nix ./tests/module-tree));
        validation-recipes = mkHost (testSite // { apps = testSite.apps // { enable = false; }; }) null
          ([ ./tests/hardware.nix { nixlab.notifications.enable = true; } ] ++ map
            (path: builtins.toFile "nixlab-recipe.nix" (builtins.fromJSON (builtins.readFile path)).module)
            [ ./admin/recipes/laptop.json ./admin/recipes/disk-health.json ./admin/recipes/private-access.json ]);
        validation-host = mkHost testSite testApps [ ./tests/hardware.nix ];
        validation-management = mkHost (testSite // {
          cockpit = testSite.cockpit // { enable = true; };
          apps = testSite.apps // { portainerPort = 9443; };
          setup = testSite.setup // { enable = true; };
          maintenance = testSite.maintenance // { laptopMode = true; };
        }) testApps [ ./tests/hardware.nix ];
        validation-ethernet = mkHost ethernetSite null [ ./tests/hardware.nix ];
        validation-ethernet-nm = mkHostNetwork ethernetSite null
          [ ./tests/hardware.nix { networking.networkmanager.enable = true; } ] { networkManager = true; };
        validation-ethernet-wifi = mkHostNetwork (ethernetSite // {
          management = ethernetSite.management // { interface = "wlan0"; };
          radios = ethernetSite.radios // { wifi = ethernetSite.radios.wifi // {
            enable = true; interface = "wlan0"; ssid = "FIXTURE-ONLY"; manageProfile = true;
          }; };
        }) null [ ./tests/hardware.nix { networking.networkmanager.enable = true; } ] { networkManager = true; };
        validation-apps = testApps;
        validation-coupled-apps = coupledApps;
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
        installer = isoInstaller;
        installer-iso = installerIso.config.system.build.isoImage;
        iso-stage = installerIso.config.system.build.nixlabStageInstaller;
        iso-setup = installerIso.config.system.build.nixlabGuidedInstaller;
        admin = import ./admin/package.nix { inherit pkgs; };
        homelab = import ./scripts/homelab.nix { inherit pkgs; };
        helpers = import ./helpers/package.nix { inherit pkgs; };
        apps-image = apps.config.system.build.qemuImage;
      };
      apps.x86_64-linux.install = { type = "app"; program = "${self.packages.x86_64-linux.installer}/bin/nixlab-install"; };
      nixlabMigrationChecks = import ./tests/settings-checks.nix { lib = nixpkgs.lib; };
      nixlabFoundationChecks = let
        native = self.nixosConfigurations.validation-ethernet.config;
        nm = self.nixosConfigurations.validation-ethernet-nm.config;
        wifi = self.nixosConfigurations.validation-ethernet-wifi.config;
      in {
        nativeBridgePorts = native.networking.bridges.br-apps.interfaces == [ "vlan20" ];
        nativePortAddressRemoved = native.networking.interfaces.eth0.ipv4.addresses == [ ];
        vlanActivationName = nm.networking.networkmanager.ensureProfiles.profiles.nixlab-vlan-vlan20.connection.id == "nixlab-vlan-vlan20";
        vlanBridgeMembership = nm.networking.networkmanager.ensureProfiles.profiles.nixlab-vlan-vlan20.connection.master == "br-apps";
        guestNetworkOwnership = builtins.elem "interface-name:incus-private" nm.networking.networkmanager.unmanaged;
        wifiManagementActivation = nixpkgs.lib.hasInfix "connection up nixlab-wifi" wifi.systemd.services.nixlab-connectivity.script;
        noWiredManagementOnWifi = !(nixpkgs.lib.hasInfix "connection up nixlab-management" wifi.systemd.services.nixlab-connectivity.script);
      };
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
        management = import ./tests/management-vm.nix { inherit pkgs; };
        portal = import ./tests/portal-vm.nix { inherit pkgs; };
        foundation = import ./tests/foundation-vm.nix { inherit pkgs; };
        host = self.nixosConfigurations.validation-host.config.system.build.toplevel;
        apps = testApps.config.system.build.toplevel;
        helpers = self.packages.x86_64-linux.homelab;
      };
      devShells.x86_64-linux = {
        default = pkgs.mkShell {
          packages = with pkgs; [ nixfmt shellcheck python3 incus-lts.client docker-compose ];
        };
        iso-test = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.qemu ];
          NIXLAB_OVMF_CODE = pkgs.OVMF.firmware;
          NIXLAB_OVMF_VARS = pkgs.OVMF.variables;
          NIXLAB_OVMF_SMM = if pkgs.OVMF.systemManagementModeRequired then "1" else "0";
        };
      };
    };
}
