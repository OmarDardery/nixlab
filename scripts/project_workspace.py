"""Read-only preflight for an *independent* declarative NixLab workspace.

This module does not evaluate Nix or grant permission to deploy anything.
A privileged broker must freeze the exact reviewed source, recheck this
manifest, use a restricted build environment, and require authorization before
applying any Incus change (tracked separately in #135).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
MAX_FILES = 4096
MAX_PROJECTS = 128
MAX_MODULES_PER_PROJECT = 256
MAX_DEPTH = 16
MAX_MODULE_DEPTH = 8
MAX_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
CIPHERTEXT = re.compile(r"^secrets\\.enc\\.(yaml|yml|json)$", re.IGNORECASE)


class WorkspaceError(ValueError):
    pass


def _refuse_sensitive_file(name):
    lower = name.lower()
    if (lower == ".env" or lower.startswith(".env.")
            or lower in {"id_rsa", "id_ed25519", "id_ecdsa", "projects.agekey"}
            or lower.endswith((".agekey", ".p12", ".pfx", ".key"))
            or (lower.startswith("secrets.") and not CIPHERTEXT.fullmatch(lower))):
        raise WorkspaceError("Potential plaintext credential file in project source: " + name)


def _entries(directory):
    # DirEntry.is_symlink never follows a link. This also excludes links into
    # unrelated host storage (including a symlinked project directory).
    try:
        items = sorted(os.scandir(directory), key=lambda item: item.name)
    except OSError as exc:
        raise WorkspaceError("Unable to read project directory: " + str(directory)) from exc
    for item in items:
        if item.is_symlink():
            raise WorkspaceError("Symlink forbidden in project source: " + item.name)
        if not (item.is_file(follow_symlinks=False) or item.is_dir(follow_symlinks=False)):
            raise WorkspaceError("Unsupported filesystem object in project source: " + item.name)
    return items


def validate_workspace(root, *, require_lock=True):
    """Return a content-hashed, JSON-compatible source manifest; never mutate.

    The caller is responsible for privilege separation, authentication,
    atomic source snapshots and comparing the captured hashes at publication.
    """
    root = Path(root).absolute()
    try:
        mode = root.lstat().st_mode
    except OSError as exc:
        raise WorkspaceError("Project workspace directory is unavailable") from exc
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise WorkspaceError("Project workspace root must be a real directory")

    files = {}
    total_bytes = 0
    project_names = set()
    discovered = {}
    root_entries = _entries(root)
    root_types = {item.name: item for item in root_entries}
    if "flake.nix" not in root_types or not root_types["flake.nix"].is_file(follow_symlinks=False):
        raise WorkspaceError("Workspace flake.nix is missing")
    if require_lock:
        lock = root_types.get("flake.lock")
        if lock is None or not lock.is_file(follow_symlinks=False):
            raise WorkspaceError("Pinned workspace flake.lock is required")
        try:
            parsed = json.loads((root / "flake.lock").read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or parsed.get("version") != 7:
                raise ValueError("Unsupported lock version")
            nodes = parsed.get("nodes")
            if not isinstance(nodes, dict) or parsed.get("root") not in nodes:
                raise ValueError("Missing root lock node")
        except (OSError, UnicodeError, ValueError) as exc:
            raise WorkspaceError("Invalid pinned workspace flake.lock") from exc

    # Walk the entire source, not only auto-imported modules: unused source can
    # still enter the Nix store when the flake path is copied there.
    def walk(directory, depth):
        nonlocal total_bytes
        if depth > MAX_DEPTH:
            raise WorkspaceError("Project source exceeds directory nesting limit")
        for item in _entries(directory):
            # .git internals are not deployable project source; do not hash
            # repository internals or blindly copy them into the backup manifest.
            if depth == 0 and item.name == ".git" and item.is_dir(follow_symlinks=False):
                continue
            candidate = Path(item.path)
            relative = candidate.relative_to(root).as_posix()
            if item.is_dir(follow_symlinks=False):
                if depth == 0 and item.name in {"state", "backups-local"}:
                    raise WorkspaceError("Mutable data must be outside the projects flake")
                walk(candidate, depth + 1)
                continue
            _refuse_sensitive_file(item.name)
            st = item.stat(follow_symlinks=False)
            if st.st_nlink != 1:
                raise WorkspaceError("Hardlinked file forbidden in project source: " + relative)
            if st.st_size > MAX_FILE_BYTES:
                raise WorkspaceError("Project source file exceeds 1 MiB: " + relative)
            total_bytes += st.st_size
            if total_bytes > MAX_BYTES:
                raise WorkspaceError("Project source exceeds 32 MiB")
            if len(files) >= MAX_FILES:
                raise WorkspaceError("Project workspace exceeds file limit")
            digest = hashlib.sha256()
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    digest.update(block)
            files[relative] = {"size": st.st_size, "sha256": digest.hexdigest()}

    walk(root, 0)

    def module_files(directory, depth=0):
        if depth > MAX_MODULE_DEPTH:
            raise WorkspaceError("Project module nesting exceeds eight directories")
        result = []
        for item in _entries(directory):
            if item.name.startswith(("_", ".")):
                continue
            target = Path(item.path)
            if item.is_dir(follow_symlinks=False):
                result.extend(module_files(target, depth + 1))
            elif item.name.endswith(".nix"):
                result.append(target.relative_to(root).as_posix())
        return result

    for item in root_entries:
        if item.name.startswith(("_", ".")) or not item.is_dir(follow_symlinks=False):
            continue
        directory = Path(item.path)
        default = directory / "default.nix"
        if not default.exists():
            continue
        if not ROOT_NAME.fullmatch(item.name):
            raise WorkspaceError("Invalid project directory name: " + item.name)
        if not default.is_file() or default.is_symlink():
            raise WorkspaceError("Project default.nix must be a regular source file")
        if len(project_names) >= MAX_PROJECTS:
            raise WorkspaceError("Too many projects")
        project_names.add(item.name)
        modules_root = directory / "modules"
        if modules_root.exists() and not modules_root.is_dir():
            raise WorkspaceError("Project modules entry must be a directory")
        modules = module_files(modules_root) if modules_root.is_dir() else []
        if len(modules) > MAX_MODULES_PER_PROJECT:
            raise WorkspaceError("Project module count exceeds 256")
        discovered[item.name] = {
            "default": f"{item.name}/default.nix",
            "modules": modules,
        }

    # Sort source identities, hash file paths/content metadata and return a
    # verifiable candidate manifest without exposing any file contents.
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schemaVersion": 1,
        "sourceHash": hashlib.sha256(canonical).hexdigest(),
        "bytes": total_bytes,
        "files": files,
        "projects": discovered,
    }


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--unlocked-template"):
        raise SystemExit("usage: project_workspace.py PATH [--unlocked-template]")
    try:
        result = validate_workspace(sys.argv[1], require_lock=len(sys.argv) == 2)
    except WorkspaceError as exc:
        print("Workspace validation failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))
