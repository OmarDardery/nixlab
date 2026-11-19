"""Root broker for separately approved offline OCI builds.

Never launches a Dockerfile as root. Every parser/build command runs in
bubblewrap under a dedicated unprivileged system account with an empty network
namespace and no Incus socket. OCI artifacts are rehashed and validated before
a future, independent Incus import/deploy approval can consume them.
"""
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import runtime as rt
from locking import open_lock
from native_imports import PublishedSources, _verified_source, check_files
from oci_source_plan import source_plan
from oci_artifacts import verify_archive

ID = re.compile(r"[0-9a-f]{32}\Z")
ROOT = Path(os.environ.get("NIXLAB_OCI_ROOT", "/var/lib/nixlab-oci"))
REVIEW_LIFETIME = 15 * 60
BUILDER_USER = "nixlab-oci"
MAX_SERVICE_ARTIFACTS = 16
MAX_WORKER_RECORD = 1024 * 1024


def _worker_json(path):
    """Read untrusted worker output by O_NOFOLLOW and exact builder ownership.

    The worker can write the per-job out directory; it must never convince the
    privileged parent to follow a symlink or read an arbitrary host file.
    """
    uid, _ = _account()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or info.st_size > MAX_WORKER_RECORD):
            raise ValueError("Untrusted OCI worker record metadata")
        raw = os.read(descriptor, MAX_WORKER_RECORD + 1)
        if len(raw) != info.st_size:
            raise ValueError("Untrusted OCI worker output changed")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Untrusted OCI worker record is not an object")
        return value
    finally:
        os.close(descriptor)


def _digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _account():
    identity = pwd.getpwnam(BUILDER_USER)
    return identity.pw_uid, identity.pw_gid


def _owned(path, mode=0o700, *, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("Untrusted OCI broker directory")
    return path


def _chown_tree(path, uid, gid):
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories:
            item = Path(root) / name
            info = item.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("OCI work contains unsafe directory metadata")
            os.chown(item, uid, gid, follow_symlinks=False)
            os.chmod(item, 0o700)
        for name in files:
            item = Path(root) / name
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("OCI work contains nonregular source")
            os.chown(item, uid, gid, follow_symlinks=False)
            os.chmod(item, 0o400 if "context" in item.parts else 0o600)
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, 0o700)


def _store_ready():
    root = _owned(ROOT, mode=0o711, create=True)
    work = _owned(root / "jobs", mode=0o711, create=True)
    # Work roots are traversable but not listable by non-root; only a
    # mode-0700, individual job directory is owned by the builder account.
    for path in (root, work):
        if path.stat().st_mode & 0o077 not in (0o011, 0o000):
            raise ValueError("OCI state parent must not be group-writable or enumerable")
    return work


def _snapshot(project, revision, identity, publisher):
    plan = source_plan(project, revision, publisher)
    if plan["mode"] not in ("compose", "dockerfile") or (
            "unsupported-buildkit-feature-requires-explicit-policy" in plan["blocking"]):
        raise ValueError("OCI project source is ambiguous or uses unsupported build syntax")
    files = check_files(plan["sourceFiles"])
    uid, gid = _account()
    workspace = _store_ready()
    scratch = Path(tempfile.mkdtemp(prefix=".", dir=workspace))
    try:
        source = scratch / "context"
        source.mkdir(mode=0o700)
        _verified_source(publisher.root / project / "revisions" / revision / "source",
                         files, destination=source)
        for name in ("work", "out"):
            (scratch / name).mkdir(mode=0o700)
        work = scratch / "work"
        for name in ("home", "tmp", "run", "config"):
            (work / name).mkdir(mode=0o700)
        (work / "storage.conf").write_text(
            '[storage]\ndriver = "vfs"\nrunroot = "/work/runroot"\ngraphroot = "/work/graphroot"\n')
        etc = scratch / "etc"
        etc.mkdir(mode=0o700)
        (etc / "passwd").write_text("root:x:0:0:OCI builder:/work/home:/bin/sh\n")
        (etc / "group").write_text("root:x:0:\n")
        rt.atomic(work / "intent.json", json.dumps(plan, sort_keys=True))
        _chown_tree(scratch, uid, gid)
        # Atomic rename is in one filesystem. Existing work with this ID can
        # only come from an interrupted earlier operation and must be reviewed.
        target = workspace / identity
        if target.exists() or target.is_symlink():
            raise ValueError("OCI work already exists; inspect prior journal")
        os.rename(scratch, target)
        return target, plan
    finally:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)


def _sandbox_command(workspace, mode):
    """Use a namespace with no host root, host files, network or Incus socket."""
    bwrap = os.environ.get("NIXLAB_OCI_BWRAP_BIN")
    compose = os.environ.get("NIXLAB_OCI_COMPOSE_BIN")
    buildah = os.environ.get("NIXLAB_OCI_BUILDAH_BIN")
    # Python may have been launched via /run/current-system/sw/bin/python3;
    # its resolved executable is still a pinned, immutable Nix store path.
    pins = {"bwrap": bwrap, "compose": compose, "buildah": buildah,
            "python": os.path.realpath(sys.executable),
            "worker": os.path.realpath(rt.SOURCE / "oci_builder_child.py")}
    for label, path in pins.items():
        if not isinstance(path, str) or not path.startswith("/nix/store/"):
            raise ValueError("Pinned OCI build runtime is unavailable: " + label)
    work = workspace / "work"
    return [
        bwrap, "--unshare-all", "--die-with-parent", "--new-session",
        "--uid", "0", "--gid", "0",
        "--ro-bind", "/nix/store", "/nix/store",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/etc",
        "--ro-bind", str(workspace / "etc" / "passwd"), "/etc/passwd",
        "--ro-bind", str(workspace / "etc" / "group"), "/etc/group",
        "--dir", "/context", "--ro-bind", str(workspace / "context"), "/context",
        "--dir", "/work", "--bind", str(work), "/work",
        "--dir", "/out", "--bind", str(workspace / "out"), "/out",
        "--chdir", "/work", "--clearenv",
        "--setenv", "PATH", os.environ.get("NIXLAB_OCI_PATH", ""),
        "--setenv", "NIXLAB_OCI_PATH", os.environ.get("NIXLAB_OCI_PATH", ""),
        "--setenv", "NIXLAB_OCI_COMPOSE_BIN", compose,
        "--setenv", "NIXLAB_OCI_BUILDAH_BIN", buildah,
        *(["--setenv", "NIXLAB_OCI_VM_TEST", "1"] if os.environ.get("NIXLAB_OCI_VM_TEST") == "1" else []),
        pins["python"], pins["worker"], mode,
        "/work/intent.json",
    ]


def _run_sandbox(workspace, mode, identity):
    _account()
    cmd = _sandbox_command(workspace, mode)
    # The root broker only prepares immutable file descriptors and launches
    # transient systemd units. The entire untrusted parser/builder runs as
    # the dedicated UID, under a second bubblewrap isolation boundary.
    unit = "nixlab-oci-sandbox-" + identity
    sandbox = [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--unit=" + unit,
        "--property=User=" + BUILDER_USER,
        "--property=NoNewPrivileges=yes",
        "--property=PrivateNetwork=yes",
        "--property=IPAddressDeny=any",
        "--property=PrivateDevices=yes",
        "--property=ProtectHome=yes",
        "--property=ProtectSystem=strict",
        # Do not mask/overmount files inside the outer /proc: doing so makes
        # the unprivileged inner user namespace's fresh proc mount fail with
        # VFS "Mount too revealing". Inner bwrap installs its own fresh /proc
        # and the builder never sees a host /proc or real UID privileges.
        "--property=ProtectControlGroups=yes",
        "--property=RestrictSUIDSGID=yes",
        "--property=LockPersonality=yes",
        "--property=RestrictRealtime=yes",
        "--property=CPUQuota=200%",
        "--property=MemoryMax=2G",
        "--property=TasksMax=256",
        "--property=RuntimeMaxSec=" + ("120" if mode == "inspect" else "1800"),
        "--property=ReadWritePaths=" + str(workspace),
        "--property=InaccessiblePaths=-/var/lib/incus -/etc/nixos -/etc/ssh -/run/nixlab-secrets -/srv/nixlab -/var/lib/nixlab-admin -/var/lib/nixlab-secrets",
        "--property=WorkingDirectory=" + str(workspace),
        "--property=Environment=NIXLAB_OCI_JOB=" + identity,
    ] + cmd
    result = subprocess.run(sandbox, capture_output=True, timeout=130 if mode == "inspect" else 1830)
    if result.returncode:
        if os.environ.get("NIXLAB_OCI_VM_TEST") == "1":
            # VM fixture has no user-uploaded content or secrets. This switch
            # must never be enabled in the production root broker.
            print("OCI-VM-SANDBOX-STDERR:", result.stderr[-1400:].decode(errors="replace"),
                  file=sys.stderr)
        raise ValueError("Isolated OCI validation/build failed; source was not deployed")


class OCIBuilds:
    """Project-scoped, immutable OCI build job queue, entirely separate from Incus apply."""
    def __init__(self, state=None, publisher=None):
        self.state = Path(state or rt.STATE)
        self.jobs = self.state / "oci-builds"
        self.publisher = publisher or PublishedSources()
        self.jobs.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _job(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError("Invalid OCI review ID")
        path = self.jobs / identity
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Unknown OCI review ID")
        return path

    def _record(self, identity):
        return rt.read_job_record(self._job(identity) / "job.json")

    def _active(self, identity=None):
        current = rt.read_json(self.state / "oci-build-active.json", {})
        if current.get("id") and current["id"] != identity:
            record = self._record(current["id"])
            if record["status"] not in ("verified-artifact", "failed", "cancelled"):
                raise ValueError("Finish or recover the current OCI build")
        return current

    def status(self, identity=None):
        active = rt.read_json(self.state / "oci-build-last.json", {})
        identity = identity or active.get("id")
        if not identity:
            return {"id": None, "status": "not-started"}
        record = self._record(identity)
        if record.get("status") in ("queued", "building") and (
                time.time() - record.get("updatedAt", record["createdAt"]) > 45
        ):
            if not rt.worker_active("nixlab-oci-build-" + identity):
                record.update(status="interrupted", errorCode="worker-interrupted",
                              updatedAt=time.time())
                rt.write_json(self._job(identity) / "job.json", record)
        return {k: record.get(k) for k in (
            "id", "project", "revision", "status", "planHash", "createdAt",
            "expires", "intentHash", "services", "artifacts", "errorCode")}

    def review(self, project, revision, *, principal="root"):
        with rt.locked(blocking=False):
            self._active()
            identity = secrets.token_hex(16)
            job_dir = self.jobs / identity
            scratch = None
            try:
                scratch, plan = _snapshot(project, revision, identity, self.publisher)
                _run_sandbox(scratch, "inspect", identity)
                parsed = _worker_json(scratch / "out" / "intent.json")
                if (parsed.get("sourceHash") != revision or
                        parsed.get("project") != project or
                        parsed.get("format") != 1):
                    raise ValueError("Isolated Compose parser did not attest the reviewed source")
                # Source is reviewed again after parser inspection, not just
                # before untrusted YAML reaches the worker.
                self.publisher.attest(project, revision)
                record = {
                    "id": identity, "format": 1, "project": project,
                    "revision": revision, "status": "ready",
                    "createdAt": time.time(), "expires": time.time() + REVIEW_LIFETIME,
                    "principal": principal, "intentHash": parsed["intentHash"],
                    "services": parsed["services"],
                    "sourceHash": revision,
                }
                record["planHash"] = _digest({
                    "project": project, "revision": revision,
                    "intentHash": parsed["intentHash"], "services": parsed["services"]
                })
                job_dir.mkdir(mode=0o700)
                rt.write_json(job_dir / "intent.json", parsed)
                rt.write_json(job_dir / "job.json", record)
                rt.audit("oci-build-reviewed", project=project, identity=identity,
                         principal=principal, revision=revision)
                rt.write_json(self.state / "oci-build-last.json", {"id": identity})
                return self.status(identity)
            finally:
                if scratch is not None and scratch.exists():
                    shutil.rmtree(scratch)

    def queue(self, identity, plan_hash, *, resume=False):
        with rt.locked(blocking=False):
            from recovery_guard import require_normal
            require_normal()
            self._active(identity)
            record = self._record(identity)
            if (record.get("status") not in (("failed", "interrupted") if resume else ("ready",)) or
                    not isinstance(plan_hash, str) or not secrets.compare_digest(plan_hash, record["planHash"])):
                raise ValueError("OCI build requires an exact, fresh reviewed revision")
            if not resume and time.time() >= record["expires"]:
                raise ValueError("OCI build review expired; review the source again")
            self.publisher.attest(record["project"], record["revision"])
            record.update(status="queued", updatedAt=time.time())
            rt.write_json(self._job(identity) / "job.json", record)
            rt.write_json(self.state / "oci-build-active.json", {"id": identity})
            try:
                rt.pin_worker_runtime(self._job(identity))
                subprocess.run([
                    "systemd-run", "--quiet", "--collect", "--no-block",
                    "--unit=nixlab-oci-build-" + identity,
                    "--property=RuntimeMaxSec=2400",
                    "--setenv=NIXLAB_STATE=" + str(self.state),
                    "--setenv=NIXLAB_OCI_ROOT=" + str(ROOT),
                    "--setenv=NIXLAB_OCI_BWRAP_BIN=" + os.environ.get("NIXLAB_OCI_BWRAP_BIN", ""),
                    "--setenv=NIXLAB_OCI_COMPOSE_BIN=" + os.environ.get("NIXLAB_OCI_COMPOSE_BIN", ""),
                    "--setenv=NIXLAB_OCI_BUILDAH_BIN=" + os.environ.get("NIXLAB_OCI_BUILDAH_BIN", ""),
                    "--setenv=NIXLAB_OCI_PATH=" + os.environ.get("NIXLAB_OCI_PATH", ""),
                    "--setenv=PATH=" + os.environ.get("PATH", "/run/current-system/sw/bin"),
                    sys.executable, str(rt.SOURCE / "main.py"),
                    "oci-build-job", identity,
                ], capture_output=True, timeout=20, check=True)
            except (OSError, subprocess.SubprocessError):
                record.update(status="interrupted", errorCode="builder-launch-failed")
                rt.write_json(self._job(identity) / "job.json", record)
                raise ValueError("OCI build worker failed to start; use explicit retry") from None
            rt.audit("oci-build-queued", identity=identity, project=record["project"])
            return self.status(identity)

    def run(self, identity):
        """Root-only parent; all actual image-build instructions run as guest UID."""
        with rt.locked():
            record = self._record(identity)
            if record["status"] != "queued":
                raise ValueError("Unapproved OCI build job")
            record.update(status="building", updatedAt=time.time())
            rt.write_json(self._job(identity) / "job.json", record)
            scratch = None
            try:
                scratch, spec = _snapshot(record["project"], record["revision"],
                                          identity, self.publisher)
                spec["intentHash"] = record["intentHash"]
                rt.atomic(scratch / "work" / "intent.json", json.dumps(spec, sort_keys=True))
                uid, gid = _account()
                os.chown(scratch / "work" / "intent.json", uid, gid)
                os.chmod(scratch / "work" / "intent.json", 0o400)
                _run_sandbox(scratch, "build", identity)
                response = _worker_json(scratch / "out" / "build-result.json")
                if (response.get("format") != 1 or response.get("project") != record["project"] or
                        response.get("revision") != record["revision"] or
                        response.get("intentHash") != record["intentHash"] or
                        not isinstance(response.get("artifacts"), list) or
                        not 1 <= len(response["artifacts"]) <= MAX_SERVICE_ARTIFACTS):
                    raise ValueError("Builder returned an unapproved or invalid OCI artifact set")
                expected = {s["service"]: s["image"] for s in record["services"] if s["built"]}
                if {a.get("service") for a in response["artifacts"]} != set(expected):
                    raise ValueError("OCI build artifact set differs from approved services")
                destination = self._job(identity) / "artifacts"
                destination.mkdir(mode=0o700)
                completed = []
                for entry in response["artifacts"]:
                    name = entry["service"]
                    if (entry.get("image") != expected[name] or
                            entry.get("artifact") != name + ".oci.tar"):
                        raise ValueError("OCI artifact identity changed after review")
                    source = scratch / "out" / entry["artifact"]
                    metadata = verify_archive(source, entry.get("sha256"), entry.get("bytes"))
                    target = destination / entry["artifact"]
                    # Strictly replace the artifact owner after the verified
                    # worker terminates. Do not execute or extract OCI layers.
                    os.rename(source, target)
                    os.chown(target, os.geteuid(), os.getegid(), follow_symlinks=False)
                    os.chmod(target, 0o400)
                    completed.append({"service": name, "image": entry["image"],
                                      "sha256": metadata["sha256"],
                                      "bytes": metadata["bytes"],
                                      "ociManifestDigest": metadata["ociManifestDigest"]})
                self.publisher.attest(record["project"], record["revision"])
                record.update(status="verified-artifact", completedAt=time.time(),
                              artifacts=completed)
                rt.audit("oci-build-artifacts-verified", identity=identity, project=record["project"],
                         imageCount=len(completed))
            except Exception:
                record.update(status="failed", errorCode="build-or-artifact-verification-failed")
                rt.audit("oci-build-failed", identity=identity, project=record["project"])
            finally:
                if scratch is not None and scratch.exists():
                    shutil.rmtree(scratch)
                rt.write_json(self._job(identity) / "job.json", record)
                rt.durable_unlink(self.state / "oci-build-active.json")
            return self.status(identity)
