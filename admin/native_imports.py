"""Private, byte-exact source staging; no evaluation, building or Incus changes."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

import runtime as rt
from native_source_guard import check_source

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
PART = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*|\.[A-Za-z0-9][A-Za-z0-9._-]*)\Z")
# Frozen OCI build contexts are private, never copied to the Nix store.
# Hard quotas apply to direct uploads AND to expanded archives.
MAX_FILES = 256
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_CHUNK = 96 * 1024
EXPIRY = 3600


def check_path(value):
    if not isinstance(value, str) or not value or len(value) > 255 or "\\" in value or value.startswith("/"):
        raise ValueError("Invalid relative source path")
    parts = value.split("/")
    if len(parts) > 12 or any(not PART.fullmatch(x) or x in (".", "..") for x in parts):
        raise ValueError("Unsupported relative source path")
    for part in parts:
        lower = part.lower()
        if (lower in {".git", ".ssh", ".gnupg", ".nixlab", "node_modules",
                      ".env", "id_rsa", "id_ed25519", "id_ecdsa", "projects.agekey"}
                or lower.startswith(".env.") and lower not in {".env.example", ".env.sample", ".env.template"}
                or lower.endswith((".agekey", ".p12", ".pfx", ".key"))
                or lower.startswith("secrets.") and not re.fullmatch(r"secrets\.enc\.(ya?ml|json)", lower)):
            raise ValueError("Potential plaintext credentials or unsafe metadata in source")
    return value


def check_files(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Select 1–256 source files")
    seen, total, result = set(), 0, []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Unsupported source manifest")
        name = check_path(item["path"])
        size, digest = item["size"], item["sha256"]
        if name in seen:
            raise ValueError("Duplicate import path")
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            raise ValueError("Source file exceeds 64 MiB")
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise ValueError("Invalid source SHA-256")
        seen.add(name); total += size
        result.append({"path": name, "size": size, "sha256": digest})
    if total > MAX_TOTAL:
        raise ValueError("Source selection exceeds 128 MiB")
    return sorted(result, key=lambda x: x["path"])


def sha(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


class ImportStaging:
    def __init__(self, state=None):
        self.root = Path(state or rt.STATE) / "project-imports"
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        mode = self.root.lstat()
        if (not stat.S_ISDIR(mode.st_mode) or stat.S_ISLNK(mode.st_mode)
                or mode.st_uid != os.geteuid() or mode.st_mode & 0o077):
            raise ValueError("Private import root must be broker-owned and mode 0700")

    def _record(self, identity, actor):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError("Invalid import identity")
        directory = self.root / identity
        mode = directory.lstat()
        if not stat.S_ISDIR(mode.st_mode) or stat.S_ISLNK(mode.st_mode) or mode.st_uid != os.geteuid():
            raise ValueError("Untrusted import directory")
        record = rt.read_json(directory / "manifest.json", {})
        if record.get("id") != identity or record.get("actor") != actor:
            raise ValueError("Import owner does not match authenticated actor")
        if record.get("status") != "ready" and time.time() >= record.get("expires", 0):
            raise ValueError("Import expired; submit a new review")
        return directory, record

    def prepare(self, actor, project, files, *, archive=False):
        if not isinstance(actor, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", actor):
            raise ValueError("Invalid import actor")
        if not isinstance(project, str) or not NAME.fullmatch(project):
            raise ValueError("Invalid project name")
        entries = check_files(files)
        if type(archive) is not bool:
            raise ValueError("Invalid archive import selection")
        if archive:
            from oci_archives import is_archive_name
            if (len(entries) != 1 or not is_archive_name(entries[0]["path"])
                    or entries[0]["size"] > MAX_ARCHIVE):
                raise ValueError("Choose one bounded ZIP/TAR archive for extraction")
        identity = secrets.token_hex(16)
        target = self.root / identity
        target.mkdir(mode=0o700)
        source = target / "source"
        source.mkdir(mode=0o700)
        for entry in entries:
            if entry["size"] != 0:
                continue
            file = source / entry["path"]
            file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            file.touch(mode=0o600, exist_ok=False)
        record = {
            "id": identity, "format": 1, "status": "uploading", "actor": actor,
            "project": project, "files": entries, "manifestHash": sha(entries),
            "archive": archive,
            "created": time.time(), "expires": time.time() + EXPIRY,
        }
        rt.write_json(target / "manifest.json", record)
        rt.audit("native-source-import-created", identity=identity, principal=actor,
                 count=len(entries))
        return {"id": identity, "manifestHash": record["manifestHash"],
                "status": "uploading", "expires": record["expires"]}

    def chunk(self, identity, actor, path, offset, encoded):
        directory, record = self._record(identity, actor)
        if record["status"] != "uploading":
            raise ValueError("Source import already sealed")
        path = check_path(path)
        entry = next((f for f in record["files"] if f["path"] == path), None)
        if entry is None or type(offset) is not int or not 0 <= offset <= entry["size"]:
            raise ValueError("File not in immutable manifest or invalid offset")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_CHUNK + 2) // 3):
            raise ValueError("Upload chunk too large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Invalid source chunk") from None
        if not data or len(data) > MAX_CHUNK or offset + len(data) > entry["size"]:
            raise ValueError("Upload chunk differs from manifest limits")
        parent = directory / "source" / Path(path).parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # All intermediates are in a private broker-owned directory; no browser
        # client can write arbitrary filesystem objects into this namespace.
        fd = os.open(parent / Path(path).name,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != offset:
                raise ValueError("Unexpected import offset; restart review")
            buf = memoryview(data)
            while buf:
                n = os.write(fd, buf)
                if n <= 0:
                    raise ValueError("Incomplete source write")
                buf = buf[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"id": identity, "path": path, "offset": offset + len(data)}

    def finish(self, identity, actor, manifest_hash):
        directory, record = self._record(identity, actor)
        if record["status"] != "uploading" or manifest_hash != record["manifestHash"]:
            raise ValueError("Import already sealed or manifest changed")
        source = directory / "source"
        expected = {item["path"] for item in record["files"]}
        actual = {f.relative_to(source).as_posix() for f in source.rglob("*") if f.is_file()}
        if expected != actual:
            raise ValueError("Import files are missing or unexpected")
        for entry in record["files"]:
            fd = os.open(source / entry["path"], os.O_RDONLY | os.O_NOFOLLOW)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != entry["size"]:
                    raise ValueError("Source file changed during upload")
                hashed = hashlib.sha256()
                while True:
                    data = os.read(fd, 65536)
                    if not data:
                        break
                    hashed.update(data)
                if hashed.hexdigest() != entry["sha256"]:
                    raise ValueError("Source bytes do not match reviewed manifest")
                os.fchmod(fd, 0o400)
            finally:
                os.close(fd)
        retained_archive = None
        if record.get("archive"):
            from oci_archives import extract_archive
            if len(record["files"]) != 1:
                raise ValueError("Archive import manifest changed")
            filename = record["files"][0]["path"]
            scratch = Path(tempfile.mkdtemp(prefix=".unpack-", dir=directory))
            try:
                entries = check_files(extract_archive(
                    source / filename, scratch, check_path=check_path,
                    max_files=MAX_FILES, max_file=MAX_FILE, max_total=MAX_TOTAL))
                # All archive names, bytes and quotas have been verified before
                # touching the original sealed upload. An interruption only
                # strands a PRIVATE, unready staging job; it never publishes.
                retained_archive = directory / "uploaded-archive"
                if retained_archive.exists() or retained_archive.is_symlink():
                    raise ValueError("Unfinished archive extraction requires a fresh import")
                os.rename(source, retained_archive)
                try:
                    os.rename(scratch, source)
                except OSError:
                    os.rename(retained_archive, source)
                    raise
                _durable_directory(directory)
                record["archiveSha256"] = record["files"][0]["sha256"]
                record["archive"] = False
                record["expandedFromArchive"] = True
                record["files"] = entries
                record["manifestHash"] = sha(entries)
            finally:
                if scratch.exists():
                    shutil.rmtree(scratch)
        record["status"] = "ready"
        record["sealedAt"] = time.time()
        rt.write_json(directory / "manifest.json", record)
        if retained_archive is not None:
            shutil.rmtree(retained_archive)
        rt.audit("native-source-import-sealed", identity=identity, principal=actor,
                 project=record["project"], sourceHash=record["manifestHash"])
        return {"id": identity, "project": record["project"], "status": "ready",
                "manifestHash": record["manifestHash"], "files": len(record["files"]),
                "expandedFromArchive": record.get("expandedFromArchive", False),
                "entries": record["files"],
                "note": "Private source staging only: no evaluation, build, publication or Incus deployment"}


# A project source revision is an inert, root-owned archive outside the Nix
# workspace flake. Dockerfile/Compose bytes must never be evaluated at publish.
# The future Incus reconciler consumes a separately approved immutable digest.
import shutil
import tempfile


def _directory(path, *, private=False, create=False):
    path = Path(path)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError("Project source directory ownership or permissions are unsafe")
    return path


def _durable_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verified_source(source, files, destination=None):
    """Read the entire sealed source again; optionally copy verified bytes."""
    source = _directory(source, private=True)
    if destination is not None:
        _directory(destination, private=True)
    for item in files:
        relative = check_path(item["path"])
        parent = source
        for component in Path(relative).parts[:-1]:
            parent = _directory(parent / component, private=True)
        fd = os.open(parent / Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        output = None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_size != item["size"]
                    or info.st_mode & 0o277):
                raise ValueError("Sealed source changed after review")
            if destination is not None:
                target = destination / relative
                target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                digest.update(chunk)
                if output is not None:
                    while chunk:
                        n = os.write(output, chunk)
                        if n <= 0:
                            raise ValueError("Project source publication could not be completed")
                        chunk = chunk[n:]
            if digest.hexdigest() != item["sha256"]:
                raise ValueError("Sealed source checksum changed after review")
            if output is not None:
                os.fsync(output)
                os.fchmod(output, 0o400)
        finally:
            if output is not None:
                os.close(output)
            os.close(fd)


class PublishedSources:
    """Reviewed, CAS-protected, immutable source publication (never deployment).

    Separate from /srv/nixlab/projects: that path is a Nix flake workspace,
    where merely including a Docker build context can expose bytes in the
    world-readable Nix store and break the workspace's 1 MiB-per-file bound.
    """

    def __init__(self, root=None):
        self.root = Path(root or os.environ.get(
            "NIXLAB_PROJECT_SOURCES", "/srv/nixlab/project-sources"))

    def _current(self, project):
        if not isinstance(project, str) or not NAME.fullmatch(project):
            raise ValueError("Invalid project name")
        if not self.root.exists() and not self.root.is_symlink():
            return None
        _directory(self.root, private=True)
        directory = self.root / project
        if not directory.exists() and not directory.is_symlink():
            return None
        _directory(directory, private=True)
        path = directory / "current.json"
        if not path.exists() and not path.is_symlink():
            return None
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError("Published source reference is untrusted")
        record = rt.read_json(path)
        if (not isinstance(record, dict) or record.get("project") != project
                or not isinstance(record.get("revision"), str)
                or not HEX.fullmatch(record["revision"])):
            raise ValueError("Published source reference is invalid")
        return record

    def attest(self, project, revision, *, require_current=True):
        """Attest the exact private, immutable uploaded source without executing it.

        This is the shared digest boundary for project transactions and future
        OCI builders. Never copy a build context into the world-readable Nix
        store, and never trust only the mutable current.json pointer.
        """
        if (not isinstance(project, str) or not NAME.fullmatch(project) or
                not isinstance(revision, str) or not HEX.fullmatch(revision)):
            raise ValueError("Invalid published source identity")
        current = self._current(project)
        if current is None or (require_current and current["revision"] != revision):
            raise ValueError("Published source revision has changed; review again")
        folder = self.root / project / "revisions" / revision
        _directory(folder, private=True)
        manifest_path = folder / "manifest.json"
        metadata = manifest_path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1 or metadata.st_mode & 0o077):
            raise ValueError("Published source manifest is untrusted")
        manifest = rt.read_json(manifest_path)
        files = check_files(manifest.get("files"))
        if (manifest.get("schemaVersion") != 1 or
                manifest.get("project") != project or
                manifest.get("sourceHash") != revision or sha(files) != revision):
            raise ValueError("Published source manifest differs from reviewed bytes")
        _verified_source(folder / "source", files)
        check_source(folder / "source", files)
        return {"revision": revision, "fileCount": len(files),
                "bytes": sum(item["size"] for item in files)}

    def inventory(self):
        if not self.root.exists() and not self.root.is_symlink():
            return {"projects": []}
        _directory(self.root, private=True)
        projects = []
        for path in sorted(self.root.iterdir()):
            if not NAME.fullmatch(path.name):
                raise ValueError("Invalid published source project directory")
            _directory(path, private=True)
            record = self._current(path.name)
            if record:
                projects.append({"project": path.name, "revision": record["revision"],
                                 "publishedAt": record.get("publishedAt")})
            if len(projects) > 128:
                raise ValueError("Too many published source projects")
        return {"projects": projects}

    def review(self, staging, identity, actor, manifest_hash):
        directory, record = staging._record(identity, actor)
        if record.get("status") != "ready" or record.get("manifestHash") != manifest_hash:
            raise ValueError("Only a sealed, matching source import may be reviewed")
        files = check_files(record.get("files"))
        if sha(files) != manifest_hash:
            raise ValueError("Source manifest changed after staging")
        _verified_source(directory / "source", files)
        check_source(directory / "source", files)
        current = self._current(record["project"])
        base = current["revision"] if current else None
        old = {}
        if current:
            revision = self.root / record["project"] / "revisions" / base
            _directory(revision, private=True)
            previous = rt.read_json(revision / "manifest.json")
            if previous.get("sourceHash") != base:
                raise ValueError("Existing published source metadata is invalid")
            old = {entry["path"]: entry["sha256"] for entry in check_files(previous["files"])}
        new = {entry["path"]: entry["sha256"] for entry in files}
        changes = {"added": sorted(set(new) - set(old)),
                   "removed": sorted(set(old) - set(new)),
                   "modified": sorted(name for name in set(new) & set(old) if new[name] != old[name])}
        token = secrets.token_hex(16)
        rt.write_json(directory / "publish-review.json", {
            "id": token, "actor": actor, "project": record["project"],
            "manifestHash": manifest_hash, "baseRevision": base,
            "expires": time.time() + 600,
        })
        rt.audit("native-source-publication-reviewed", principal=actor,
                 identity=identity, project=record["project"], sourceHash=manifest_hash)
        return {"reviewId": token, "project": record["project"],
                "revision": manifest_hash, "baseRevision": base, "changes": changes,
                "fileCount": len(files), "bytes": sum(item["size"] for item in files),
                "alreadyPublished": base == manifest_hash,
                "note": "Review publishes inert source only; no build, evaluation, or Incus change"}

    def _ensure_tree(self, project):
        parent = self.root.parent
        if not parent.exists() and not parent.is_symlink():
            _directory(parent.parent)
            _directory(parent, create=True)
        _directory(parent)
        _directory(self.root, private=True, create=True)
        directory = _directory(self.root / project, private=True, create=True)
        revisions = _directory(directory / "revisions", private=True, create=True)
        return directory, revisions

    def publish(self, staging, identity, actor, review_id, *, acknowledge=False):
        if acknowledge is not True or not isinstance(review_id, str) or not ID.fullmatch(review_id):
            raise ValueError("Explicit reviewed source publication acknowledgement required")
        directory, record = staging._record(identity, actor)
        if record.get("status") != "ready":
            raise ValueError("Only sealed source can be published")
        review = rt.read_json(directory / "publish-review.json", {})
        if (review.get("id") != review_id or review.get("actor") != actor
                or review.get("project") != record["project"]
                or review.get("manifestHash") != record["manifestHash"]
                or time.time() >= review.get("expires", 0)):
            raise ValueError("Review expired or source identity changed; review again")
        files = check_files(record.get("files"))
        revision = record["manifestHash"]
        if sha(files) != revision:
            raise ValueError("Source manifest changed after review")
        _verified_source(directory / "source", files)
        check_source(directory / "source", files)
        existing = self._current(record["project"])
        base = existing["revision"] if existing else None
        if base != review.get("baseRevision"):
            raise ValueError("Published source changed after review; review again")
        project_dir, revisions = self._ensure_tree(record["project"])
        target = revisions / revision
        if target.exists() or target.is_symlink():
            _directory(target, private=True)
            metadata = rt.read_json(target / "manifest.json", {})
            if (metadata.get("sourceHash") != revision
                    or check_files(metadata.get("files")) != files):
                raise ValueError("Existing published source revision is untrusted")
            _verified_source(target / "source", files)
            check_source(target / "source", files)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=revisions))
            try:
                source = temporary / "source"
                source.mkdir(mode=0o700)
                _verified_source(directory / "source", files, destination=source)
                rt.write_json(temporary / "manifest.json", {
                    "schemaVersion": 1, "project": record["project"],
                    "sourceHash": revision, "files": files,
                    "actor": actor, "created": time.time(),
                })
                # Do not allow a partial directory to become the current source.
                # Nested build contexts must survive power loss too; fsync
                # every newly created parent before publishing the ref.
                for current_dir, _, _ in os.walk(source, topdown=False, followlinks=False):
                    _durable_directory(current_dir)
                _durable_directory(temporary)
                os.rename(temporary, target)
                _durable_directory(revisions)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        if base != revision:
            rt.write_json(project_dir / "current.json", {
                "schemaVersion": 1, "project": record["project"],
                "revision": revision, "previous": base,
                "actor": actor, "publishedAt": time.time(),
            })
        rt.audit("native-source-published", principal=actor, identity=identity,
                 project=record["project"], revision=revision, previous=base)
        rt.write_json(directory / "manifest.json", dict(record, status="published"))
        return {"project": record["project"], "revision": revision,
                "previous": base, "status": "published",
                "noChange": base == revision,
                "note": "Source only: no OCI build, Incus deployment or Nix evaluation"}
