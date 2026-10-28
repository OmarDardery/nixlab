"""Small ownership-aware Incus reconciler. No shell interpolation or deletion."""
import copy
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

OWNER = "user.nixlab.managed"

def run(*args, data=None):
    result = subprocess.run(["incus", *args], input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Incus exited {result.returncode}")
    return result.stdout

def plan(current, spec):
    if current.get("config", {}).get(OWNER) != "true":
        raise ValueError(f"Refusing to modify unowned instance {spec['name']}")
    if current["type"] != spec["type"]:
        raise ValueError("Instance type is immutable; migrate explicitly")
    if OWNER in spec.get("config", {}) and spec["config"][OWNER] != "true":
        raise ValueError("The Nixlab ownership marker cannot be removed by reconciliation")
    writable = {k: copy.deepcopy(current[k]) for k in
                ("architecture", "config", "devices", "ephemeral", "profiles", "description") if k in current}
    result = copy.deepcopy(writable)
    result.setdefault("config", {}).update(spec.get("config", {}))
    result.setdefault("devices", {}).update(spec.get("devices", {}))
    result["profiles"] = spec.get("profiles", ["base"])
    return result if result != writable else None

def reconcile(spec):
    if OWNER in spec.get("config", {}) and spec["config"][OWNER] != "true":
        raise ValueError("The Nixlab ownership marker cannot be removed by reconciliation")
    for k in ("name", "project"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", spec[k]):
            raise ValueError(f"Invalid {k}")
    project, name = spec["project"], spec["name"]
    # List is checked first: an API/permission error is never mistaken for absence.
    existing = json.loads(run("list", "--project", project, "--format=json"))
    found = next((i for i in existing if i["name"] == name), None)
    if found is None and spec.get("manageExistingOnly"):
        print(f"absent {project}/{name}; no VM created")
        return
    if found is None:
        args = ["init", spec["image"], name, "--project", project,
                "--config", OWNER + "=true"]
        if spec["type"] == "virtual-machine":
            args.append("--vm")
        elif spec["type"] != "container":
            raise ValueError("Unknown instance type")
        for profile in spec.get("profiles", ["base"]):
            args += ["--profile", profile]
        # Apply disks/NIC reservations before the first boot, including after a failed prior run.
        run(*args)
    current = json.loads(run("query", "/1.0/instances/" + quote(name, safe="") + "?project=" + quote(project, safe="")))
    if spec.get("manageExistingOnly") and current.get("config", {}).get(OWNER) != "true":
        print(f"unowned {project}/{name}; left untouched")
        return
    update = plan(current, spec)
    if update is not None:
        run("config", "edit", name, "--project", project, data=json.dumps(update))
        print(f"updated {project}/{name}")
    else:
        print(f"unchanged {project}/{name}")
    state = json.loads(run("list", "--project", project, "--format=json"))
    status = next(i["status"] for i in state if i["name"] == name)
    if spec.get("stop", False) and status == "Running":
        run("stop", name, "--project", project, "--timeout", "120")
    elif spec.get("start", True) and status == "Stopped":
        run("start", name, "--project", project)

if __name__ == "__main__":
    # Shared with the export job; a rebuild cannot change instance resources mid-export.
    with open("/run/lock/nixlab-incus.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for spec in json.loads(Path(sys.argv[1]).read_text()):
            reconcile(spec)
