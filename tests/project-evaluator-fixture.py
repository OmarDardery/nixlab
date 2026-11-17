"""Real NixOS VM check for unprivileged offline project Nix evaluation."""
import sys
import subprocess
from pathlib import Path
import project_plans

good, forbidden = map(Path, sys.argv[1:3])
try:
    inventory = project_plans.evaluate(good, "1" * 32)
except ValueError:
    # Disposable fixture only: expose bounded Nix/systemd errors in CI.
    # Never use this reporting path for user project source or secrets.
    result = subprocess.run([
        "journalctl", "--unit=nixlab-project-eval-" + "1" * 32,
        "--no-pager", "--output=cat", "--lines=25"
    ], capture_output=True, text=True, timeout=15)
    print("EVALUATOR_UNIT_JOURNAL", result.stdout[-5000:], result.stderr[-1000:], flush=True)
    raise
assert inventory == {"schemaVersion": 1, "projects": {}}, inventory

try:
    project_plans.evaluate(forbidden, "2" * 32)
except ValueError as exc:
    # A failed eval is bounded and does not echo the sensitive Nix path.
    assert "Restricted project evaluation failed" in str(exc), str(exc)
    assert "shadow" not in str(exc)
else:
    raise AssertionError("Untrusted project Nix accessed host protected file")
print("PROJECT-EVAL-SANDBOX-PASSED")
