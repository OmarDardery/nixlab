"""Real NixOS VM check for unprivileged offline project Nix evaluation."""
import sys
from pathlib import Path
import project_plans

good, forbidden = map(Path, sys.argv[1:3])
inventory = project_plans.evaluate(good, "1" * 32)
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
