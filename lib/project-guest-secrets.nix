# NixOS guest-only sops-nix option fragment from typed, plaintext-free metadata.
# The guest must already have its OWN age identity at guestAgeKeyFile, and its
# public recipient must have been reviewed and re-encrypted into secrets.enc.yaml.
# Never generate/import the host's /var/lib/nixlab/keys/projects.agekey here.
{ lib }:
{ project, instance, bindings, sopsFile, guestAgeKeyFile }:
let
  inherit (builtins) isAttrs isString match;
  validName = name: isString name && match "[A-Za-z_][A-Za-z0-9_]{0,127}" name != null;
  validInstance = value: isString value && match "[a-z][a-z0-9-]{0,62}" value != null;
  validKey = value: isString value && match "/var/lib/sops-nix/[A-Za-z0-9_.-]+" value != null;
  chosen = lib.filterAttrs (_: binding:
    isAttrs binding && (binding.instance or null) == instance
  ) bindings;
  validBinding = alias: binding:
    validName alias &&
    (binding.backend or null) == "nixos-sops" &&
    validName (binding.key or null) &&
    isString (binding.destination or null) &&
    match "/run/secrets/[A-Za-z_][A-Za-z0-9_/-]*" binding.destination != null &&
    (binding.restartUnits or null) != null &&
    builtins.isList binding.restartUnits &&
    lib.all (unit: isString unit && match "[A-Za-z0-9_.@-]+\\.service" unit != null)
      binding.restartUnits;
in
if !validInstance project || !validInstance instance then
  throw "Invalid NixLab guest secret consumer"
else if !(isAttrs bindings) || chosen == {} then
  throw "Guest has no explicit sops-nix bindings"
else if !(validKey guestAgeKeyFile) then
  throw "NixLab guest identity must be a pre-enrolled guest-local /var/lib/sops-nix file"
else if !isString sopsFile || match ".*secrets\\.enc\\.yaml" sopsFile == null then
  throw "NixLab guest SOPS source must be ciphertext"
else if !(lib.all (alias: validBinding alias chosen.${alias}) (builtins.attrNames chosen)) then
  throw "Unsafe NixLab guest SOPS consumer"
else {
  # Guest ciphertext and the guest-owned identity are provisioned at runtime.
  # An evaluated Nix path must never import a live secret, or force a
  # nonexistent first-boot file into the Nix store. The broker owns delivery.
  sops.validateSopsFiles = false;
  sops.useSystemdActivation = true;
  sops.age.keyFile = guestAgeKeyFile;
  sops.age.generateKey = false;
  # Disable implicit SSH-key fallback: only the reviewed guest identity may
  # decrypt this project's ciphertext. Never use the host master identity.
  sops.age.sshKeyPaths = [];
  sops.gnupg.sshKeyPaths = [];
  sops.secrets = lib.mapAttrs (_: binding: {
    inherit sopsFile;
    key = binding.key;
    path = binding.destination;
    mode = "0400";
    restartUnits = binding.restartUnits;
  }) chosen;
}
