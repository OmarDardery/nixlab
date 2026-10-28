# Dendritic composition using the Nixpkgs module system directly.
{ lib }:
(lib.evalModules {
  modules = [ {
    options.nixosModules = lib.mkOption {
      type = lib.types.attrsOf lib.types.deferredModule;
      default = { };
      description = "Role modules assembled by automatically discovered platform features.";
    };
  } ] ++ (import ./site-modules.nix ../platform-modules);
}).config.nixosModules
