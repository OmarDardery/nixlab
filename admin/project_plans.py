"""Bounded project inventory and reviewed existing-native-instance adapter.

Nix expressions are evaluated in a separate unprivileged systemd sandbox,
never by the root broker. Only owned existing native Incus guests may receive
in-place CPU/memory updates. Creating VMs, changing storage, Compose and USB
devices belongs to #133 and is deliberately refused until qualified.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import reconcile
from project_workspace import validate_workspace

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
MEMORY = re.compile(r"[1-9][0-9]*(?:KiB|MiB|GiB)\Z")
CLAIM = re.compile(r"(instance|volume|network)/([A-Za-z0-9_.-]+)\Z")
MAX_OUTPUT = 1024 * 1024
HOST_DESIRED = Path("/etc/nixlab/desired-instances.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cached_source(source):
    """Add checked nonsecret/SOPS source to the public Nix store, without eval.

    The workspace is already reviewed for prohibited plaintext file paths by
    project_workspace.validate_workspace(). Nix store paths are world-readable:
    do not pass decrypted credentials or private key material here.
    """
    source = Path(source)
    if (source / ".git").exists() or (source / ".git").is_symlink():
        raise ValueError("Unreviewed Git metadata cannot be copied into the public Nix store")
    before = validate_workspace(source)
    try:
        result = subprocess.run(
            ["nix-store", "--add", str(source)],
            capture_output=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Could not pin project source in the Nix store") from None
    try:
        path = result.stdout.decode("ascii").strip()
    except UnicodeError:
        path = ""
    if (result.returncode != 0 or len(result.stdout) > 256 or
        not re.fullmatch(r"/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+", path)):
        raise ValueError("Could not pin project source in the Nix store")
    if validate_workspace(Path(path))["sourceHash"] != before["sourceHash"]:
        raise ValueError("Nix store project snapshot differs from reviewed source")
    return path


def evaluate(source, identity):
    """Evaluate typed project declarations in a restricted rootless Nix interpreter.

    Nix's flake path accessor can CREATE `/nix/store/*-source.lock` even with
    --no-write-lock-file and --offline. A read-only store therefore fails.
    Instead, interpret NixLab's trusted, pinned project schema with
    nix-instantiate --eval in a DynamicUser sandbox, using the immutable
    checked source and a pinned Nixpkgs lib. Never fetch/execute the uploaded
    flake's custom outputs, request builds, or open the host Nix daemon.
    """
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid reviewed source identity")
    store_path = _cached_source(Path(source))
    evaluator = os.environ.get("NIXLAB_PROJECT_EVAL_LIB", "")
    nixpkgs_lib = os.environ.get("NIXLAB_PROJECT_NIXPKGS_LIB", "")
    # These trusted paths are supplied by the packaged NixLab binary, not
    # by the project's flake.nix. Prevent Nix expression string injection.
    for value in (evaluator, nixpkgs_lib):
        if not re.fullmatch(r"/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+(?:/[A-Za-z0-9._-]+)*", value):
            raise ValueError("Trusted project evaluator is not installed")
    expression = (
        "let lib = import " + nixpkgs_lib + "; "
        "evaluate = import " + evaluator + " { inherit lib; }; "
        "in evaluate { root = " + store_path + "; }"
    )
    nix_binary = shutil.which("nix-instantiate") or "/run/current-system/sw/bin/nix-instantiate"
    command = [
        "systemd-run", "--pipe", "--wait", "--collect", "--quiet",
        "--unit=nixlab-project-eval-" + identity,
        "--property=Type=exec",
        "--property=DynamicUser=yes",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes",
        "--property=PrivateTmp=yes",
        "--property=PrivateDevices=yes",
        "--property=PrivateNetwork=yes",
        "--property=IPAddressDeny=any",
        "--property=RestrictAddressFamilies=AF_UNIX",
        "--property=NoNewPrivileges=yes",
        "--property=CapabilityBoundingSet=",
        "--property=SystemCallFilter=~@mount",
        "--property=MemoryMax=536870912",
        "--property=TasksMax=32",
        "--property=RuntimeMaxSec=90",
        "--property=UMask=0077",
        "--property=InaccessiblePaths=-/var/lib/incus -/var/lib/nixlab-secrets -/var/lib/nixlab-admin -/etc/nixlab -/run/nix -/nix/var/nix/daemon-socket",
        "--setenv=HOME=/tmp",
        "--setenv=NIX_REMOTE=local?read-only=true",
        "--", nix_binary, "--extra-experimental-features", "read-only-local-store",
        "--eval", "--strict", "--json",
        "--option", "restrict-eval", "true",
        "--option", "allow-import-from-derivation", "false",
        "--option", "substituters", "",
        "--option", "builders", "",
        "--expr", expression,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=110, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Restricted project evaluation could not start or timed out") from None
    if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT:
        raise ValueError("Restricted project evaluation failed; check offline project Nix source")
    try:
        raw = json.loads(result.stdout)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid project evaluation output") from None
    return validate_inventory(raw)


def validate_inventory(raw):
    if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "projects"} or raw["schemaVersion"] != 1:
        raise ValueError("Unsupported typed project inventory")
    projects = raw["projects"]
    if not isinstance(projects, dict) or len(projects) > 128:
        raise ValueError("Invalid number of declarative projects")
    results = {}
    claimed = set()
    for name, p in sorted(projects.items()):
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(p, dict):
            raise ValueError("Invalid declarative project identity")
        if set(p) != {"name", "enable", "backend", "incusProject", "resources", "secrets", "backup", "claims", "source", "modules"}:
            raise ValueError("Unknown project inventory field")
        if p["name"] != name or p["source"] != name or p["backend"] not in ("incus-native", "incus-compose"):
            raise ValueError("Project inventory identity/backend mismatch")
        if type(p["enable"]) is not bool or not isinstance(p["incusProject"], str) or not NAME.fullmatch(p["incusProject"]):
            raise ValueError("Unsupported project state")
        res = p["resources"]
        if (not isinstance(res, dict) or set(res) != {"cpus", "memory"} or
            type(res["cpus"]) is not int or not 1 <= res["cpus"] <= 128 or
            not isinstance(res["memory"], str) or not MEMORY.fullmatch(res["memory"])):
            raise ValueError("Invalid bounded project resources")
        if (not isinstance(p["secrets"], dict) or set(p["secrets"]) != {"file"} or
            (p["secrets"]["file"] is not None and (
                not isinstance(p["secrets"]["file"], str) or
                any(part in ("", ".", "..") for part in p["secrets"]["file"].split("/"))))):
            raise ValueError("Invalid encrypted secret reference")
        if (not isinstance(p["backup"], dict) or set(p["backup"]) != {"enable", "consistency"} or
            type(p["backup"]["enable"]) is not bool or p["backup"]["consistency"] not in ("stop", "quiesce", "none")):
            raise ValueError("Invalid project backup policy")
        if (not isinstance(p["modules"], list) or
            any(not isinstance(m, str) or ".." in m.split("/") for m in p["modules"])):
            raise ValueError("Invalid project source module identity")
        if not isinstance(p["claims"], list) or len(p["claims"]) > 64:
            raise ValueError("Invalid project claim count")
        for claim in p["claims"]:
            if not isinstance(claim, str) or not CLAIM.fullmatch(claim):
                raise ValueError("Unsupported resource claim")
            identity = p["incusProject"] + "/" + claim
            if identity in claimed:
                raise ValueError("Overlapping Incus ownership claims")
            claimed.add(identity)
        results[name] = p
    if len({p["incusProject"] for p in results.values()}) != len(results):
        raise ValueError("Multiple projects claim a single Incus project")
    return {"schemaVersion": 1, "projects": results}


def _native_spec(project, name, current):
    if current.get("config", {}).get(reconcile.OWNER) != "true":
        raise ValueError("Refusing to adopt an Incus instance without NixLab ownership")
    if current.get("type") not in ("container", "virtual-machine"):
        raise ValueError("Unsupported existing Incus instance type")
    try:
        last = json.loads(current.get("config", {}).get(reconcile.LAST_APPLIED, "{}"))
    except (ValueError, TypeError):
        raise ValueError("Invalid previous Incus ownership metadata") from None
    if not isinstance(last, dict) or last.get("version") != 1:
        raise ValueError("Existing instance must have last-applied NixLab ownership")
    if not isinstance(last.get("config"), dict) or not isinstance(last.get("devices"), dict) or not isinstance(last.get("profiles"), list):
        raise ValueError("Incomplete Incus ownership record")
    config = dict(last["config"])
    config["limits.cpu"] = str(project["resources"]["cpus"])
    config["limits.memory"] = project["resources"]["memory"]
    return {"project": project["incusProject"], "name": name, "type": current["type"],
            "config": config, "devices": last["devices"], "profiles": last["profiles"]}


def _observation(project, guest):
    current, etag = reconcile.read_instance(project["incusProject"], guest)
    spec = _native_spec(project, guest, current)
    updated = reconcile.plan(current, spec)
    return {
        "project": project["incusProject"], "name": guest,
        "observedStatus": str(current.get("status") or "unknown")[:32],
        "etag": etag,
        "before": {"cpus": current.get("config", {}).get("limits.cpu"),
                   "memory": current.get("config", {}).get("limits.memory")},
        "after": {"cpus": str(project["resources"]["cpus"]),
                  "memory": project["resources"]["memory"]},
        "fingerprint": digest({
            k: current.get(k) for k in ("type", "config", "devices", "profiles")
        }),
        "action": "update" if updated is not None else "noop",
        "desiredHash": digest(updated if updated is not None else {
            k: current.get(k) for k in ("type", "config", "devices", "profiles")
        }),
    }


def _host_claims():
    """Never double-own instances controlled by the NixOS host reconciler."""
    try:
        if HOST_DESIRED.is_symlink():
            # /etc/NixOS symlinks into the immutable Nix store are expected.
            if not str(HOST_DESIRED.resolve()).startswith("/nix/store/"):
                raise ValueError("Unexpected host Incus inventory symlink")
        raw = json.loads(HOST_DESIRED.read_text())
    except (OSError, ValueError, TypeError):
        raise ValueError("Host-owned Incus inventory unavailable; refusing project adoption") from None
    if not isinstance(raw, list):
        raise ValueError("Invalid host-owned Incus inventory")
    owners = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("project"), str):
            raise ValueError("Invalid host-owned instance declaration")
        owners.add((item["project"], item["name"]))
    return owners


def plan(inventory):
    """Fail closed on resource types the #133 adapter cannot yet manage."""
    items = []
    host_owned = None
    for key, project in inventory["projects"].items():
        if not project["enable"]:
            continue
        for claim in sorted(project["claims"]):
            kind, name = CLAIM.fullmatch(claim).groups()
            if project["backend"] != "incus-native" or kind != "instance":
                raise ValueError("Unsupported Incus resource claim; #133 adapter required")
            if project["incusProject"] == "lab":
                raise ValueError("Legacy lab project is host-owned; explicit #133 migration required")
            if host_owned is None:
                host_owned = _host_claims()
            if (project["incusProject"], name) in host_owned:
                raise ValueError("Instance is already host-flake owned; explicit #133 migration required")
            items.append(_observation(project, name))
    return {"format": 1, "inventoryHash": digest(inventory), "resources": items,
            "actions": [{
                "project": item["project"], "name": item["name"],
                "action": item["action"], "before": item["before"],
                "after": item["after"],
                "potentialDisruption": item["action"] == "update" and item["observedStatus"] == "Running",
            } for item in items], "boundary": "Existing NixLab-owned native Incus instances only; volumes/VM images/Compose/data are never replaced"}


def recheck_and_apply(project, approved, *, verify_only=False):
    """Read the fresh ETag/ownership, then write only a reviewed native config."""
    name = approved["name"]
    actual = _observation(project, name)
    if actual != approved:
        if (verify_only and actual["action"] == "noop" and
            not (approved["observedStatus"] == "Running" and actual["observedStatus"] != "Running")):
            return {"status": "verified", "newEtag": actual["etag"], "health": actual["observedStatus"]}
        raise ValueError("Incus state changed since project review; re-review before deployment")
    if approved["action"] == "noop":
        return {"status": "verified", "newEtag": approved["etag"], "health": approved["observedStatus"]}
    current, etag = reconcile.read_instance(project["incusProject"], name)
    if etag != approved["etag"]:
        raise ValueError("Incus ETag drift during apply")
    spec = _native_spec(project, name, current)
    update = reconcile.plan(current, spec)
    if update is None or digest(update) != approved["desiredHash"]:
        raise ValueError("Incus update differs from approved plan")
    reconcile.write_instance(project["incusProject"], name, update, etag)
    verified = _observation(project, name)
    if verified["action"] != "noop":
        raise ValueError("Incus resource did not reach declared desired state")
    if approved["observedStatus"] == "Running" and verified["observedStatus"] != "Running":
        raise ValueError("Incus guest stopped during configuration update; manual health review required")
    return {"status": "verified", "newEtag": verified["etag"], "health": verified["observedStatus"]}
