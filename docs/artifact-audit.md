# Reviewing artifact retirement

Run `python3 scripts/artifact_audit.py` in a checkout for an offline, read-only,
versioned JSON inventory of tracked files and observed text references. Redirect
it outside the source tree for a baseline report. The result is advisory; it is
not a deletion list or blocking CI gate.

For each proposed retirement, inspect imports, packaging (`admin/source.nix` and
installer wrappers), Nix module/flake outputs, generated systemd commands,
frontend assets, workflows, examples, recovery procedures and public CLI users.
Substring references can be false positives; absent references cannot prove an
external command or generated path unused. Binary/large files are inventoried
but not searched. Record uncovered consumers as unknown.

A removal PR must name the artifact, actual consumers, compatibility decision,
regression coverage and rollback. Preserve machine configuration, previous
closures, transaction recovery, Incus ownership and backup restart intent.
Measure code/file counts only as context; fewer files do not prove improvement.
