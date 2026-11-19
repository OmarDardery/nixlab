"""Fail-closed source-secret preflight for inert OCI uploads.

Only rejects known credential-bearing source constructs. It does not claim
general-purpose DLP: unknown secrets and arbitrary Dockerfile RUN commands
still require an isolated builder and a separate deployment review.
"""
import re
from pathlib import Path

# A named secret must be a reference, never a literal in Dockerfile/Compose.
CREDENTIAL = re.compile(
    r"(?:PASSWORD|PASSWD|PASS|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|"
    r"ACCESS[_-]?KEY|CLIENT[_-]?SECRET|AUTH[_-]?TOKEN|CREDENTIAL)",
    re.IGNORECASE,
)
REFERENCE = re.compile(
    r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?:(?::[-?])[^}]*)?\}$"
)
ENV_DIRECTIVE = re.compile(r"^\s*(?:ENV|ARG)\s+(.+)$", re.IGNORECASE)
ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
# A conservative subset of Compose syntax; reject ambiguous inline values
# rather than silently leaking credentials to Incus or OCI layers.
COMPOSE_KEY = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*(?:#.*)?$")
LIST_ITEM = re.compile(r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _literal(value):
    value = value.strip().strip("'\"").strip()
    if not value or value in ("null", "~"):
        return False
    # A reference is only safe if the downstream adapter resolves it into a
    # protected runtime file. Build-time references remain prohibited.
    return not bool(REFERENCE.fullmatch(value))


def _dockerfile(path, raw):
    try:
        content = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Cannot inspect Dockerfile as UTF-8") from None
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = ENV_DIRECTIVE.match(line)
        if not match:
            continue
        directive = line.lstrip().split(None, 1)[0].upper()
        rest = match.group(1).strip()
        for key, value in ASSIGNMENT.findall(rest):
            if CREDENTIAL.search(key):
                # ARG PASSWORD (without default) is still forbidden: it could
                # enter the build process and image history at build time.
                if directive in ("ARG", "ENV"):
                    raise ValueError(
                        f"Credential-bearing Dockerfile {directive} in {path}; "
                        "use the NixLab SOPS secrets UI and runtime file bindings")
        if directive == "ARG" and CREDENTIAL.search(rest.split()[0].split("=")[0]):
            raise ValueError(
                f"Credential-bearing Dockerfile ARG in {path}; "
                "build-time secrets are not supported")
        # Legacy ENV KEY VALUE syntax, where values are not assignment tokens.
        if directive == "ENV" and "=" not in rest:
            parts = rest.split(None, 1)
            if len(parts) == 2 and CREDENTIAL.search(parts[0]):
                raise ValueError(
                    f"Credential-bearing Dockerfile ENV in {path}; use SOPS")


def _compose(path, raw):
    try:
        content = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Cannot inspect Compose YAML as UTF-8") from None
    # Only examine environment/build args contexts, not unrelated YAML keys.
    section = None
    indent = -1
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        matched = COMPOSE_KEY.match(line)
        if matched:
            level, key, value = len(matched.group(1)), matched.group(2), matched.group(3)
            if section is not None and level <= indent:
                section = None
            if key in ("environment", "args") and not value:
                section, indent = key, level
                continue
            if section is not None and level > indent and CREDENTIAL.search(key):
                if section in ("args", "environment"):
                    raise ValueError(
                        f"Credential-bearing Compose {section} in {path}; "
                        "import values into SOPS and use scoped runtime files")
        if section is not None and len(line) - len(line.lstrip()) > indent:
            item = LIST_ITEM.match(line)
            if item and CREDENTIAL.search(item.group(1)):
                if section in ("args", "environment"):
                    raise ValueError(
                        f"Credential-bearing Compose {section} in {path}; "
                        "use the NixLab SOPS secrets UI")


def inspect_source(source, files):
    """Check sealed bytes before source review AND immediately before publish.

    The import layer has already verified ownership and SHA-256. Keep this
    check bounded; a larger or undecodable source cannot bypass inspection.
    """
    for item in files:
        name = item["path"]
        basename = Path(name).name.lower()
        if basename in ("dockerfile", "containerfile") or basename.startswith(("dockerfile.", "containerfile.")):
            if item["size"] > 1024 * 1024:
                raise ValueError("Dockerfile too large for credential preflight")
            _dockerfile(name, (source / name).read_bytes())
        elif basename in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"):
            if item["size"] > 1024 * 1024:
                raise ValueError("Compose source too large for credential preflight")
            _compose(name, (source / name).read_bytes())
