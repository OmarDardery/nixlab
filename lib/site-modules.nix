# Only this administrator-owned tree is discovered, never examples or recovery data.
root:
let
  walk = dir: builtins.concatLists (map (name:
    let kind = (builtins.readDir dir).${name}; path = dir + "/${name}";
    in if builtins.substring 0 1 name == "_" then [ ]
       else if kind == "directory" then walk path
       else if kind == "regular" && builtins.match ".*\\.nix" name != null then [ path ]
       else if kind == "symlink" then throw "site-modules must not contain symlinks"
       else [ ]
  ) (builtins.attrNames (builtins.readDir dir)));
in if builtins.pathExists root then walk root else [ ]
