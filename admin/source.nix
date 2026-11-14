# One Python runtime dependency list for the installed CLI and nix-run launchers.
{ pkgs }:
let
  scripts = [
    ../scripts/preflight.py
    ../scripts/backup.py
    ../scripts/backup_plan.py
    ../scripts/reconcile.py
    ../scripts/infrastructure.py
    ../scripts/guest_deploy.py
    ../scripts/locking.py
    ../scripts/foundation.py
    ../scripts/backup_health.py
    ../scripts/incus_client.py
  ];
in pkgs.runCommand "nixlab-admin-runtime" { } ''
  mkdir -p "$out"
  cp -r ${./.}/. "$out/"
  chmod -R u+w "$out"
  ${pkgs.lib.concatMapStringsSep "\n" (file: ''cp ${file} "$out/${builtins.baseNameOf file}"'') scripts}
  cp ${pkgs.writeText "nixlab-default-settings.json" (builtins.toJSON (import ../site-defaults.nix))} "$out/default-settings.json"
''
