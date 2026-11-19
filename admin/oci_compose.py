"""Fail-closed Compose ownership and immutable build-intent validation.

The original YAML is never rewritten. Only a separate generated override is
permitted, and it is accepted only after incus-compose parses both originals
and override under an offline, unprivileged, filesystem-isolated worker.
"""
import hashlib
import json
from pathlib import PurePosixPath
import re

NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SENSITIVE = re.compile(r"(?:PASSWORD|PASSWD|TOKEN|SECRET|PRIVATE|AUTH|KEY|CREDENTIAL)", re.I)
BANNED_SERVICE = {
    "privileged", "cap_add", "devices", "device_cgroup_rules",
    "security_opt", "userns_mode", "pid", "ipc", "uts",
    "cgroup", "cgroup_parent", "cgroupns_mode", "network_mode",
    "volumes_from", "extends", "develop", "secrets", "configs",
    "env_file", "x-incus", "x-incus-compose",
}
BANNED_TOP = {"secrets", "configs", "include", "x-incus", "x-incus-compose"}
MAX_SERVICES = 16


def _safe_relative(value, *, allow_dot=False):
    if not isinstance(value, str) or "\\" in value or value.startswith("/") or "\0" in value:
        raise ValueError("Unsupported or absolute Compose source path")
    parts = value.split("/")
    if any(x == ".." or not x for x in parts):
        raise ValueError("Compose context cannot escape its source directory")
    if any(x != "." and (x.startswith("-") or not SOURCE.fullmatch(x)) for x in parts):
        raise ValueError("Unsupported Compose source component")
    return "." if allow_dot and all(x == "." for x in parts) else "/".join(x for x in parts if x != ".")


def _source_path(base, path, *, allow_dot=False):
    # compose-go canonicalizes relative build paths to absolute paths.
    # Accept ONLY the isolated, pinned /context mount, never arbitrary host
    # paths, /work, /tmp, or a symlink into another bind mount.
    if isinstance(path, str) and (path == "/context" or path.startswith("/context/")):
        relative = path[len("/context"):].lstrip("/")
        return _safe_relative(relative, allow_dot=allow_dot) if relative else "."
    parts = list(PurePosixPath(base).parts) if base != "." else []
    leaf = _safe_relative(path, allow_dot=allow_dot)
    if leaf != ".":
        parts += leaf.split("/")
    return "/".join(parts) or "."


def _volumes(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Invalid Compose mounts")
    names = []
    for item in value:
        if isinstance(item, str):
            source = item.split(":", 1)[0]
            if ":" not in item or source.startswith(("/", ".", "~")) or "/" in source or source == "":
                raise ValueError("Compose bind or anonymous mounts require explicit separate review")
            if not NAME.fullmatch(source):
                raise ValueError("Invalid named volume")
            names.append(source)
        elif isinstance(item, dict):
            if set(item) - {"type", "source", "target", "read_only", "volume"}:
                raise ValueError("Unsupported Compose volume attributes")
            if item.get("type") != "volume" or not isinstance(item.get("source"), str) or not NAME.fullmatch(item["source"]):
                raise ValueError("Bind/anonymous mounts are forbidden in this deployment adapter")
            names.append(item["source"])
        else:
            raise ValueError("Invalid Compose volume")
    return names


def _environments(environment):
    if environment is None:
        return
    if not isinstance(environment, dict):
        raise ValueError("Compose environment must be explicit named entries")
    if len(environment) > 64:
        raise ValueError("Compose environment exceeds review bounds")
    for key, value in environment.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key):
            raise ValueError("Invalid environment variable name")
        if SENSITIVE.search(key):
            raise ValueError("Secret-like environment variable requires scoped #134 SOPS delivery")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("Unsupported Compose environment value")


def validate_model(model, *, project, revision, compose_path, files):
    """Validate the normalized incus-compose JSON model before any side effect.

    A valid model does NOT mean that the proposed deployment is automatically
    authorized. This is an allowlist of nonsecret, nonprivileged Compose shape.
    """
    if not isinstance(project, str) or not NAME.fullmatch(project):
        raise ValueError("Invalid project")
    if not isinstance(revision, str) or not DIGEST.fullmatch(revision):
        raise ValueError("Invalid source revision")
    if not isinstance(model, dict) or BANNED_TOP.intersection(model):
        raise ValueError("Compose demands unsupported privileged or secret resources")
    # No external networks, top-level volume driver/host path or arbitrary
    # Compose extensions may be sent to the privileged Incus adapter.
    volumes = model.get("volumes") or {}
    if not isinstance(volumes, dict) or any(
        not isinstance(name, str) or not NAME.fullmatch(name) or
        props not in ({}, None)
        for name, props in volumes.items()
    ):
        raise ValueError("Custom storage drivers, external volumes and unsafe volume options are forbidden")
    networks = model.get("networks") or {}
    if not isinstance(networks, dict) or any(
        not isinstance(name, str) or not NAME.fullmatch(name) or
        props not in ({}, None)
        for name, props in networks.items()
    ):
        raise ValueError("External or customized Compose networks require #133")
    services = model.get("services")
    if not isinstance(services, dict) or not 1 <= len(services) <= MAX_SERVICES:
        raise ValueError("Compose requires 1–16 services")
    known = {item["path"] for item in files}
    base = str(PurePosixPath(compose_path).parent)
    preview = []
    for service, data in sorted(services.items()):
        if not NAME.fullmatch(service) or not isinstance(data, dict):
            raise ValueError("Invalid Compose service")
        if set(data) & BANNED_SERVICE or any(k.startswith("x-") for k in data):
            raise ValueError("Compose service requires forbidden host privileges or runtime secrets")
        _environments(data.get("environment"))
        ports = data.get("ports") or []
        if (not isinstance(ports, list) or len(ports) > 32 or
                any(not isinstance(port, (str, int, dict)) for port in ports)):
            raise ValueError("Invalid Compose host port exposure")
        named = _volumes(data.get("volumes"))
        if any(not isinstance(v, str) or not NAME.fullmatch(v) for v in named):
            raise ValueError("Unsupported named storage volume")
        build = data.get("build")
        if isinstance(build, str):
            context, dockerfile = build, "Dockerfile"
        elif isinstance(build, dict):
            if set(build) - {"context", "dockerfile", "args", "target", "no_cache", "pull", "labels", "platforms"}:
                raise ValueError("Unsupported build options; source retained unchanged")
            if build.get("args") or build.get("target") or build.get("platforms") or build.get("pull"):
                raise ValueError("Build args, network/pull, target and platforms require explicit build policy")
            context, dockerfile = build.get("context", "."), build.get("dockerfile", "Dockerfile")
        elif build is None:
            context = dockerfile = None
        else:
            raise ValueError("Invalid Compose build definition")
        if context is not None:
            context_path = _source_path(base, context, allow_dot=True)
            dockerfile_path = _source_path(context_path, dockerfile)
            if dockerfile_path not in known:
                raise ValueError("Compose Dockerfile is missing from the exact published context")
            image = f"localhost/nixlab-{project[:24]}-{service[:24]}:{revision[:32]}"
        else:
            image = data.get("image")
            if not isinstance(image, str) or "@sha256:" not in image:
                raise ValueError("Non-built services must use explicit immutable OCI image digests")
            context_path = dockerfile_path = None
        preview.append({
            "service": service, "image": image, "built": context is not None,
            "context": context_path, "dockerfile": dockerfile_path,
            "namedVolumes": named, "ports": data.get("ports") or [],
        })
    # A YAML override only changes image identities and suppresses the
    # original build: field. It does not rewrite the user's Compose bytes.
    lines = ["services:"]
    for svc in preview:
        if svc["built"]:
            lines.extend(["  " + svc["service"] + ":",
                          "    image: " + json.dumps(svc["image"]),
                          "    build: null"])
    override = "\n".join(lines) + "\n" if len(lines) > 1 else ""
    identity = hashlib.sha256(json.dumps(
        {"project": project, "revision": revision, "services": preview},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"format": 1, "project": project, "revision": revision,
            "services": preview, "separateOverride": override,
            "sourceHash": revision, "intentHash": identity,
            "requiresBuilder": any(svc["built"] for svc in preview),
            "wouldCreateOrRecreate": [svc["service"] for svc in preview],
            "note": "Intent only. Apply requires separate approval and exact Incus ownership checks."}
