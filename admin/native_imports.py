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

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
PART = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*|\.[A-Za-z0-9_-]+)\Z")
MAX_FILES = 64
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
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
        raise ValueError("Select 1–64 source files")
    seen, total, result = set(), 0, []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Unsupported source manifest")
        name = check_path(item["path"])
        size, digest = item["size"], item["sha256"]
        if name in seen:
            raise ValueError("Duplicate import path")
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            raise ValueError("Source file exceeds 8 MiB")
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise ValueError("Invalid source SHA-256")
        seen.add(name); total += size
        result.append({"path": name, "size": size, "sha256": digest})
    if total > MAX_TOTAL:
        raise ValueError("Source selection exceeds 32 MiB")
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

    def prepare(self, actor, project, files):
        if not isinstance(actor, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", actor):
            raise ValueError("Invalid import actor")
        if not isinstance(project, str) or not NAME.fullmatch(project):
            raise ValueError("Invalid project name")
        entries = check_files(files)
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
        record["status"] = "ready"
        record["sealedAt"] = time.time()
        rt.write_json(directory / "manifest.json", record)
        rt.audit("native-source-import-sealed", identity=identity, principal=actor,
                 project=record["project"], sourceHash=record["manifestHash"])
        return {"id": identity, "project": record["project"], "status": "ready",
                "manifestHash": record["manifestHash"], "files": len(record["files"]),
                "note": "Private source staging only: no evaluation, build, publication or Incus deployment"}
