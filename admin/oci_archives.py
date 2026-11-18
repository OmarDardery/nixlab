"""Strict, bounded, data-only ZIP/TAR extraction for private OCI build contexts.

Nothing here uses archive.extract()/extractall(), follows archive links,
executes a Dockerfile, parses Nix, or opens an Incus socket. All target paths
must pass the same source path rules as direct file uploads.
"""
import hashlib
import os
from pathlib import Path
import stat
import tarfile
import zipfile

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
MAX_ARCHIVE_ENTRIES = 1024


def is_archive_name(name):
    return isinstance(name, str) and name.lower().endswith(ARCHIVE_SUFFIXES)


def _copy_member(reader, root, relative, stated_size, *, max_file, max_total, total):
    target = root / relative
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if stated_size < 0 or stated_size > max_file or total + stated_size > max_total:
        raise ValueError("Archive entry exceeds uncompressed source quota")
    sha = hashlib.sha256()
    size = 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            block = reader.read(min(65536, stated_size - size + 1))
            if not block:
                break
            size += len(block)
            if size > stated_size or size > max_file or total + size > max_total:
                raise ValueError("Archive entry exceeds declared size or uncompressed quota")
            sha.update(block)
            cursor = memoryview(block)
            while cursor:
                written = os.write(fd, cursor)
                if written < 1:
                    raise ValueError("Incomplete archive extraction")
                cursor = cursor[written:]
        if size != stated_size:
            raise ValueError("Truncated archive entry")
        os.fsync(fd)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)
    return {"path": relative, "size": size, "sha256": sha.hexdigest()}


def extract_archive(archive, destination, *, check_path, max_files, max_file, max_total):
    """Materialize regular entries to a NEW mode-0700 directory.

    Reject ZIP/TAR symlinks, hardlinks, devices, FIFOs, traversal, duplicates,
    implausible metadata, encrypted ZIP files and decompression bombs. Explicit
    ZIP/TAR entry size and streamed bytes are checked independently.
    """
    archive, destination = Path(archive), Path(destination)
    if not is_archive_name(archive.name):
        raise ValueError("Upload a ZIP, TAR, TAR.GZ or TGZ archive")
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("Archive extraction target must be an empty private directory")
    info = archive.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Archive upload must be a regular file")
    files, seen, total, entries = [], set(), 0, 0

    def accept(name, size, reader):
        nonlocal total, entries
        entries += 1
        if entries > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Archive has too many members")
        safe = check_path(name)
        if safe in seen:
            raise ValueError("Archive contains duplicate source paths")
        if len(files) >= max_files:
            raise ValueError("Archive exceeds source file count limit")
        if size > max_file or total + size > max_total:
            raise ValueError("Archive exceeds uncompressed source quota")
        seen.add(safe)
        entry = _copy_member(reader, destination, safe, size, max_file=max_file,
                             max_total=max_total, total=total)
        total += entry["size"]
        files.append(entry)

    try:
        if archive.name.lower().endswith(".zip"):
            with zipfile.ZipFile(archive, "r", allowZip64=True) as z:
                # Bound metadata allocations and directory-only attack entries.
                if len(z.infolist()) > MAX_ARCHIVE_ENTRIES:
                    raise ValueError("Archive has too many members")
                for member in z.infolist():
                    mode = member.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if member.flag_bits & 1:
                        raise ValueError("Encrypted archive entries are unsupported")
                    if member.is_dir():
                        if kind not in (0, stat.S_IFDIR):
                            raise ValueError("Unsafe archive directory")
                        check_path(member.filename.rstrip("/"))
                        continue
                    if kind not in (0, stat.S_IFREG):
                        raise ValueError("Archive links and special files are forbidden")
                    with z.open(member, "r") as reader:
                        accept(member.filename, member.file_size, reader)
        else:
            with tarfile.open(archive, mode="r:*") as tar:
                for member in tar:
                    entries += 1
                    if entries > MAX_ARCHIVE_ENTRIES:
                        raise ValueError("Archive has too many members")
                    if member.isdir():
                        check_path(member.name.rstrip("/"))
                        continue
                    if not member.isfile() or getattr(member, "sparse", None):
                        raise ValueError("Archive links, sparse entries and special files are forbidden")
                    reader = tar.extractfile(member)
                    if reader is None:
                        raise ValueError("Archive member could not be read")
                    with reader:
                        # Count only one member per actual input.
                        entries -= 1
                        accept(member.name, member.size, reader)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, RuntimeError):
        raise ValueError("Archive is invalid, incomplete or unsupported") from None
    if not files:
        raise ValueError("Archive contains no source files")
    return sorted(files, key=lambda item: item["path"])
