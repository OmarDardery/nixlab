root:
let
  modern = builtins.pathExists (root + "/machine/format.json");
  version = if modern then (builtins.fromJSON (builtins.readFile (root + "/machine/format.json"))).version else 0;
  base = if modern then root + "/machine" else root;
in if version != 0 && version != 1 then throw "Unsupported Nixlab machine directory version" else {
  settings = if modern then base + "/settings.json" else base + "/local/settings.json";
  installation = if modern then base + "/installation.json" else base + "/local/installation.json";
  modules = if modern then base + "/modules" else base + "/site-modules";
  installed = base + "/installed-system/configuration.nix";
}
