{ config, lib, pkgs, site, appImage ? null, ... }:
let
  inherit (lib) mkOption types;
  cfg = config.nixlab.deployment;
  targetType = types.submodule ({ name, ... }: {
    options = {
      name = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]{0,62}"; default = name; };
      project = mkOption { type = types.strMatching "[A-Za-z0-9][A-Za-z0-9_.-]{0,62}"; default = "lab"; };
      system = mkOption { type = types.package; description = "Reviewed NixOS system.build.toplevel for this existing guest."; };
      dependsOn = mkOption { type = types.listOf types.str; default = [ ]; };
      healthUnits = mkOption { type = types.listOf (types.strMatching "[A-Za-z0-9@_.:-]+\\.(service|target|socket)"); default = [ "multi-user.target" ]; };
      httpChecks = mkOption { type = types.listOf (types.strMatching "http://127\\.0\\.0\\.1:[0-9]+/[^[:space:]]*"); default = [ ]; description = "Guest-loopback HTTP endpoints; no credentials, redirects or response bodies are retained."; };
      healthTimeout = mkOption { type = types.ints.between 1 300; default = 60; };
      maxAttempts = mkOption { type = types.ints.between 1 5; default = 3; };
      restartPolicy = mkOption { type = types.enum [ "defer" "reboot" ]; default = "defer"; };
      rollbackOnFailure = mkOption { type = types.bool; default = true; };
    };
  });
  targets = cfg.targets;
  manifest = lib.mapAttrs (key: target:
    let probe = pkgs.writeShellApplication {
      name = "nixlab-guest-health";
      runtimeInputs = [ pkgs.systemd pkgs.curl ];
      text = lib.concatMapStringsSep "\n" (unit: "systemctl is-active --quiet ${lib.escapeShellArg unit}") target.healthUnits
        + "\n" + lib.concatMapStringsSep "\n" (url: "test \"$(curl --fail --silent --output /dev/null --write-out '%{http_code}' --max-time 5 --proto '=http' --noproxy '*' ${lib.escapeShellArg url})\" = 200") target.httpChecks;
    }; in {
      inherit (target) name project dependsOn healthUnits healthTimeout maxAttempts restartPolicy rollbackOnFailure;
      system = toString target.system; probe = toString probe; httpChecks = builtins.length target.httpChecks;
    }) targets;
  declared = map (i: "${i.project}/${i.name}") config.nixlab.incus.desiredInstances;
in {
  options.nixlab.deployment = {
    apps.enable = mkOption { type = types.bool; default = false; description = "Opt the apps VM into explicit, journaled deployment after host confirmation."; };
    targets = mkOption { type = types.attrsOf targetType; default = { }; };
    manifest = mkOption { type = types.attrs; readOnly = true; internal = true; };
  };
  config = {
    nixlab.deployment.manifest = manifest;
    nixlab.deployment.targets = lib.mkIf (cfg.apps.enable && site.apps.enable && appImage != null) {
      apps = { name = "apps"; system = appImage.system; healthUnits = [ "docker-portainer.service" ]; };
    };
    environment.etc."nixlab/deployments.json".text = builtins.toJSON manifest;
    assertions = [
      { assertion = builtins.length (builtins.attrNames targets) <= 16; message = "Declare at most 16 guest deployment targets."; }
      { assertion = lib.all (key: builtins.match "[a-z][a-z0-9-]{0,31}" key != null) (builtins.attrNames targets); message = "Deployment keys must be short lowercase names."; }
      { assertion = lib.all (t: builtins.elem "${t.project}/${t.name}" declared) (builtins.attrValues targets); message = "Only explicitly source-owned guests can have deployment targets."; }
      { assertion = cfg.apps.enable || lib.all (t: t.project != "lab" || t.name != "apps") (builtins.attrValues targets); message = "Use nixlab.deployment.apps.enable for lab/apps so onboarding does not deploy its OS twice."; }
      { assertion = lib.all (t: t.healthUnits != [ ] || t.httpChecks != [ ]) (builtins.attrValues targets); message = "Every deployment needs at least one health check."; }
      { assertion = !cfg.apps.enable || (site.apps.enable && site.setup.enable); message = "Coordinated apps deployment requires the managed apps VM and onboarding."; }
      { assertion = !cfg.apps.enable || builtins.match "(.+@)?sha256:[0-9a-f]{64}" site.apps.portainerImage != null; message = "Pin the Nix-owned Portainer image by immutable sha256 digest before coordinated deployment."; }
      { assertion = !cfg.apps.enable || !site.apps.enableDemo; message = "The example demo has mutable upstream image tags; keep it outside coordinated deployment until its images are pinned."; }
    ];
  };
}
