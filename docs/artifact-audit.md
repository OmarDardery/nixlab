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

## Reproducible baseline and report

Run from a clean, fully checked-out repository. This command only reads tracked
files and prints JSON; it does not evaluate Nix, inspect the live host, download
dependencies, modify the checkout, or delete anything:

```sh
python3 scripts/artifact_audit.py > /tmp/nixlab-artifact-baseline.json
python3 -m unittest discover -s tests -p 'test_artifact_audit.py' -v
```

The versioned report includes:

| Field | Interpretation |
|---|---|
| `trackedCount`, `textCount` | All Git-tracked paths versus UTF-8 sources actually inspected |
| `inspectionOmissions` | Missing/sparse files, symlinks (including symlinked parents), binary or oversized files, invalid UTF-8; these **were not inspected** |
| `baseline.classifications` | File types, including known public entrypoints, tests/fixtures and tracked generated artifacts |
| `baseline.sourceFiles`, `testFiles` | Counts of tracked code and tests, not a quality score |
| `baseline.sourceLinesInspected`, `testLinesInspected` | Count of *inspected* code lines only; excludes skipped/binary sources |
| `baseline.cliBinaries`, `publicFlakeAttributesFromContract`, `machinePathsFromContract`, `runtimePathsFromContract`, `systemdUnitsFromContract` | Public compatibility contract from `tests/fixtures/architecture-contract.json` when present; empty otherwise, **not** an exhaustive external-use inventory |
| `baseline.unresolvedPolicyDuplicationToReview` | Known sources of complexity requiring separate owner issues; advisory, not automatically proven duplicates |
| `artifacts[].observedReferences` | Files containing path/basename/module-name substrings. These can include comments/false positives |
| `artifacts[].confidence` | `text-reference-only` or `unknown`; **never** a proof that deletion is safe |
| `artifacts[].action` | `keep` for a known public entrypoint or a text hit, otherwise `investigate`; no `delete` action |

The small `PUBLIC_ENTRYPOINTS` allowlist in the script protects known published
flake, CLI and recovery entrypoints despite no textual references. It is not an
allowlist for *deleting* other files; dynamic imports, Nix interpolations, CSS/HTML
routes, systemd packaging, compatibility callers and direct operator use still
require manual tracing. Never interpret `investigate` as an orphan proven dead.

If a proposed refactor is meant to shrink the codebase, capture a before/after
baseline from the same checkout environment and source revision. Compare all
field values **alongside** behavior/regression tests, not just source lines.

## Checklist for an actual removal PR

1. Identify the file's real consumers in Python imports/dynamic modules, Nix
   `import`, `lib/platform.nix`, flake exports, systemd `ExecStart`,
   packaged entrypoints (`admin/source.nix`, `installer/package.nix`),
   web asset handlers, JS imports, CI and release scripts, docs and recovery
   media. Inspect paths in `inspectionOmissions` separately.
2. Show why its uses are safely replaced or intentionally deprecated. Identify
   externally callable binaries, portals, state paths, Nix outputs and native
   Incus owners; record any migration, adapter and revert procedure.
3. Test an intentionally unreferenced helper and fixture (both should be
   `investigate`); remove a reference and verify the tool changes its result;
   verify a published entrypoint remains `keep` and a known served asset
   identifies its actual consumer.
4. Run focused Python and JS suites and Nix evaluation/CI tests on the exact
   source revision. For host networking, rollback, guest data and backup code,
   retain the appropriate real NixOS/Incus integration and physical acceptance
   evidence. A text-only scan never qualifies runtime behavior.
5. Only then remove the specific file in a small reviewed PR with a rollback
   and compatibility rationale. Do not delete `Manager`/worker guards,
   network recovery, Incus ETag ownership records, guest restart intent,
   backup manifests or the pinned `flake.lock` merely to reduce file counts.

This audit intentionally adds **no new runtime service**, no extra dependency,
no automatic file removal and no blocking CI gate.
