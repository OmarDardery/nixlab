"""Real NixOS/Incus fixture: source review -> apply -> crash/retry -> forward restore.

No fake Incus backend: only the short-lived systemd worker launcher is mocked
because the crash failpoint must be placed *after* the actual Incus REST write.
"""
import fcntl
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

BASE = Path("/var/lib/nixlab-project-tx-fixture")
BASE.mkdir(mode=0o700, exist_ok=True)
os.environ["NIXLAB_STATE"] = str(BASE / "state")
os.environ["NIXLAB_PROJECTS"] = str(BASE / "projects")
os.environ["NIXLAB_ROOT"] = str(BASE / "root")

import runtime as rt
import project_transactions as tx
import project_plans
import reconcile
from locking import open_lock

rt.STATE.mkdir(mode=0o700, exist_ok=True)
tx.INCUS_LOCK = BASE / "incus.lock"

def incus(*args):
    return subprocess.run(("incus", *args), check=True, text=True,
                          capture_output=True, timeout=90).stdout

# The project and instance were created by this disposable VM test *before*
# the project source transaction. An unowned guest must never be adopted.
current, initial_etag = reconcile.read_instance("alpha", "printer")
assert current["config"]["limits.cpu"] == "2"
assert current["config"]["limits.memory"] == "2GiB"
before = {
    "version": 1,
    "config": {"limits.cpu": "2", "limits.memory": "2GiB"},
    "devices": current.get("devices", {}),
    "profiles": current.get("profiles", []),
}
incus("config", "set", "printer", "user.nixlab.managed=true", "--project", "alpha")
incus("config", "set", "printer",
      "user.nixlab.last-applied=" + json.dumps(before, sort_keys=True, separators=(",", ":")),
      "--project", "alpha")

incus("start", "printer", "--project", "alpha")
incus("exec", "printer", "--project", "alpha", "--disable-stdin", "--",
      "/bin/sh", "-c", "echo must-survive > /var/lib/test/transaction")

manager = tx.ProjectTransactions()
manager.initialize(
    "{ outputs = { self }: { nixlabProjects = { schemaVersion = 1; projects = {}; }; }; }",
    json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}}),
    principal="vm-fixture")
assert tx.validate_workspace(manager.workspace)["projects"] == {}

def declaration(cpus):
    return (
        '{ ... }: { nixlab.project.name = "alpha"; '
        'nixlab.project.enable = true; '
        'nixlab.project.backend = "incus-native"; '
        'nixlab.project.incusProject = "alpha"; '
        'nixlab.project.resources.cpus = ' + str(cpus) + '; '
        'nixlab.project.resources.memory = "2GiB"; '
        'nixlab.project.claims = [ "instance/printer" ]; }'
    )

def queue(review, *, resume=False):
    # Only the worker dispatch is intercepted. Every review, Nix evaluation,
    # source exchange, ETag read and guest write is real in this VM.
    with patch.object(rt, "pin_worker_runtime"), patch.object(
        tx.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")
    ):
        result = manager.deploy(review["id"], review["planHash"], resume=resume)
    assert result["deployment"] == "queued", result

first = manager.review({"alpha/default.nix": declaration(3)}, principal="vm-fixture")
assert first["status"] == "ready"
assert first["runtimeActions"][0]["action"] == "update"
assert first["runtimeActions"][0]["after"]["cpus"] == "3"
assert first["planHash"] and first["candidateHash"] != first["baseHash"]
assert manager.publish(first["id"], first["planHash"])["status"] == "published-source"
queue(first)

# Real Incus PUT commits, but the worker crashes before its verified journal
# update. No subsequent worker can blindly repeat the write.
original = project_plans.recheck_and_apply
mutations = []
def crash_after_real_write(project, approved, *, verify_only=False):
    result = original(project, approved, verify_only=verify_only)
    mutations.append((approved["name"], verify_only))
    raise RuntimeError("power lost before recording verified Incus ETag")
with patch.object(project_plans, "recheck_and_apply", side_effect=crash_after_real_write):
    interrupted = manager.run_deployment(first["id"])
assert interrupted["deployment"] == "partial", interrupted
assert interrupted["deploymentResources"]["alpha/printer"]["phase"] == "applying"
assert incus("config", "get", "printer", "limits.cpu", "--project", "alpha").strip() == "3"
assert mutations == [("printer", False)]
assert (rt.STATE / "project-deploy-active.json").exists()

try:
    queue(first)
    raise AssertionError("partial journal requires explicit resume")
except ValueError as exc:
    assert "Resume" in str(exc), str(exc)

queue(first, resume=True)
with patch.object(reconcile, "write_instance",
                  side_effect=AssertionError("Resume must verify, not repeat the Incus write")):
    recovered = manager.run_deployment(first["id"])
assert recovered["deployment"] == "verified", recovered
assert recovered["deploymentResources"]["alpha/printer"]["phase"] == "verified"
assert not (rt.STATE / "project-deploy-active.json").exists()
assert incus("exec", "printer", "--project", "alpha", "--disable-stdin", "--",
             "/bin/cat", "/var/lib/test/transaction").strip() == "must-survive"

# Configuration rollback of a *deployed* revision is unsafe: review a new
# forward change instead; existing data/volume is left untouched.
try:
    manager.rollback(first["id"], first["planHash"])
    raise AssertionError("Applied resources cannot source-only rollback")
except ValueError as exc:
    assert "Source-only rollback" in str(exc), str(exc)

second = manager.review({"alpha/default.nix": declaration(2)}, principal="vm-fixture")
assert second["runtimeActions"][0]["action"] == "update"
manager.publish(second["id"], second["planHash"])
queue(second)
verified = manager.run_deployment(second["id"])
assert verified["deployment"] == "verified", verified
assert incus("config", "get", "printer", "limits.cpu", "--project", "alpha").strip() == "2"
assert incus("exec", "printer", "--project", "alpha", "--disable-stdin", "--",
             "/bin/cat", "/var/lib/test/transaction").strip() == "must-survive"

# Noop operation is idempotent; it must not enqueue or restart anything.
unchanged = manager.deploy(second["id"], second["planHash"])
assert unchanged["deployment"] == "verified"
print("PROJECT-TX-REAL-INCUS-PASSED")
