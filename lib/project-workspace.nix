# Read-only, deterministic project workspace evaluator.
# Deliberately independent of the NixOS host/module system and Incus runtime.
{ lib }:
{ root }:
let
  inherit (lib) mkOption types;
  fail = message: throw "NixLab projects: ${message}";
  sorted = names: lib.sort builtins.lessThan names;
  isIgnored = name: lib.hasPrefix "_" name || lib.hasPrefix "." name;

  # A path reference in a manifest is never allowed to escape a project.
  validRelative = value:
    value == null ||
    (builtins.isString value &&
      lib.all (part:
        part != "" && part != "." && part != ".." &&
        builtins.match "[A-Za-z0-9._-]+" part != null
      ) (lib.splitString "/" value));

  # readDir detects symlinks without following them. Do not let a workspace
  # import files outside its immutable reviewed source by accident.
  entriesAt = dir:
    let entries = builtins.readDir dir;
        links = builtins.filter (name: entries.${name} == "symlink")
          (builtins.attrNames entries);
    in if links != [] then fail "Symlink forbidden in ${toString dir}: ${builtins.head links}"
       else entries;

  # Validate the *whole* project source tree, even inert resource files.
  # The later isolated evaluator must still use the Python preflight and a
  # frozen candidate: pure Nix cannot prevent arbitrary explicit imports.
  scanTree = directory: depth:
    if depth > 16 then fail "Project source exceeds 16 directory levels"
    else let
      entries = entriesAt directory;
      names = sorted (builtins.attrNames entries);
      forbidden = builtins.filter (name:
        name == ".env" || lib.hasPrefix ".env." name ||
        lib.hasSuffix ".agekey" name ||
        builtins.elem name [ "id_rsa" "id_ed25519" "id_ecdsa" ]
      ) names;
      childDirs = builtins.filter (name:
        entries.${name} == "directory" && name != ".git"
      ) names;
    in if forbidden != [] then
      fail "Potential plaintext credential in project source: ${builtins.head forbidden}"
    else builtins.deepSeq
      (map (name: scanTree (directory + "/${name}") (depth + 1)) childDirs)
      true;

  # Deterministic recursion, with disabled/hidden subtrees ignored. Paths in
  # files/ (including .nix files) are never discovered as modules.
  moduleFiles = directory: depth:
    if depth > 8 then fail "Module nesting exceeds eight directories"
    else let entries = entriesAt directory; in
      lib.concatMap (name:
        let kind = entries.${name};
            child = directory + "/${name}";
        in if isIgnored name then []
           else if kind == "directory" then moduleFiles child (depth + 1)
           else if kind == "regular" && lib.hasSuffix ".nix" name then [ child ]
           else if kind == "regular" then []
           else fail "Unsupported module entry ${toString child}"
      ) (sorted (builtins.attrNames entries));

  schema = directoryName: { lib, ... }: {
    options.nixlab.project = {
      name = mkOption {
        type = types.strMatching "[a-z][a-z0-9-]{0,62}";
        default = directoryName;
        description = "Stable managed project identity.";
      };
      enable = mkOption { type = types.bool; default = true; };
      backend = mkOption {
        type = types.enum [ "incus-native" "incus-compose" ];
        default = "incus-native";
      };
      incusProject = mkOption {
        type = types.strMatching "[a-z][a-z0-9-]{0,62}";
        default = directoryName;
      };
      resources.cpus = mkOption { type = types.ints.positive; default = 2; };
      resources.memory = mkOption {
        type = types.strMatching "[1-9][0-9]*(KiB|MiB|GiB)";
        default = "2GiB";
      };
      # Values, passwords and token contents are not Nix options. Only the
      # relative name of the *ciphertext* resource is represented here.
      secrets.file = mkOption { type = types.nullOr types.str; default = null; };
      backup.enable = mkOption { type = types.bool; default = true; };
      backup.consistency = mkOption {
        type = types.enum [ "stop" "quiesce" "none" ];
        default = "stop";
      };
      # Reserved identities used by later reconcilers; collisions are rejected
      # before anyone is authorized to mutate Incus.
      claims = mkOption {
        type = types.listOf (types.strMatching "[a-z][a-z0-9-]*/[A-Za-z0-9_.-]+");
        default = [];
      };
    };
  };

  rootEntries = entriesAt root;
  projectDirs = builtins.filter (name:
    let kind = rootEntries.${name};
    in !isIgnored name && kind == "directory" &&
      ((entriesAt (root + "/${name}"))."default.nix" or null) == "regular"
  ) (sorted (builtins.attrNames rootEntries));

  projects = map (directoryName:
    let directory = root + "/${directoryName}";
        entries = entriesAt directory;
        hasModules = entries ? modules;
        sources = if !hasModules then []
          else if entries.modules == "directory"
            then moduleFiles (directory + "/modules") 0
            else fail "modules must be a directory in ${directoryName}";
        # The default module is first; all discovered fragments are sorted.
        evaluated = lib.evalModules {
          modules = [ (schema directoryName) (directory + "/default.nix") ] ++ sources;
        };
        p = evaluated.config.nixlab.project;
        secret = p.secrets.file;
    in if !(scanTree directory 0) then
      fail "Unsafe project source directory"
    else if !validRelative secret then
      fail "Project ${directoryName} has an unsafe ciphertext reference"
    else if builtins.length sources > 256 then
      fail "Project ${directoryName} exceeds 256 Nix module files"
    else {
      name = p.name;
      inherit (p) enable backend incusProject resources secrets backup claims;
      source = directoryName;
      modules = map (path:
        lib.removePrefix ("${toString directory}/") (toString path)
      ) sources;
    }
  ) projectDirs;

  names = map (p: p.name) projects;
  incusProjects = map (p: p.incusProject) projects;
  claims = lib.concatMap (p: p.claims) projects;
  noDuplicates = values: builtins.length values == builtins.length (lib.unique values);
in
  if builtins.length projectDirs > 128 then fail "Workspace exceeds 128 projects"
  else if !(noDuplicates names) then fail "Duplicate NixLab project identities"
  else if !(noDuplicates incusProjects) then fail "Multiple projects claim the same Incus project"
  else if !(noDuplicates claims) then fail "Conflicting declared resource claims"
  else {
    schemaVersion = 1;
    projects = builtins.listToAttrs (map (project: {
      name = project.name;
      value = project;
    }) projects);
  }
