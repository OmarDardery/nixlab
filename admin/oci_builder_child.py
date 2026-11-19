"""Non-root, no-network OCI build worker invoked *inside* a bubblewrap namespace.

This module is never the root broker. It sees only /context (read-only),
/work (mutable rootless Buildah storage), /out (private artifacts), and the
public Nix store. No Incus socket, host /etc, user home, host secrets,
Docker daemon, or network namespace reaches this process.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from oci_compose import validate_model

CONTEXT = Path("/context")
WORK = Path("/work")
OUTPUT = Path("/out")
MAX_CONFIG = 1024 * 1024
MAX_IMAGE = 1024 * 1024 * 1024
SAFE = re.compile(r"[a-z][a-z0-9_-]{0,62}\Z")


def _read(path, limit=MAX_CONFIG):
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("OCI build intent exceeds bounded source quota")
    return json.loads(raw)


def _exec(command, *, timeout, cwd=None):
    # Never echo untrusted subprocess stdout/stderr to a privileged journal.
    result = subprocess.run(command, capture_output=True, timeout=timeout, cwd=cwd,
                            env={"PATH": os.environ["NIXLAB_OCI_PATH"],
                                 "HOME": "/work/home", "TMPDIR": "/work/tmp",
                                 "XDG_RUNTIME_DIR": "/work/run",
                                 "XDG_CONFIG_HOME": "/work/config",
                                 "CONTAINERS_STORAGE_CONF": "/work/storage.conf",
                                 "BUILDAH_ISOLATION": "chroot"})
    if result.returncode:
        raise ValueError("Isolated OCI tool refused the pinned build or Compose configuration")
    if len(result.stdout) > MAX_CONFIG:
        raise ValueError("Compose validation result is too large")
    return result.stdout


def _compose_json(spec, *, extra=None):
    compose = os.environ["NIXLAB_OCI_COMPOSE_BIN"]
    original = spec["entrypoint"]
    if original is not None:
        flags = ["-f", "/context/" + original]
    else:
        flags = []
    if spec["generatedCompose"]:
        # This file is separate from the exact uploaded Dockerfile. Since it
        # lives in /work, explicitly pin its build context to /context instead
        # of accidentally selecting the worker's scratch directory.
        original_context = Path(spec["entrypoint"]).parent.as_posix()
        relative_context = "." if original_context == "." else "./" + original_context
        absolute_context = "/context" + ("" if original_context == "." else "/" + original_context)
        expected = "context: " + json.dumps(relative_context)
        if spec["generatedCompose"].count(expected) != 1:
            raise ValueError("Reviewed Dockerfile context differs from generated launch manifest")
        text = spec["generatedCompose"].replace(expected, "context: " + json.dumps(absolute_context))
        generated = WORK / "generated.yaml"
        generated.write_text(text)
        flags = ["-f", str(generated)]
    if extra:
        flags += ["-f", str(extra)]
    base = [compose, "-p", spec["project"]] + flags
    # Independent explicit syntax check; neither call starts Incus or builds.
    _exec(base + ["config", "--quiet"], timeout=30, cwd=CONTEXT)
    return _read_bytes_json(_exec(base + ["config", "--format", "json"], timeout=30,
                                  cwd=CONTEXT))


def _read_bytes_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Pinned Compose parser produced an invalid model") from None


def _model(spec):
    original = _compose_json(spec)
    checked = validate_model(original, project=spec["project"], revision=spec["revision"],
                             compose_path=spec["entrypoint"] or ".",
                             files=spec["sourceFiles"])
    if checked["separateOverride"]:
        # Reject a change in non-build model scope or a builder falling back
        # to original Dockerfile after overlay. Image name must be exact.
        extra = WORK / "generated-override.yaml"
        extra.write_text(checked["separateOverride"])
        patched = _compose_json(spec, extra=extra)
        services = patched.get("services", {})
        for item in checked["services"]:
            after = services.get(item["service"], {})
            if item["built"] and (after.get("image") != item["image"]
                                  or after.get("build") is not None):
                raise ValueError("Compose override did not disable implicit builds")
    return checked


def inspect(spec):
    plan = _model(spec)
    (OUTPUT / "intent.json").write_text(json.dumps(plan, sort_keys=True))
    return plan


def _digest(path):
    result = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            count += len(chunk)
            if count > MAX_IMAGE:
                raise ValueError("OCI archive exceeds per-service artifact quota")
            result.update(chunk)
    return {"sha256": result.hexdigest(), "bytes": count}


def build(spec):
    checked = _model(spec)
    if checked["intentHash"] != spec["intentHash"]:
        raise ValueError("Source-qualified Compose review differs inside the sandbox")
    tool = os.environ["NIXLAB_OCI_BUILDAH_BIN"]
    records = []
    for svc in checked["services"]:
        if not svc["built"]:
            continue
        name = svc["service"]
        if not SAFE.fullmatch(name):
            raise ValueError("Unsafe service identity")
        context = CONTEXT / svc["context"]
        dockerfile = CONTEXT / svc["dockerfile"]
        # No registry access at build time. 'FROM scratch' and previously
        # explicitly imported offline bases work. Missing bases fail closed.
        storage = [
            tool, "--root", "/work/storage-" + name,
            "--runroot", "/work/run-" + name,
            "--storage-driver", "vfs",
        ]
        _exec(storage + [
            "bud", "--isolation", "chroot", "--network", "none",
            "--pull", "never", "--layers=false",
            "-f", str(dockerfile), "--tag", svc["image"], str(context),
        ], timeout=1200, cwd=CONTEXT)
        archive = OUTPUT / (name + ".oci.tar")
        _exec(storage + ["push", "--format", "oci", svc["image"],
                         "oci-archive:" + str(archive) + ":" + svc["image"]],
              timeout=300, cwd=CONTEXT)
        record = _digest(archive)
        records.append({"service": name, "image": svc["image"], "artifact": archive.name,
                        "sha256": record["sha256"], "bytes": record["bytes"]})
    if not records:
        raise ValueError("No locally built services in the reviewed source")
    output = {"format": 1, "project": spec["project"], "revision": spec["revision"],
              "intentHash": checked["intentHash"], "artifacts": records}
    (OUTPUT / "build-result.json").write_text(json.dumps(output, sort_keys=True))
    return output


def main(argv):
    if len(argv) != 2 or argv[0] not in ("inspect", "build"):
        raise ValueError("Expected isolated OCI inspect or build operation")
    spec = _read(Path(argv[1]))
    if (spec.get("format") != 1 or not isinstance(spec.get("project"), str)
            or not SAFE.fullmatch(spec["project"]) or
            not re.fullmatch(r"[0-9a-f]{64}", spec.get("revision", "")) or
            not isinstance(spec.get("sourceFiles"), list)):
        raise ValueError("Invalid immutable OCI source intent")
    return inspect(spec) if argv[0] == "inspect" else build(spec)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        # Do not expose potentially secret Dockerfile/Compose values in logs.
        print("Isolated OCI operation refused; inspect the reviewed source and policy",
              file=sys.stderr)
        sys.exit(1)
