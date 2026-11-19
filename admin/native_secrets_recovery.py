"""Passphrase-encrypted, self-verifying off-host recovery kit.

Kit bytes are ciphertext-only API output. The password and host age identity
never enter argv, environment values, logs, Nix derivations or a plaintext file.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
import tempfile

import native_secrets as ns
import runtime as rt

MAGIC = b"NIXLAB-SECRETS-RECOVERY-V1"
MAX_KIT = 256 * 1024
FILES = {
    "restic-password": Path("/var/lib/nixlab-secrets/restic-password"),
    "restic-environment": Path("/var/lib/nixlab-secrets/restic-environment"),
    "remote-access": Path("/var/lib/nixlab-secrets/cloudflared.json"),
}


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _un64(data):
    if not isinstance(data, str) or len(data) > MAX_KIT * 2:
        raise ValueError("Invalid recovery payload")
    return base64.b64decode(data, validate=True)


def _password(passphrase):
    if (not isinstance(passphrase, str) or not 16 <= len(passphrase) <= 256
            or len(passphrase.encode("utf-8")) > 1024):
        raise ValueError("Recovery passphrase must be 16–256 characters")
    return passphrase.encode("utf-8")


def _derive(passphrase, salt):
    return hashlib.scrypt(_password(passphrase), salt=salt,
                          n=32768, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise ValueError("Pinned cryptography runtime is not installed") from None
    return AESGCM


def _decode(blob, passphrase):
    try:
        envelope = json.loads(blob) if isinstance(blob, (bytes, str)) else blob
        if not isinstance(envelope, dict) or set(envelope) != {
                "format", "version", "salt", "nonce", "ciphertext"}:
            raise ValueError
        if envelope["format"] != "nixlab-recovery-kit" or envelope["version"] != 1:
            raise ValueError
        salt, nonce, cipher = (_un64(envelope[key]) for key in ("salt", "nonce", "ciphertext"))
        if len(salt) != 16 or len(nonce) != 12 or len(cipher) > MAX_KIT:
            raise ValueError
        plain = _aes()(_derive(passphrase, salt)).decrypt(nonce, cipher, MAGIC)
        record = json.loads(plain)
        if not isinstance(record, dict) or set(record) != {"version", "recipient", "identity", "credentials", "manifest"}:
            raise ValueError
        if record["version"] != 1 or not ns.RECIPIENT.fullmatch(record["recipient"]):
            raise ValueError
        if not isinstance(record["identity"], str) or not ns.PRIVATE_KEY.fullmatch(record["identity"].strip()):
            raise ValueError
        if not isinstance(record["credentials"], dict) or set(record["credentials"]) - set(FILES):
            raise ValueError
        credentials = {key: _un64(value) for key, value in record["credentials"].items()}
        if any(len(data) > 32768 for data in credentials.values()):
            raise ValueError
        expected_manifest = {
            "recipient": record["recipient"],
            "credentials": {key: {"sha256": hashlib.sha256(data).hexdigest(),
                                  "bytes": len(data)} for key, data in sorted(credentials.items())},
        }
        if record["manifest"] != expected_manifest:
            raise ValueError
        return record, credentials
    except (ValueError, TypeError, KeyError, OverflowError, MemoryError):
        raise ValueError("Recovery kit or passphrase is invalid") from None
    except Exception:
        # Invalid AEAD authentication failures must never leak cryptographic
        # exception details or rejected plaintext.
        raise ValueError("Recovery kit or passphrase is invalid") from None


class RecoveryKit:
    def __init__(self, manager, credentials=None, staging=None):
        self.manager = manager
        self.files = credentials if credentials is not None else FILES
        self.staging = Path(staging or "/run")

    def _prove_unlock(self, record):
        """Verify actual SOPS encrypt/decrypt with the kit's recovered identity.

        Temporary identity and all decrypted data exist only in a broker-owned
        0700 directory on /run (tmpfs on NixOS). No plaintext crosses argv,
        Nix evaluation, Incus or public API boundaries.
        """
        ns._dir(self.staging)
        with tempfile.TemporaryDirectory(dir=self.staging, prefix=".nixlab-recovery-") as work:
            private = Path(work) / "identity"
            fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            try:
                os.fchmod(fd, 0o400)
                material = (record["identity"].strip() + "\n").encode("ascii")
                os.write(fd, material)
                os.fsync(fd)
            finally:
                os.close(fd)
            probe = b'{"RECOVERY_PROBE":"offline-unlock-check"}'
            encrypted = ns._run([
                "sops", "encrypt", "--age", record["recipient"],
                "--filename-override", "secrets.enc.yaml",
                "--input-type", "json", "--output-type", "yaml", "/dev/stdin",
            ], probe)
            output = ns._run([
                "sops", "decrypt", "--input-type", "yaml",
                "--output-type", "json", "/dev/stdin",
            ], encrypted, key_path=private)
            try:
                if json.loads(output) != json.loads(probe):
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("Recovery kit could not unlock verified SOPS ciphertext") from None
            # Restore source may be installed BEFORE identity activation. If it
            # exists, prove its actual project ciphertext unlocks as well.
            if self.manager.projects.exists() and not self.manager.projects.is_symlink():
                ns._dir(self.manager.projects)
                for project in sorted(self.manager.projects.iterdir()):
                    if not ns.NAME.fullmatch(project.name) or not (project / "default.nix").is_file():
                        continue
                    project = self.manager._project(project.name)
                    ciphertext = self.manager._cipher(project)
                    if ciphertext is not None:
                        decrypted = ns._run([
                            "sops", "decrypt", "--input-type", "yaml",
                            "--output-type", "json", "/dev/stdin",
                        ], ciphertext, key_path=private)
                        document = json.loads(decrypted)
                        if (not isinstance(document, dict)
                                or any(not ns.KEY.fullmatch(key) for key in document)):
                            raise ValueError("Recovery source ciphertext cannot be validated")


    def export(self, actor, passphrase, *, include_backup=False):
        recipient = self.manager._identity()
        private = ns._regular(self.manager.identity, 4096, private=True).decode("ascii").strip()
        if not ns.PRIVATE_KEY.fullmatch(private):
            raise ValueError("Private age identity is invalid")
        saved = {}
        if include_backup:
            for label, path in sorted(self.files.items()):
                if path.exists() or path.is_symlink():
                    ns._dir(path.parent, private=True)
                    saved[label] = _b64(ns._regular(path, 32768, private=True))
        manifest = {"recipient": recipient, "credentials": {
            label: {"sha256": hashlib.sha256(_un64(value)).hexdigest(),
                    "bytes": len(_un64(value))}
            for label, value in sorted(saved.items())}}
        payload = json.dumps({"version": 1, "recipient": recipient,
                              "identity": private, "credentials": saved,
                              "manifest": manifest},
                             sort_keys=True, separators=(",", ":")).encode()
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        cipher = _aes()(_derive(passphrase, salt)).encrypt(nonce, payload, MAGIC)
        envelope = {"format": "nixlab-recovery-kit", "version": 1,
                    "salt": _b64(salt), "nonce": _b64(nonce),
                    "ciphertext": _b64(cipher)}
        serial = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        if len(serial) > MAX_KIT:
            raise ValueError("Recovery kit exceeded size limit")
        verified, _ = _decode(serial, passphrase)
        if verified["recipient"] != recipient:
            raise ValueError("Recovery kit self-test failed")
        rt.audit("native-recovery-kit-exported", principal=actor,
                 backupCredentials=sorted(saved), encrypted=True)
        return {"format": "nixlab-recovery-kit-v1", "encrypted": _b64(serial),
                "sha256": hashlib.sha256(serial).hexdigest(),
                "verified": False, "requiresOfflineUnlockVerification": True,
                "recipient": recipient,
                "credentialNames": sorted(saved),
                "note": "Store offline, separate from encrypted project sources; never share the passphrase"}

    def inspect(self, actor, encrypted, passphrase):
        serial = _un64(encrypted)
        if len(serial) > MAX_KIT:
            raise ValueError("Invalid recovery kit")
        record, credentials = _decode(serial, passphrase)
        # Check the private identity derives exactly this public recipient.
        candidate = ns._run(["age-keygen", "-y"], (record["identity"] + "\n").encode()).decode().strip()
        if candidate != record["recipient"]:
            raise ValueError("Recovery identity did not match its recipient")
        self._prove_unlock(record)
        rt.audit("native-recovery-kit-verified", principal=actor)
        return {"valid": True, "verified": True,
                "recipient": record["recipient"],
                "credentialNames": sorted(credentials),
                "note": "Recovery kit verified in memory; no host files modified"}

    def restore(self, actor, encrypted, passphrase, *, acknowledge=False):
        if acknowledge is not True:
            raise ValueError("Explicit offline recovery approval required")
        serial = _un64(encrypted)
        record, credentials = _decode(serial, passphrase)
        candidate = ns._run(["age-keygen", "-y"], (record["identity"] + "\n").encode()).decode().strip()
        if candidate != record["recipient"]:
            raise ValueError("Recovery key and recipient mismatch")
        # Preflight ALL backed-up destinations before modifying anything.
        for label, value in credentials.items():
            target = self.files[label]
            if target.exists() or target.is_symlink():
                raise ValueError("Recovery target already exists; no credential may be replaced")
            ns._dir(target.parent, private=True, create=True)
            if len(value) > 32768:
                raise ValueError("Recovery credential is oversized")
        if self.manager.identity.exists() or self.manager.identity.is_symlink():
            raise ValueError("An existing age identity may not be replaced")
        self.manager.enroll_identity(actor, "restore",
                                     private_key=record["identity"], acknowledge=True)
        # Identity is always restored before any encrypted project data is
        # activated. If an I/O error occurs, never delete the identity.
        for label, value in sorted(credentials.items()):
            target = self.files[label]
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                data = memoryview(value)
                while data:
                    n = os.write(fd, data)
                    if n < 1:
                        raise ValueError("Recovery credential could not be written")
                    data = data[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
            ns._fsync(target.parent)
        rt.audit("native-recovery-kit-restored", principal=actor,
                 backupCredentials=sorted(credentials))
        return {"restored": True, "recipient": record["recipient"],
                "credentialNames": sorted(credentials),
                "note": "Age identity and selected unlock material restored; project data remains separate"}
