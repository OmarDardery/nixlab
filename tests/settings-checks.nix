{ lib }:
let
  check = values: import ../lib/settings.nix { inherit lib values; };
  rejects = values: !(builtins.tryEval (builtins.deepSeq (check values) true)).success;
  defaults = import ../site-defaults.nix;
in {
  defaultsPreserved = check defaults == defaults;
  partialInventory = (check { hostName = "fixture"; }).apps == defaults.apps;
  unknownSettingRejected = rejects { inventedOption = true; };
  wrongBooleanRejected = rejects { apps.enable = "yes"; };
  invalidIngressRejected = rejects { tunnel.ingress."example.invalid" = 10; };
  invalidMountRejected = rejects { mounts = [ { name = "bad"; uuid = "abcd"; path = "/srv/nixlab/bad"; fsType = "invented"; role = "data"; required = true; readOnly = false; } ]; };
  optionalStartIsAbsent = !((builtins.head (check { instances = [ { name = "example"; image = "images:fixture"; } ]; }).instances) ? start);
}
