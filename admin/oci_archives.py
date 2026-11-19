"""Strict, bounded, data-only ZIP/TAR extraction for private OCI build contexts.

Nothing here uses archive.extract()/extractall(), follows archive links,
executes a Dockerfile, parses Nix, or opens an Incus socket. All target paths
must pass the same source path rules as direct file uploads.
"""
import hashlib
import os
from pathlib import Path
import stat
import struct
import tarfile
import zipfile

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
MAX_ARCHIVE_ENTRIES = 1024
MAX_ZIP_DIRECTORY_BYTES = 2 * 1024 * 1024


def is_archive_name(name):
    return isinstance(name, str) and name.lower().endswith(ARCHIVE_SUFFIXES)


def _preflight_zip_directory(archive):
    """Bound central-directory parsing memory before constructing ZipInfo objects.

    A malicious 64 MiB ZIP can contain hundreds of thousands of empty entries,
    exhausting the root broker even when the expanded-data limit is small.
    ZIP64 and multi-disk archives are unnecessary for a 256-file/128-MiB
    context and are deliberately rejected rather than parsed as exceptions.
    """
    with archive.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
    where = tail.rfind(b"PK\x05\x06")
    if where < 0 or len(tail) - where < 22:
        raise ValueError("Archive has no valid bounded ZIP directory")
    signature, disk, directory_disk, disk_entries, entries, length, offset, comment = (
        struct.unpack_from("<IHHHHIIH", tail, where))
    if (signature != 0x06054B50 or disk or directory_disk or
            disk_entries != entries or not 1 <= entries <= MAX_ARCHIVE_ENTRIES or
            length > MAX_ZIP_DIRECTORY_BYTES or entries == 0xFFFF or
            offset == 0xFFFFFFFF or length == 0xFFFFFFFF or
            offset + length > size or where + 22 + comment != len(tail)):
        raise ValueError("Archive has an unsupported or oversized ZIP directory")


def _member_path(name, *, directory=False):
    # Standard 'tar -C project .' produces './' and './Dockerfile'.
    # Remove only redundant LEADING './', never inner '.', '..', or an
    # absolute path. The resulting relative name remains byte-for-byte
    # equivalent as a filesystem path, without renaming any file content.
    while name.startswith("./"):
        name = name[2:]
    if directory and name.rstrip("/") in ("", "."):
        return None
    return name.rstrip("/") if directory else name


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
        safe = check_path(_member_path(name))
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
            _preflight_zip_directory(archive)
            with zipfile.ZipFile(archive, "r", allowZip64=False) as z:
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
                        relative = _member_path(member.filename, directory=True)
                        if relative is not None:
                            check_path(relative)
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
                        relative = _member_path(member.name, directory=True)
                        if relative is not None:
                            check_path(relative)
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
