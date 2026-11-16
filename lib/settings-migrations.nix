# Pure, read-compatible migrations. Directory layout versions are independent.
stored:
let
  version = stored.schemaVersion or 0;
  legacy = if (stored.ready or false) && !((stored.monitoring or { }) ? collection)
    then stored // { monitoring = (stored.monitoring or { }) // { collection = true; }; }
    else stored;
  # Read compatibility for the retired dashboard's port. Its image is never
  # reused for Arcane. This is settings normalization, not a data migration.
  current = if version == 0 then legacy else stored;
  oldApps = current.apps or { };
  normalized = if oldApps ? portainerPort || oldApps ? portainerImage then
    current // { apps = builtins.removeAttrs oldApps [ "portainerPort" "portainerImage" ]
      // (if !(oldApps ? arcanePort) && oldApps ? portainerPort
          then { arcanePort = oldApps.portainerPort; } else { }); }
    else current;
in
if !builtins.isAttrs stored then throw "NixLab settings must be an object"
else if !builtins.isInt version || version < 0 || version > 1 then
  throw "Unsupported NixLab settings schema version"
else if version == 0 then normalized // { schemaVersion = 1; }
else normalized
