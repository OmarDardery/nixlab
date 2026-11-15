# Minimal real source for management fixtures using the production Nix reader.
{ pkgs }:
pkgs.runCommand "nixlab-test-site-source" { } ''
  mkdir -p $out/lib
  cp ${../site.nix} $out/site.nix
  cp ${../site-defaults.nix} $out/site-defaults.nix
  cp ${../lib/machine.nix} $out/lib/machine.nix
  cp ${../lib/settings-migrations.nix} $out/lib/settings-migrations.nix
''
