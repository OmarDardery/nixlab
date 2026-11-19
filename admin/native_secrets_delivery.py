"""Root-only, scoped /run delivery boundary for encrypted project credentials.

This is the native secret *file* adapter, not a Compose deployment engine.
Never put the host age key, decrypted values, project .env or build context in
Nix evaluation, process argv/env, Incus instance config or OCI image workers.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import tempfile
import threading

import native_secrets as ns
import runtime as rt


def _private_directory(path):
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise ValueError("Secret staging directory must be broker-owned")
    return path


def _sealed(path, value):
    """Create a fresh read-only secret file, fully synced before any consumer."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        data = memoryview(value.encode("utf-8"))
        while data:
            n = os.write(fd, data)
            if n < 1:
                raise ValueError("Scoped secret staging was interrupted")
            data = data[n:]
        os.fchmod(fd, 0o400)
        os.fsync(fd)
    finally:
        os.close(fd)


def _bindings(project, selected):
    if (not isinstance(project, dict) or not ns.NAME.fullmatch(project.get("name", ""))
            or not isinstance(project.get("incusProject"), str)
            or not ns.NAME.fullmatch(project["incusProject"])):
        raise ValueError("Invalid project consumer identity")
    secret = project.get("secrets")
    if not isinstance(secret, dict) or set(secret) != {"file", "bindings"}:
        raise ValueError("Missing typed secret references")
    if secret["file"] != "secrets.enc.yaml":
        raise ValueError("Only managed per-project ciphertext is deliverable")
    if not isinstance(secret["bindings"], dict) or len(secret["bindings"]) > 128:
        raise ValueError("Invalid consumer bindings")
    if not isinstance(selected, str) or not ns.NAME.fullmatch(selected):
        raise ValueError("Invalid workload consumer name")
    aliases = {}
    for alias, binding in secret["bindings"].items():
        if (not isinstance(alias, str) or not ns.KEY.fullmatch(alias)
                or not isinstance(binding, dict)
                or set(binding) != {"key", "instance", "backend", "destination", "restartUnits"}
                or not isinstance(binding["instance"], str)
                or not ns.NAME.fullmatch(binding["instance"])
                or not isinstance(binding["key"], str)
                or not ns.KEY.fullmatch(binding["key"])):
            raise ValueError("Malformed consumer reference")
        if binding["instance"] == selected:
            # NixOS guests must use *their own* sops-nix identity, not host
            # plaintext injection. No fallback that forwards host age material.
            if binding["backend"] != "incus-file":
                raise ValueError("NixOS guest requires independent sops-nix recipient provisioning")
            if (not isinstance(binding["destination"], str)
                    or not re.fullmatch(r"/run/secrets/[A-Za-z_][A-Za-z0-9_/-]*",
                                        binding["destination"])):
                raise ValueError("Secret delivery target must be /run/secrets")
            aliases[alias] = binding["key"]
    if not aliases:
        raise ValueError("No scoped secret-file bindings for requested workload")
    return aliases


class ScopedSecrets:
    def __init__(self, manager, staging=None):
        self.manager = manager
        self.staging = Path(staging or "/run")

    @contextmanager
    def open(self, project, revision, inventory, workload):
        """Stage ONLY the requested workload's values; caller sees path handles.

        The lifecycle is exclusively within this context. Failure paths never
        return partial credentials. Nothing in the response contains plaintext.
        """
        if (not isinstance(inventory, dict) or inventory.get("name") != project
                or not isinstance(project, str) or not ns.NAME.fullmatch(project)
                or not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision)):
            raise ValueError("Project scope or revision mismatch")
        aliases = _bindings(inventory, workload)
        directory = self.manager._project(project)
        host = self.manager._identity()
        self.manager._check_policy(directory, host)
        encrypted = self.manager._cipher(directory)
        if encrypted is None or hashlib.sha256(encrypted).hexdigest() != revision:
            raise ValueError("Encrypted source changed; secret delivery refused")
        index = self.manager._metadata(project)
        if index.get("revision") != revision:
            raise ValueError("Metadata/source drift blocks scoped secret delivery")
        keys = set(index.get("keys", []))
        if any(value not in keys for value in aliases.values()):
            raise ValueError("Consumer references unknown encrypted key")
        # Decrypt only after all metadata and ownership checks.
        values = self.manager._decode(encrypted)
        if keys != set(values):
            raise ValueError("SOPS ciphertext and metadata key set disagree")
        _private_directory(self.staging)
        with tempfile.TemporaryDirectory(dir=self.staging, prefix=".nixlab-scoped-") as tmp:
            runtime = _private_directory(tmp)
            previous = {}
            def clear_on_signal(signum, _frame):
                raise SystemExit(128 + signum)
            try:
                for alias, key in sorted(aliases.items()):
                    _sealed(runtime / alias, values[key])
                if threading.current_thread() is threading.main_thread():
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        previous[signum] = signal.getsignal(signum)
                        signal.signal(signum, clear_on_signal)
                rt.audit("native-secret-staged", project=project, instance=workload,
                         revision=revision, keyCount=len(aliases))
                yield {"project": project, "instance": workload, "revision": revision,
                       "paths": {alias: runtime / alias for alias in sorted(aliases)}}
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
                # TemporaryDirectory securely unlinks even on errors/signals.
                # Abrupt power loss leaves only volatile /run data (not Git/Nix).
