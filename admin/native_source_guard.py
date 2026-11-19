"""Fail-closed, read-only pre-publication checks for original OCI sources.

Never edit uploaded bytes, interpolate Compose values or print inspected values.
These checks are advisory safety barriers, not a substitute for an isolated OCI
builder or a verified Incus Compose runtime persistence audit.
"""
from pathlib import Path
import re

MAX_INSPECT = 2 * 1024 * 1024
DOCKERFILE = re.compile(r"(?i)^dockerfile(?:[._-][a-z0-9._-]+)?$")
COMPOSE = re.compile(r"(?i)^(?:docker-)?compose(?:[._-][a-z0-9_-]+)?\.ya?ml$")
CREDENTIAL = re.compile(r"(?i)(?:password|passwd|passphrase|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|authorization|auth[_-]?token)")
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z_0-9.-]*(?:PASSWORD|PASSWD|PASSPHRASE|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Za-z_0-9.-]*)\s*(?:=|:)\s*([^\s#]+)"
)
CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s@/]+@", re.I)
PRIVATE_MATERIAL = re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----|AGE-SECRET-KEY-1", re.I)


def _fail(path, problem):
    # Paths were previously validated by the importer, not source content.
    raise ValueError(f"Source publication blocked for {path}: {problem}. "
                     "Import values using Projects → Environment & Secrets (SOPS/age); "
                     "re-upload a redacted original file. Upload bytes were not modified.")


def _placeholder(value):
    value = str(value).strip().strip("\"'")
    return not value or value in ("null", "~") or (
        (value.startswith("${") and value.endswith("}"))
        or (value.startswith("{{") and value.endswith("}}"))
    )


def _inspect_text(path, data):
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        _fail(path, "Dockerfile/Compose/.env source is not valid UTF-8")
    if "\x00" in text:
        _fail(path, "Dockerfile/Compose/.env source has NUL bytes")
    if PRIVATE_MATERIAL.search(text):
        _fail(path, "private key material detected")
    if CREDENTIAL_URL.search(text):
        _fail(path, "credential-bearing URL detected")
    for number, line in enumerate(text.splitlines(), 1):
        match = CREDENTIAL_ASSIGNMENT.search(line)
        if match and not _placeholder(match.group(2)):
            _fail(path, f"possible plaintext credential on line {number}")
    return text


def _dockerfile(path, text):
    # ARG/ENV secret names are not safe even when supplied at build time:
    # layers, history and builder state can expose their resolved values.
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"\s*(?:ENV|ARG)\s+", line, re.I) and CREDENTIAL.search(line):
            _fail(path, f"unsafe build ARG/ENV credential on line {number}")
        if re.search(r"--mount\s*=\s*type\s*=\s*(?:secret|ssh)\b", line, re.I):
            _fail(path, "unqualified Dockerfile build secret/SSH mount")


def _compose(path, text):
    try:
        import yaml
        obj = yaml.safe_load(text)
    except Exception:
        _fail(path, "Compose YAML could not be safely parsed")
    if not isinstance(obj, dict) or not isinstance(obj.get("services"), dict):
        _fail(path, "Compose services mapping is invalid or unsupported")
    for service, spec in obj["services"].items():
        if not isinstance(service, str) or not isinstance(spec, dict):
            _fail(path, "Compose service structure is invalid")
        build = spec.get("build")
        if isinstance(build, dict):
            for key in ("secrets", "ssh"):
                if key in build:
                    _fail(path, f"unsupported upstream Compose build.{key}; runtime SOPS cannot satisfy build-time {key}")
            args = build.get("args", {})
            if isinstance(args, dict):
                if any(CREDENTIAL.search(str(key)) for key in args):
                    _fail(path, "secret-bearing build.args cannot enter an OCI build worker")
            elif isinstance(args, list):
                if any(CREDENTIAL.search(str(item).split("=", 1)[0]) for item in args):
                    _fail(path, "secret-bearing build.args cannot enter an OCI build worker")
        environment = spec.get("environment", {})
        if isinstance(environment, dict):
            for key, value in environment.items():
                if CREDENTIAL.search(str(key)) and value is not None and not _placeholder(value):
                    _fail(path, "plaintext secret in Compose environment")
        elif isinstance(environment, list):
            for item in environment:
                if isinstance(item, str) and "=" in item:
                    key, value = item.split("=", 1)
                    if CREDENTIAL.search(key) and not _placeholder(value):
                        _fail(path, "plaintext secret in Compose environment")
    # env_file: .env and ${VAR} remain byte-identical. Later runtime delivery
    # MUST fail closed until proven not to persist values in Incus config.


def check_source(directory, files):
    """Inspect sealed, hash-verified source at every review/publish/attestation."""
    source = Path(directory)
    for item in files:
        relative = item["path"]
        basename = Path(relative).name.lower()
        if basename == ".env" or (basename.startswith(".env.") and
                                  basename not in (".env.example", ".env.sample", ".env.template")):
            _fail(relative, "plaintext .env upload is not publishable")
        docker = DOCKERFILE.fullmatch(basename) is not None
        compose = COMPOSE.fullmatch(basename) is not None
        example_env = basename in (".env.example", ".env.sample", ".env.template")
        if not (docker or compose or example_env):
            continue
        if item["size"] > MAX_INSPECT:
            _fail(relative, "source exceeds bounded credential-inspection limit")
        # The caller has verified ownership, inode, SHA-256 and symlink safety;
        # this second, bounded read is intentionally never a source copy.
        data = (source / relative).read_bytes()
        if len(data) != item["size"]:
            _fail(relative, "source changed during inspection")
        text = _inspect_text(relative, data)
        if docker:
            _dockerfile(relative, text)
        if compose:
            _compose(relative, text)
