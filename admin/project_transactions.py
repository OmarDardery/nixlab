"""Reviewed project SOURCE publication. Never mutates Incus or the NixOS host.

This intentionally separates an approved source revision from a deployed
workload: the #133 Incus adapter will consume a separate exact-revision plan.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time

import runtime as rt
import project_plans
from project_workspace import validate_workspace, WorkspaceError

WORKSPACE = Path(os.environ.get("NIXLAB_PROJECTS", "/srv/nixlab/projects"))
SOURCE_LIMIT = 128 * 1024
JOB_LIFETIME = 15 * 60
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
FRAGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _regular_directory(path):
    path = Path(path)
    mode = path.lstat()
    if not stat.S_ISDIR(mode.st_mode) or stat.S_ISLNK(mode.st_mode):
        raise ValueError("Project source path must be a real directory")
    return path


def _workspace(root):
    root = Path(root).absolute()
    parent = _regular_directory(root.parent)
    if (parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o022):
        raise ValueError("Project workspace parent must be owned and not writable by other users")
    directory = _regular_directory(root)
    if (directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o022):
        raise ValueError("Project workspace must be controlled by the NixLab broker")
    return directory


def _patches(changes):
    if not isinstance(changes, dict) or not 1 <= len(changes) <= 16:
        raise ValueError("Supply one to sixteen project Nix file edits")
    total = 0
    for name, content in changes.items():
        if not isinstance(name, str) or len(name) > 255 or "\x00" in name:
            raise ValueError("Invalid project file path")
        if name != "backups.nix":
            parts = name.split("/")
            if not (len(parts) >= 2 and NAME.fullmatch(parts[0]) and
                    (parts[1:] == ["default.nix"] or
                     (len(parts) >= 3 and parts[1] == "modules" and
                      all(FRAGMENT.fullmatch(p) and not p.startswith("_") for p in parts[2:-1]) and
                      re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.nix", parts[-1])))):
                raise ValueError("Project edits must target default.nix or modules/**/*.nix")
        if content is None:
            if name == "backups.nix" or name.endswith("/default.nix"):
                raise ValueError("Cannot implicitly remove project entrypoints or backup policy")
        elif not isinstance(content, str) or "\x00" in content:
            raise ValueError("Project Nix edits must be UTF-8 text")
        else:
            total += len(content.encode("utf-8"))
            if len(content.encode("utf-8")) > 65536:
                raise ValueError("Individual Nix edit exceeds 64 KiB")
    if total > SOURCE_LIMIT:
        raise ValueError("Project Nix changes exceed 128 KiB")
    return changes


def _safe_bytes(root, name, expected):
    """Read file by anchored descriptors; never follow directory/file links."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        f = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            st = os.fstat(f)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != expected["size"]:
                raise ValueError("Project source changed during snapshot")
            data = bytearray()
            while len(data) <= st.st_size:
                block = os.read(f, min(65536, st.st_size + 1 - len(data)))
                if not block: break
                data.extend(block)
            result = bytes(data)
            if len(result) != st.st_size or hashlib.sha256(result).hexdigest() != expected["sha256"]:
                raise ValueError("Project source changed during snapshot")
            return result
        finally:
            os.close(f)
    finally:
        os.close(fd)


def _copy_verified(source, destination, manifest):
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    for relative, info in sorted(manifest["files"].items()):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        rt.atomic(target, _safe_bytes(source, relative, info))
    result = validate_workspace(destination)
    if result["sourceHash"] != manifest["sourceHash"]:
        raise ValueError("Project snapshot changed; retry the review")
    return result


def _parse_nix(paths):
    for path in paths:
        try:
            result = subprocess.run(["nix-instantiate", "--parse", str(path)],
                                    capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Nix parser unavailable or timed out") from None
        if result.returncode != 0:
            # Parser output may quote arbitrary source or credentials.
            raise ValueError("Project Nix syntax is invalid") from None


def _sync_parent(path):
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exchange(left, right):
    """Linux renameat2(RENAME_EXCHANGE): no window with a missing workspace."""
    libc = ctypes.CDLL(None, use_errno=True)
    method = getattr(libc, "renameat2", None)
    if method is None:
        raise ValueError("Atomic directory exchange unavailable on this host")
    method.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    method.restype = ctypes.c_int
    if method(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    _sync_parent(left)


def _freeze(directory):
    for root, folders, files in os.walk(directory):
        for name in folders:
            os.chmod(Path(root) / name, 0o500)
        for name in files:
            os.chmod(Path(root) / name, 0o400)
    os.chmod(directory, 0o500)


class ProjectTransactions:
    def __init__(self, workspace=None, state=None):
        self.workspace = Path(workspace or WORKSPACE).absolute()
        self.state = Path(state or rt.STATE)
        self.jobs = self.state / "project-transactions"
        self.jobs.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _job(self, identity):
        return rt.job_directory(self.jobs, identity, "project transaction")

    def _record(self, identity):
        return rt.read_job_record(self._job(identity) / "job.json")

    def _write(self, identity, record):
        rt.write_json(self._job(identity) / "job.json", record)

    def _root_state(self, deployment=None):
        project_active = rt.read_json(self.state / "project-deploy-active.json", {})
        if project_active.get("id") and project_active.get("id") != deployment:
            raise ValueError("Finish or recover the active project deployment")
        if rt.read_json(self.state / "pending.json"):
            raise ValueError("Finish the pending host activation before project changes")
        current = rt.read_json(self.state / "active.json", {})
        if current.get("id"):
            path = self.state / "jobs" / current["id"] / "job.json"
            record = rt.read_job_record(path)
            if record.get("status") in {"building", "ready", "applying", "awaiting-confirmation", "rolling-back"}:
                raise ValueError("Finish the active host transaction first")
        from deployments import check_idle
        check_idle(self.state, allow_project=deployment)

    def _active(self):
        active = rt.read_json(self.state / "project-active.json", {})
        identity = active.get("id")
        if identity:
            record = self._record(identity)
            if record["status"] in {"ready", "applying-source", "rolling-back", "recovery-required"}:
                raise ValueError("Finish or recover the active project review")
        return active

    def status(self, identity=None):
        active = rt.read_json(self.state / "project-active.json", {})
        identity = identity or active.get("id")
        if not identity:
            return {"active": None, "deployment": "not-implemented"}
        record = self._record(identity)
        deployment_path = self._job(identity) / "deployment.json"
        deployment = rt.read_json(deployment_path, {})
        if deployment.get("status") in ("queued", "applying") and (
            time.time() - deployment.get("updatedAt", deployment.get("createdAt", time.time())) > 30
        ):
            if not rt.worker_active("nixlab-project-deploy-" + identity):
                deployment.update(status="interrupted", errorCode="worker-interrupted",
                                  updatedAt=time.time())
                rt.write_json(deployment_path, deployment)
        return {key: record.get(key) for key in
                ("id", "status", "created", "expires", "baseHash", "candidateHash",
                 "planHash", "changes", "principal", "sourceOnly", "errorCode",
                 "inventoryHash", "runtimeActions")} | {
                     "deployment": deployment.get("status", "not-started"),
                     "deploymentResources": deployment.get("resources", {}),
                     "recoveryRequired": record["status"] in ("applying-source", "rolling-back", "recovery-required") or
                         deployment.get("status") in ("partial", "interrupted"),
                 }

    def review(self, changes, principal="root"):
        changes = _patches(changes)
        with rt.locked(blocking=False):
            self._root_state()
            self._active()
            live = _workspace(self.workspace)
            before = validate_workspace(live)
            identity = secrets.token_hex(16)
            directory = self.jobs / identity
            scratch = Path(tempfile.mkdtemp(prefix=".review-", dir=self.jobs))
            try:
                source = scratch / "source"
                _copy_verified(live, source, before)
                impact = []
                nix_files = []
                for relative, content in sorted(changes.items()):
                    path = source / relative
                    previous = before["files"].get(relative)
                    if content is None:
                        if previous is None:
                            raise ValueError("Cannot remove an unknown module")
                        path.unlink()
                        op = "remove"
                    else:
                        rt.atomic(path, content, mode=0o600)
                        nix_files.append(path)
                        op = "update" if previous else "create"
                    impact.append({"path": relative, "operation": op})
                _parse_nix(nix_files)
                after = validate_workspace(source)
                inventory = project_plans.evaluate(source, identity)
                reviewed_runtime = project_plans.plan(inventory)
                if before["sourceHash"] == after["sourceHash"]:
                    raise ValueError("Project source has no changes to publish")
                # A changed source outside the approved paths is never promoted.
                if validate_workspace(live)["sourceHash"] != before["sourceHash"]:
                    raise ValueError("Project source changed while being reviewed")
                record = {
                    "id": identity, "status": "ready", "created": time.time(),
                    "expires": time.time() + JOB_LIFETIME, "principal": principal,
                    "baseHash": before["sourceHash"], "candidateHash": after["sourceHash"],
                    "changes": impact, "sourceOnly": True,
                    "inventoryHash": reviewed_runtime["inventoryHash"],
                    "runtimeActions": reviewed_runtime["actions"],
                }
                record["planHash"] = _digest({
                    "id": identity, "base": record["baseHash"],
                    "candidate": record["candidateHash"], "changes": impact,
                    "inventoryHash": reviewed_runtime["inventoryHash"],
                    "runtimePlan": reviewed_runtime,
                    "sourceOnly": True,
                })
                rt.write_json(scratch / "inventory.json", inventory)
                rt.write_json(scratch / "runtime-plan.json", reviewed_runtime)
                rt.write_json(scratch / "job.json", record)
                _freeze(source)
                os.replace(scratch, directory)
                rt.write_json(self.state / "project-active.json", {"id": identity})
                rt.audit("project-source-reviewed", identity=identity, paths=len(impact), principal=principal)
                return self.status(identity)
            finally:
                if scratch.exists():
                    shutil.rmtree(scratch, ignore_errors=True)

    def _paths(self, identity):
        prefix = self.workspace.parent
        return (prefix / (".nixlab-project-staged-" + identity),
                prefix / (".nixlab-project-before-" + identity),
                prefix / (".nixlab-project-reverted-" + identity))

    def publish(self, identity, plan_hash):
        with rt.locked(blocking=False):
            self._root_state()
            record = self._record(identity)
            if rt.read_json(self.state / "project-active.json", {}).get("id") != identity or record["status"] != "ready":
                raise ValueError("Project review is no longer active")
            if not isinstance(plan_hash, str) or not secrets.compare_digest(plan_hash, record["planHash"]):
                raise ValueError("The exact reviewed project plan must be approved")
            if time.time() >= record["expires"]:
                raise ValueError("Project review expired; submit a new review")
            live = _workspace(self.workspace)
            before = validate_workspace(live)
            candidate = validate_workspace(self._job(identity) / "source")
            if before["sourceHash"] != record["baseHash"] or candidate["sourceHash"] != record["candidateHash"]:
                raise ValueError("Project source or reviewed candidate changed; re-review required")
            # Recheck both the typed inventory and live Incus ETags under the
            # mutation lock. No guest is touched by source publication.
            runtime_inventory = project_plans.evaluate(self._job(identity) / "source", identity)
            planned_inventory = rt.read_json(self._job(identity) / "inventory.json")
            if project_plans.digest(runtime_inventory) != project_plans.digest(planned_inventory):
                raise ValueError("Reviewed typed project inventory changed")
            current_plan = project_plans.plan(runtime_inventory)
            if current_plan != rt.read_json(self._job(identity) / "runtime-plan.json"):
                raise ValueError("Incus observation changed since review; prepare a new project plan")
            staged, previous, reverted = self._paths(identity)
            if any(path.exists() or path.is_symlink() for path in (staged, previous, reverted)):
                raise ValueError("Project recovery path already exists; inspect it locally")
            _copy_verified(self._job(identity) / "source", staged, candidate)
            record.update(status="applying-source", phase="prepared")
            self._write(identity, record)
            # Atomic directory exchange. At every instant /projects exists:
            # before swap it is the base tree; afterward it is the candidate.
            # On crash the base source is still at 'staged' or 'previous'.
            _exchange(live, staged)
            record["phase"] = "exchanged"
            self._write(identity, record)
            os.rename(staged, previous)
            _sync_parent(previous)
            record.update(status="published-source", phase="published", publishedAt=time.time())
            self._write(identity, record)
            rt.write_json(self.state / "project-last.json", {"id": identity})
            rt.audit("project-source-published", identity=identity, principal=record["principal"])
            return self.status(identity)

    def recover(self, identity):
        with rt.locked(blocking=False):
            record = self._record(identity)
            if rt.read_json(self.state / "project-active.json", {}).get("id") != identity:
                raise ValueError("Recover the current project transaction only")
            if record["status"] not in ("applying-source", "rolling-back", "recovery-required"):
                raise ValueError("Project transaction is not interrupted")
            staged, previous, reverted = self._paths(identity)
            live = self.workspace
            def matches(path, expected):
                if not path.exists() or path.is_symlink():
                    return False
                try: return validate_workspace(path)["sourceHash"] == expected
                except (OSError, ValueError, WorkspaceError): return False
            if record["status"] == "rolling-back":
                if matches(live, record["baseHash"]) and matches(previous, record["candidateHash"]):
                    record["status"] = "rolled-back"
                    rt.durable_unlink(self.state / "project-last.json")
                elif matches(live, record["candidateHash"]) and matches(previous, record["baseHash"]):
                    # Crash before exchange; still running the approved source.
                    record["status"] = "published-source"
                else:
                    record["status"] = "recovery-required"
            elif matches(live, record["candidateHash"]) and matches(previous, record["baseHash"]):
                record["status"] = "published-source"
                rt.write_json(self.state / "project-last.json", {"id": identity})
            elif matches(live, record["candidateHash"]) and matches(staged, record["baseHash"]):
                # Atomic exchange completed, but archive/journal update did not.
                os.rename(staged, previous)
                _sync_parent(previous)
                record["status"] = "published-source"
                rt.write_json(self.state / "project-last.json", {"id": identity})
            elif matches(live, record["baseHash"]) and matches(staged, record["candidateHash"]):
                # Crash before swap; no published changes.
                record["status"] = "recovered"
            else:
                record["status"] = "recovery-required"
            record["phase"] = "reconciled"
            self._write(identity, record)
            if record["status"] == "recovery-required":
                raise ValueError("Project publication requires manual recovery; files were retained")
            rt.audit("project-source-recovered", identity=identity, outcome=record["status"])
            return self.status(identity)

    def rollback(self, identity, plan_hash):
        with rt.locked(blocking=False):
            self._root_state()
            record = self._record(identity)
            if record["status"] != "published-source" or rt.read_json(self.state / "project-last.json", {}).get("id") != identity:
                raise ValueError("Only the last published source revision can be rolled back")
            deployment = rt.read_json(self._job(identity) / "deployment.json", {})
            if deployment.get("status") not in (None, "not-started", "verified"):
                raise ValueError("Recover the partial Incus deployment before changing project source")
            if deployment.get("status") == "verified" and record.get("runtimeActions") and any(
                item.get("action") != "noop" for item in record["runtimeActions"]
            ):
                raise ValueError("Source-only rollback would leave applied Incus settings inconsistent; review a new forward change")
            active = rt.read_json(self.state / "project-active.json", {})
            if active.get("id") not in (None, identity):
                other = self._record(active["id"])
                if other["status"] in {"ready", "applying-source", "rolling-back", "recovery-required"}:
                    raise ValueError("Finish the pending project review before rolling back older source")
            if not isinstance(plan_hash, str) or not secrets.compare_digest(plan_hash, record["planHash"]):
                raise ValueError("The reviewed project plan must be confirmed for rollback")
            live = _workspace(self.workspace)
            staged, previous, reverted = self._paths(identity)
            if reverted.exists() or reverted.is_symlink() or staged.exists():
                raise ValueError("Unexpected project recovery path")
            if (validate_workspace(live)["sourceHash"] != record["candidateHash"] or
                not previous.exists() or validate_workspace(previous)["sourceHash"] != record["baseHash"]):
                raise ValueError("Project source drift; refuse blind rollback")
            record["status"] = "rolling-back"
            self._write(identity, record)
            _exchange(live, previous)
            record.update(status="rolled-back", phase="rolled-back")
            self._write(identity, record)
            rt.durable_unlink(self.state / "project-last.json")
            rt.audit("project-source-rolled-back", identity=identity)
            return self.status(identity)

    def deploy(self, identity, plan_hash, *, resume=False):
        """Queue a project-only, exact-Etag deployment in the existing broker.

        This worker currently supports *only* reviewed in-place limits for
        already-owned native Incus instances. Other backends are refused.
        """
        with rt.locked(blocking=False):
            from recovery_guard import require_normal
            require_normal()
            self._root_state(deployment=identity)
            job = self._record(identity)
            if job["status"] != "published-source" or rt.read_json(self.state / "project-last.json", {}).get("id") != identity:
                raise ValueError("Publish the exact project source revision before deployment")
            if not isinstance(plan_hash, str) or not secrets.compare_digest(plan_hash, job["planHash"]):
                raise ValueError("The exact reviewed deployment plan must be approved")
            if validate_workspace(_workspace(self.workspace))["sourceHash"] != job["candidateHash"]:
                raise ValueError("Published project source drifted before deployment")
            directory = self._job(identity)
            plan = rt.read_json(directory / "runtime-plan.json")
            inventory = rt.read_json(directory / "inventory.json")
            if project_plans.digest(inventory) != plan["inventoryHash"]:
                raise ValueError("Reviewed inventory is corrupt")
            existing = rt.read_json(directory / "deployment.json", {})
            if existing and not resume:
                if existing.get("status") == "verified":
                    return self.status(identity)
                raise ValueError("Resume the current deployment journal; never restart blindly")
            if resume and existing.get("status") not in ("partial", "interrupted"):
                raise ValueError("No partial deployment to recover")
            if not existing:
                # All ETags checked before the worker is launched. The worker
                # checks them again immediately before each individual write.
                if project_plans.plan(inventory) != plan:
                    raise ValueError("Incus state differs from approved deployment")
                existing = {
                    "format": 1, "status": "queued", "createdAt": time.time(),
                    "planHash": job["planHash"], "sourceHash": job["candidateHash"],
                    "resources": {r["project"] + "/" + r["name"]: {
                        "phase": "pending", "action": r["action"]
                    } for r in plan["resources"]},
                }
            else:
                existing["status"] = "queued"
            existing["updatedAt"] = time.time()
            rt.write_json(directory / "deployment.json", existing)
            rt.write_json(self.state / "project-deploy-active.json", {"id": identity})
            rt.audit("project-deploy-requested", identity=identity, resume=resume, principal=job["principal"])
            try:
                rt.pin_worker_runtime(directory)
                subprocess.run([
                    "systemd-run", "--quiet", "--collect", "--no-block",
                    "--unit=nixlab-project-deploy-" + identity,
                    "--property=RuntimeMaxSec=3600",
                    "--setenv=NIXLAB_STATE=" + str(self.state),
                    "--setenv=NIXLAB_PROJECTS=" + str(self.workspace),
                    "--setenv=PATH=" + os.environ.get("PATH", "/run/current-system/sw/bin"),
                    sys.executable, str(rt.SOURCE / "main.py"),
                    "project-deploy-job", identity
                ], capture_output=True, check=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                existing.update(status="interrupted", errorCode="worker-start-failed")
                rt.write_json(directory / "deployment.json", existing)
                raise ValueError("Project worker failed to start; use explicit resume") from None
            return self.status(identity)

    def run_deployment(self, identity):
        """Root-only async worker: updates one guarded resource at a time."""
        with rt.locked():
            from recovery_guard import require_normal
            require_normal()
            directory = self._job(identity)
            record = self._record(identity)
            journal = rt.read_json(directory / "deployment.json")
            plan = rt.read_json(directory / "runtime-plan.json")
            inventory = rt.read_json(directory / "inventory.json")
            if journal.get("status") not in ("queued", "partial", "interrupted") or (
                journal.get("planHash") != record.get("planHash") or
                journal.get("sourceHash") != record.get("candidateHash") or
                project_plans.digest(inventory) != plan.get("inventoryHash") or
                validate_workspace(self.workspace)["sourceHash"] != record["candidateHash"]
            ):
                raise ValueError("Project deployment journal or published source differs")
            journal["status"] = "applying"
            rt.write_json(directory / "deployment.json", journal)
            try:
                for resource in plan["resources"]:
                    key = resource["project"] + "/" + resource["name"]
                    phase = journal["resources"][key]["phase"]
                    if phase == "verified":
                        # Previous successes can be trusted only while the
                        # resource remains in declared desired state.
                        project = next(p for p in inventory["projects"].values()
                                       if p["incusProject"] == resource["project"])
                        current = project_plans._observation(project, resource["name"])
                        if (current["action"] != "noop" or
                                current["etag"] != journal["resources"][key].get("newEtag")):
                            raise ValueError("Previously verified Incus resource drifted; re-review")
                        continue
                    project = next(p for p in inventory["projects"].values()
                                   if p["incusProject"] == resource["project"])
                    journal["resources"][key]["phase"] = "applying"
                    rt.write_json(directory / "deployment.json", journal)
                    result = project_plans.recheck_and_apply(
                        project, resource, verify_only=phase == "applying")
                    journal["resources"][key].update(phase="verified",
                                                     newEtag=result["newEtag"])
                    journal["updatedAt"] = time.time()
                    rt.write_json(directory / "deployment.json", journal)
                journal["status"] = "verified"
                journal["completedAt"] = time.time()
            except Exception:
                # A write may have succeeded before a power loss or API error.
                # Never reissue it blindly; resume verifies actual desired
                # state and re-reviews any inconclusive outcome.
                journal["status"] = "partial"
                journal["errorCode"] = "resource-review-required"
                rt.write_json(directory / "deployment.json", journal)
                rt.audit("project-deploy-partial", identity=identity)
                return self.status(identity)
            rt.write_json(directory / "deployment.json", journal)
            rt.durable_unlink(self.state / "project-deploy-active.json")
            rt.audit("project-deploy-verified", identity=identity,
                     resources=len(plan["resources"]))
            return self.status(identity)


    def cancel(self, identity):
        with rt.locked(blocking=False):
            record = self._record(identity)
            if record["status"] != "ready":
                raise ValueError("Only an unapplied project source review can be cancelled")
            record["status"] = "cancelled"
            self._write(identity, record)
            rt.audit("project-source-cancelled", identity=identity)
            return self.status(identity)
