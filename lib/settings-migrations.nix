# Pure, read-compatible migrations. Directory layout versions are independent.
stored:
let
  version = stored.schemaVersion or 0;
  legacy = if (stored.ready or false) && !((stored.monitoring or { }) ? collection)
    then stored // { monitoring = (stored.monitoring or { }) // { collection = true; }; }
    else stored;
in
if !builtins.isAttrs stored then throw "NixLab settings must be an object"
else if !builtins.isInt version || version < 0 || version > 1 then
  throw "Unsupported NixLab settings schema version"
else if version == 0 then legacy // { schemaVersion = 1; }
else stored
