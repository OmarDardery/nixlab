"""Conservative secret-surface review for immutable Incus Compose sources.

This is a STATIC classification, never runtime approval. In incus-compose,
service environments can be persisted in instance config, while top-level
file secrets are pushed into the guest filesystem (not tmpfs mounts).
Only a pinned live Incus test can qualify a future delivery implementation.

No secret value, interpolated value, or raw Compose content is returned.
"""
from pathlib import PurePosixPath
import re

import yaml

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:[:?+\-][^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))")
CREDENTIAL = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential)")
MAX_COMPOSE = 2 * 1024 * 1024


def _check_relative(value):
    if not isinstance(value, str) or not value or len(value) > 255:
        return False
    if "\x00" in value or "\\" in value or value.startswith("/"):
        return False
    parts = value.removeprefix("./").split("/")
    return (1 <= len(parts) <= 12
            and all(part not in ("", ".", "..") and
                    re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
            and PurePosixPath(*parts).name.lower() not in (".env", "secrets.enc.yaml"))


def classify_compose_secrets(contents, bindings=None):
    """Classify service-level leakage and file-secret staging prerequisites.

    'qualified' is intentionally NEVER returned. Merely finding a matching
    file: definition does not prove that Incus avoids persisting a guest copy.
    """
    if not isinstance(contents, bytes) or len(contents) > MAX_COMPOSE:
        raise ValueError("Invalid or oversized Compose source")
    try:
        source = contents.decode("utf-8")
        document = yaml.safe_load(source)
    except (UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise ValueError("Compose secret preflight could not parse source") from None
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValueError("Compose source has no valid services")
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict) or len(bindings) > 128:
        raise ValueError("Invalid Compose secret bindings")

    declared = {}
    for alias, binding in bindings.items():
        if (not isinstance(alias, str) or not KEY.fullmatch(alias)
                or not isinstance(binding, dict)
                or not KEY.fullmatch(str(binding.get("key", "")))
                or not isinstance(binding.get("instance"), str)
                or not NAME.fullmatch(binding["instance"])
                or binding.get("backend") != "incus-file"):
            raise ValueError("Invalid Compose secret delivery binding")
        declared[alias] = binding

    reasons = set()
    routes = []
    top_level = document.get("secrets", {})
    if top_level is None:
        top_level = {}
    if not isinstance(top_level, dict):
        raise ValueError("Compose top-level secrets must be a mapping")

    sensitive_names = {name for alias, binding in declared.items()
                       for name in (alias, binding["key"])}
    for match in REFERENCE.finditer(source):
        if (match.group(1) or match.group(2)) in sensitive_names:
            reasons.add("secret-interpolation-may-persist-in-incus-config")

    consumed = set()
    used_paths = set()
    for service, spec in document["services"].items():
        if not isinstance(service, str) or not NAME.fullmatch(service) or not isinstance(spec, dict):
            raise ValueError("Invalid Compose service identity")
        if spec.get("env_file") is not None:
            reasons.add("compose-env-file-may-persist-in-incus-config")
        env = spec.get("environment", {})
        if env is not None and not isinstance(env, (dict, list)):
            raise ValueError("Invalid Compose environment")
        entries = env.items() if isinstance(env, dict) else (
            (part.split("=", 1)[0], part.split("=", 1)[1] if "=" in part else None)
            for part in env)
        for key, value in entries:
            if not isinstance(key, str):
                raise ValueError("Invalid Compose environment key")
            if CREDENTIAL.search(key) or key in sensitive_names:
                reasons.add("compose-secret-environment-may-persist-in-incus-config")
            if isinstance(value, str) and any(
                    (m.group(1) or m.group(2)) in sensitive_names for m in REFERENCE.finditer(value)):
                reasons.add("secret-interpolation-may-persist-in-incus-config")
        grants = spec.get("secrets", [])
        if grants is None:
            grants = []
        if not isinstance(grants, list):
            raise ValueError("Compose service secrets must be a list")
        for grant in grants:
            if isinstance(grant, str):
                alias = grant
            elif isinstance(grant, dict) and isinstance(grant.get("source"), str):
                alias = grant["source"]
            else:
                raise ValueError("Invalid Compose service secret grant")
            if alias not in declared or declared[alias]["instance"] != service:
                reasons.add("compose-secret-grant-not-bound-to-service")
                continue
            consumed.add(alias)
            definition = top_level.get(alias)
            if not isinstance(definition, dict) or set(definition) != {"file"}:
                reasons.add("compose-secret-not-relative-file")
                continue
            relative = definition["file"]
            if not _check_relative(relative):
                reasons.add("compose-secret-file-path-unsafe")
                continue
            clean = relative.removeprefix("./")
            if clean in used_paths or (isinstance(grant, dict) and "target" in grant
                                      and (not isinstance(grant["target"], str)
                                           or not grant["target"].startswith("/"))):
                reasons.add("compose-secret-file-target-ambiguous")
                continue
            used_paths.add(clean)
            routes.append({"service": service, "alias": alias, "relativeFile": clean})
            # Upstream incus-compose pushes file bytes into the instance rootfs,
            # possibly shadowed by /run tmpfs. Do not call this ephemeral.
            reasons.add("incus-compose-secret-persists-in-guest-rootfs-unqualified")
    if declared and set(declared) != consumed:
        reasons.add("declared-secret-not-consumed-by-compose")
    if top_level and not routes:
        reasons.add("compose-secrets-without-approved-file-consumer")
    if routes and (not declared or len(routes) != len(consumed)):
        reasons.add("compose-file-secret-mapping-incomplete")

    return {
        "format": 1,
        "qualification": "blocked" if reasons else "no-secret-delivery-claimed",
        "blocking": sorted(reasons),
        "fileRoutes": sorted(routes, key=lambda x: (x["service"], x["alias"])),
        "note": "Static review only; no plaintext, guest mutation or Incus runtime qualification",
    }
