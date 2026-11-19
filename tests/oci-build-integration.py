"""Disposable NixOS VM: real unprivileged systemd+bwrap+Buildah, no Incus access."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

os.environ["NIXLAB_STATE"] = "/var/lib/nixlab-oci-vm-state"
os.environ["NIXLAB_PROJECT_SOURCES"] = "/srv/nixlab/project-sources"
os.environ["NIXLAB_OCI_ROOT"] = "/var/lib/nixlab-oci"

import native_imports
import runtime as rt
import oci_builds

Path(os.environ["NIXLAB_STATE"]).mkdir(mode=0o700, exist_ok=True)
stage = native_imports.ImportStaging()
published = native_imports.PublishedSources()
engine = oci_builds.OCIBuilds()
files = {
    "Dockerfile": b"FROM scratch\nCOPY payload.txt /payload.txt\n",
    "payload.txt": b"guarded-oci-build\n",
}
entries = [{"path": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
           for name, value in files.items()]
prepared = stage.prepare("root-test", "testapp", entries)
for name, data in files.items():
    stage.chunk(prepared["id"], "root-test", name, 0, base64.b64encode(data).decode())
sealed = stage.finish(prepared["id"], "root-test", prepared["manifestHash"])
approval = published.review(stage, prepared["id"], "root-test", sealed["manifestHash"])
current = published.publish(stage, prepared["id"], "root-test", approval["reviewId"], acknowledge=True)
source = published.root / "testapp" / "revisions" / current["revision"] / "source"
assert (source / "Dockerfile").read_bytes() == files["Dockerfile"]

# The real pinned incus-compose parser runs under systemd-run as nixlab-oci,
# with bubblewrap --unshare-all, no host network and no Incus socket.
review = engine.review("testapp", current["revision"], principal="vm-test")
assert review["status"] == "ready", review
assert review["services"][0]["built"] is True, review
assert not list((oci_builds.ROOT / "jobs").iterdir()), "Isolated snapshot cleaned after review"

# Mock only root dispatcher, not the unprivileged builder: it runs for real.
with patch.object(rt, "pin_worker_runtime"), patch.object(
        oci_builds.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
    queued = engine.queue(review["id"], review["planHash"])
assert queued["status"] == "queued"
completed = engine.run(review["id"])
assert completed["status"] == "verified-artifact", completed
assert completed["artifacts"][0]["sha256"]
assert completed["artifacts"][0]["bytes"] > 0
assert (source / "Dockerfile").read_bytes() == files["Dockerfile"]
assert (source / "payload.txt").read_bytes() == files["payload.txt"]
assert not list((oci_builds.ROOT / "jobs").iterdir()), "Builder work removed"
assert not Path("/var/lib/incus/unix.socket").exists(), "No Incus daemon in OCI builder fixture"
print("OCI-ISOLATED-BUILDAH-VM-PASSED")
