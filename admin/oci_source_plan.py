"""Inspectable OCI source intent; no Dockerfile, YAML or Incus execution.

This module deliberately does not pronounce a Compose file compatible or a
Dockerfile build-safe. The #139 builder/Compose adapter must qualify these
immutable revisions in an isolated worker before deployment is allowed.
"""
import hashlib
import json
from pathlib import Path
import re

from native_imports import PublishedSources, check_files
import runtime as rt

COMPOSE = {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}
DOCKERFILE = {"Dockerfile", "Containerfile"}
MAX_DOCKERFILE_READ = 1024 * 1024

# Only features that cannot be silently emulated by the selected builder.
BUILDKIT = (
    re.compile(r"(?mi)^\s*#\s*syntax\s*=\s*docker/dockerfile:", re.ASCII),
    re.compile(r"(?mi)^\s*RUN\s+--(?:mount|security|device)\b", re.ASCII),
    re.compile(r"(?mi)^\s*(?:COPY|ADD)\s+--link\b", re.ASCII),
)


def _generated_compose(project, revision, dockerfile):
    """Generate a separate preview, never modify original uploaded bytes."""
    parent = Path(dockerfile).parent.as_posix()
    context = "." if parent == "." else "./" + parent
    image = "localhost/nixlab-" + project[:32] + ":" + revision[:32]
    # JSON strings are valid YAML scalar syntax and escape arbitrary paths.
    manifest = (
        "services:\n  app:\n"
        "    image: " + json.dumps(image) + "\n"
        "    build:\n"
        "      context: " + json.dumps(context) + "\n"
        "      dockerfile: " + json.dumps(Path(dockerfile).name) + "\n"
    )
    return manifest, image


def source_plan(project, revision, publisher=None):
    publisher = publisher or PublishedSources()
    attestation = publisher.attest(project, revision)
    folder = publisher.root / project / "revisions" / revision
    record = rt.read_json(folder / "manifest.json")
    files = check_files(record["files"])
    by_path = {entry["path"]: entry for entry in files}
    compose = sorted(path for path in by_path if Path(path).name in COMPOSE)
    dockerfiles = sorted(path for path in by_path if Path(path).name in DOCKERFILE)
    reasons = []
    generated, image = None, None
    if len(compose) == 1:
        mode = "compose"
        selected = compose[0]
        # The original Compose and any optional compose.incus.yaml MUST be
        # passed to the isolated, pinned incus-compose validator later.
        reasons.append("sandboxed-incus-compose-compatibility-check-required")
    elif len(compose) > 1:
        mode, selected = "ambiguous-compose", None
        reasons.append("multiple-compose-files-require-explicit-selection")
    elif len(dockerfiles) == 1:
        mode, selected = "dockerfile", dockerfiles[0]
        generated, image = _generated_compose(project, revision, selected)
        path = folder / "source" / selected
        if by_path[selected]["size"] > MAX_DOCKERFILE_READ:
            reasons.append("dockerfile-exceeds-preflight-source-size")
        else:
            # A regular file, owned and checksum-verified by attestation.
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(pattern.search(text) for pattern in BUILDKIT):
                reasons.append("unsupported-buildkit-feature-requires-explicit-policy")
    elif dockerfiles:
        mode, selected = "ambiguous-dockerfile", None
        reasons.append("multiple-dockerfiles-require-explicit-selection")
    else:
        mode, selected = "unsupported", None
        reasons.append("no-compose-or-dockerfile-entrypoint")
    reasons.extend(["isolated-oci-builder-not-qualified",
                    "source-qualified-image-import-not-qualified",
                    "incus-compose-owned-deployment-not-qualified"])
    return {
        "format": 1,
        "project": project,
        "revision": attestation["revision"],
        "fileCount": attestation["fileCount"],
        "bytes": attestation["bytes"],
        "sourceFiles": files,
        "mode": mode,
        "entrypoint": selected,
        "generatedCompose": generated,
        "proposedImage": image,
        "buildContextHash": revision,
        "buildIdentity": hashlib.sha256((project + "\0" + revision).encode()).hexdigest(),
        "canBuild": False,
        "canDeploy": False,
        "blocking": reasons,
        "note": "Source-only planning: no OCI image, Incus resources or volume created",
    }
