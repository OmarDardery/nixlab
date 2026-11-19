"""Write-only SOPS/age project secrets, never a browser decryption API.

The broker alone imports this module. It is called with rt.locked() held for
each mutation. Ciphertext is declarative source; the host age identity is not.
The source-only implementation deliberately does NOT deploy secrets to Incus.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import time

import runtime as rt

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
RECIPIENT = re.compile(r"age1[023456789acdefghjklmnpqrstuvwxyz]{50,70}\Z")
PRIVATE_KEY = re.compile(r"AGE-SECRET-KEY-1[023456789ACDEFGHJKLMNPQRSTUVWXYZ]{50,70}\Z")
IDENTITY_FILE = Path("/var/lib/nixlab/keys/projects.agekey")
PROJECTS = Path("/srv/nixlab/projects")
MAX_SECRET = 8192
MAX_PLAINTEXT = 65536
MAX_ENCRYPTED = 1024 * 1024
REVIEW_TTL = 600


def _dir(path, private=False, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError("Unsafe secrets directory")
    return path


def _regular(path, max_bytes, private=False):
    """Reject aliasing, symlinks, foreign owners and oversized inputs."""
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_size > max_bytes
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError("Unsafe secrets file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(fd)
        if current.st_ino != info.st_ino or current.st_dev != info.st_dev:
            raise ValueError("Secret file changed during validation")
        data = bytearray()
        while len(data) <= max_bytes:
            part = os.read(fd, min(65536, max_bytes + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        if len(data) != info.st_size or len(data) > max_bytes:
            raise ValueError("Secret file changed during read")
        return bytes(data)
    finally:
        os.close(fd)


def _fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run(argv, data=None, *, key_path=None):
    """Never put values or private keys into argv, environment or logs."""
    env = {"PATH": os.environ.get("PATH", "/run/current-system/sw/bin:/usr/bin:/bin"),
           "HOME": "/var/empty", "LC_ALL": "C"}
    if key_path is not None:
        env["SOPS_AGE_KEY_FILE"] = str(key_path)
    try:
        proc = subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, check=False, timeout=25, env=env)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("SOPS or age operation unavailable") from None
    if proc.returncode or len(proc.stdout) > 2 * MAX_ENCRYPTED:
        raise ValueError("SOPS or age operation rejected") from None
    return proc.stdout


def _hash(data):
    return hashlib.sha256(data).hexdigest()


class Secrets:
    def __init__(self, state=None, projects=None, identity=None):
        self.state = Path(state or rt.STATE) / "project-secrets"
        self.projects = Path(projects or PROJECTS)
        self.identity = Path(identity or IDENTITY_FILE)

    def _private_state(self):
        _dir(self.state.parent)
        return _dir(self.state, private=True, create=True)

    def _identity(self):
        _dir(self.identity.parent, private=True)
        _regular(self.identity, 4096, private=True)
        raw = _run(["age-keygen", "-y", str(self.identity)])
        try:
            recipient = raw.decode("ascii").strip()
        except UnicodeError:
            raise ValueError("Invalid age identity") from None
        if not RECIPIENT.fullmatch(recipient):
            raise ValueError("Invalid age recipient")
        return recipient

    def identity_status(self):
        try:
            recipient = self._identity()
        except FileNotFoundError:
            return {"configured": False, "healthy": False, "recipient": None}
        except (ValueError, OSError):
            return {"configured": True, "healthy": False, "recipient": None}
        return {"configured": True, "healthy": True, "recipient": recipient}

    def enroll_identity(self, actor, mode, *, private_key=None, acknowledge=False):
        if acknowledge is not True or mode not in ("generate", "restore"):
            raise ValueError("Explicit generate-or-restore approval required")
        # Do not rotate an existing identity in response to a retry or reboot.
        parent = self.identity.parent
        if parent.exists() or parent.is_symlink():
            _dir(parent, private=True)
        else:
            _dir(parent.parent)
            _dir(parent, private=True, create=True)
        if self.identity.exists() or self.identity.is_symlink():
            raise ValueError("Existing age identity cannot be overwritten")
        if mode == "generate":
            if private_key is not None:
                raise ValueError("Unexpected identity import data")
            output = _run(["age-keygen"])
            keylines = [line.strip() for line in output.decode("ascii").splitlines()
                        if line.strip().startswith("AGE-SECRET-KEY-")]
            if len(keylines) != 1 or not PRIVATE_KEY.fullmatch(keylines[0]):
                raise ValueError("age-keygen returned an invalid identity")
            raw = (keylines[0] + "\n").encode("ascii")
        else:
            if not isinstance(private_key, str) or len(private_key) > 4096:
                raise ValueError("Invalid recovery identity")
            lines = [line.strip() for line in private_key.splitlines()
                     if line.strip().startswith("AGE-SECRET-KEY-")]
            if len(lines) != 1 or not PRIVATE_KEY.fullmatch(lines[0]):
                raise ValueError("Invalid recovery identity")
            raw = (lines[0] + "\n").encode("ascii")
        derived = _run(["age-keygen", "-y"], raw).decode("ascii").strip()
        if not RECIPIENT.fullmatch(derived):
            raise ValueError("Age recipient verification failed")
        # Exclusive create prevents accidental overwrite, unlike a blind rename.
        descriptor = os.open(self.identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            os.fchmod(descriptor, 0o400)
            remaining = memoryview(raw)
            while remaining:
                written = os.write(descriptor, remaining)
                if written < 1:
                    raise ValueError("Could not persist recovery identity")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync(parent)
        rt.audit("native-age-identity-enrolled", principal=actor, method=mode)
        return {"configured": True, "healthy": True, "recipient": derived,
                "recoveryRequired": True,
                "message": "Save the recovery identity offline by an independently protected method"}

    def _project(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Invalid project name")
        _dir(self.projects)
        project = _dir(self.projects / name)
        entry = project / "default.nix"
        _regular(entry, 1024 * 1024)
        return project

    def _metadata(self, project):
        root = self.state / "projects"
        if not root.exists() and not root.is_symlink():
            return {"project": project, "keys": [], "revision": None}
        _dir(root, private=True)
        target = root / project
        if not target.exists() and not target.is_symlink():
            return {"project": project, "keys": [], "revision": None}
        _dir(target, private=True)
        index = target / "index.json"
        if not index.exists() and not index.is_symlink():
            return {"project": project, "keys": [], "revision": None}
        record = json.loads(_regular(index, 65536, private=True))
        if (not isinstance(record, dict) or record.get("project") != project
                or not isinstance(record.get("keys"), list)
                or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in record["keys"])):
            raise ValueError("Invalid secret metadata index")
        return record

    def _cipher(self, project_path):
        path = project_path / "secrets.enc.yaml"
        if not path.exists() and not path.is_symlink():
            return None
        return _regular(path, MAX_ENCRYPTED)

    def _recipients(self, recipients, host):
        if (not isinstance(recipients, list) or not 1 <= len(recipients) <= 16
                or any(not isinstance(value, str) or not RECIPIENT.fullmatch(value)
                       for value in recipients)
                or len(recipients) != len(set(recipients)) or host not in recipients):
            raise ValueError("Age policy must retain the host and use 1–16 unique recipients")
        return sorted(recipients)

    def _policy(self, recipients):
        if isinstance(recipients, str):
            recipients = [recipients]
        return ("creation_rules:\n"
                "  - path_regex: secrets\\.enc\\.yaml$\n"
                "    age: " + ",".join(sorted(recipients)) + "\n").encode()

    def _check_policy(self, project_path, recipient):
        path = project_path / ".sops.yaml"
        if not path.exists() and not path.is_symlink():
            return [recipient]
        raw = _regular(path, 8192)
        prefix = b"creation_rules:\n  - path_regex: secrets\\.enc\\.yaml$\n    age: "
        if not raw.startswith(prefix) or not raw.endswith(b"\n"):
            raise ValueError("Unrecognized SOPS recipient policy")
        try:
            recipients = raw[len(prefix):-1].decode("ascii").split(",")
        except UnicodeError:
            raise ValueError("Invalid recipient policy") from None
        valid = self._recipients(recipients, recipient)
        if raw != self._policy(valid):
            raise ValueError("Recipient policy conflicts with managed project secrets")
        return valid

    def _decode(self, ciphertext):
        if ciphertext is None:
            return {}
        raw = _run(["sops", "decrypt", "--input-type", "yaml",
                    "--output-type", "json", "/dev/stdin"],
                   ciphertext, key_path=self.identity)
        if len(raw) > MAX_PLAINTEXT:
            raise ValueError("Decrypted secrets exceed size limit")
        try:
            document = json.loads(raw)
        except (UnicodeError, ValueError):
            raise ValueError("Invalid decrypted secrets structure") from None
        if (not isinstance(document, dict) or len(document) > 128
                or any(not isinstance(k, str) or not KEY.fullmatch(k)
                       or not isinstance(v, str) or len(v.encode()) > MAX_SECRET
                       for k, v in document.items())):
            raise ValueError("Invalid decrypted secrets schema")
        return document

    def _encode(self, secrets_map, recipient):
        data = json.dumps(secrets_map, sort_keys=True, ensure_ascii=True).encode()
        if len(data) > MAX_PLAINTEXT:
            raise ValueError("Secrets exceed plaintext limit")
        # Input JSON, output SOPS YAML. Never pass the secret value in argv.
        cipher = _run(["sops", "encrypt", "--age", recipient,
                       "--filename-override", "secrets.enc.yaml",
                       "--input-type", "json", "--output-type", "yaml",
                       "/dev/stdin"], data)
        if len(cipher) > MAX_ENCRYPTED or b"sops:" not in cipher:
            raise ValueError("SOPS encryption did not produce expected ciphertext")
        return cipher

    def inventory(self):
        status = self.identity_status()
        if not self.projects.is_dir() or self.projects.is_symlink():
            return {"identity": status, "projects": []}
        _dir(self.projects)
        listed = []
        for candidate in sorted(self.projects.iterdir()):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            if not NAME.fullmatch(candidate.name) or not (candidate / "default.nix").is_file():
                continue
            project = self._project(candidate.name)
            record = self._metadata(candidate.name)
            actual = self._cipher(project)
            digest = _hash(actual) if actual is not None else None
            listed.append({"project": candidate.name, "keys": record["keys"],
                           "revision": digest, "indexed": digest == record.get("revision"),
                           "secretCount": len(record["keys"]),
                           "recipients": self._check_policy(project, status["recipient"])
                           if status["healthy"] else []})
            if len(listed) > 128:
                raise ValueError("Too many secret projects")
        return {"identity": status, "projects": listed,
                "note": "Only secret names and ciphertext hashes are returned; values are never readable"}

    def review(self, actor, project, key, action, value, expected_revision):
        root = self._private_state()
        public = self._identity()
        project_dir = self._project(project)
        current_recipients = self._check_policy(project_dir, public)
        if action not in ("set", "delete", "import", "recipients"):
            raise ValueError("Unsupported secret mutation")
        new_recipients = current_recipients
        if action == "recipients":
            if key is not None:
                raise ValueError("Recipient changes cannot select a secret key")
            new_recipients = self._recipients(value, public)
            if new_recipients == current_recipients:
                raise ValueError("Recipient policy is already current")
        if action == "import":
            if key is not None or not isinstance(value, dict) or not 1 <= len(value) <= 64:
                raise ValueError("Import requires 1–64 named secrets")
            if (any(not isinstance(name, str) or not KEY.fullmatch(name)
                    or not isinstance(text, str)
                    or len(text.encode("utf-8")) > MAX_SECRET
                    or "\x00" in text for name, text in value.items())
                    or sum(len(text.encode("utf-8")) for text in value.values()) > MAX_PLAINTEXT):
                raise ValueError("Invalid imported environment names or values")
        elif action != "recipients":
            if not isinstance(key, str) or not KEY.fullmatch(key):
                raise ValueError("Invalid secret variable name")
            if action == "set":
                if (not isinstance(value, str) or not value
                        or len(value.encode("utf-8")) > MAX_SECRET
                        or "\x00" in value):
                    raise ValueError("Secret value must be 1–8192 safe UTF-8 bytes")
            elif value is not None:
                raise ValueError("Deletion must not include a secret value")
        existing = self._cipher(project_dir)
        base = _hash(existing) if existing is not None else None
        if expected_revision != base:
            raise ValueError("Secrets source changed; refresh and review again")
        index = self._metadata(project)
        if index.get("revision") != base:
            raise ValueError("Secret metadata/source revision drift; recover before editing")
        if action == "recipients" and existing is None:
            raise ValueError("Create a project secret before changing recipients")
        values = self._decode(existing)
        if action == "delete":
            if key not in values:
                raise ValueError("Secret key no longer exists")
            del values[key]
        elif action == "import":
            values.update(value)
        elif action == "set":
            values[key] = value
        changed_keys = sorted(value) if action == "import" else ([] if action == "recipients" else [key])
        if len(values) > 128:
            raise ValueError("Too many project secrets")
        cipher = self._encode(values, ",".join(new_recipients))
        # Verify the exact ciphertext can be read by the installed identity.
        if self._decode(cipher) != values:
            raise ValueError("SOPS ciphertext verification failed")
        token = secrets.token_hex(16)
        drafts = _dir(root / "reviews", private=True, create=True)
        target = drafts / token
        target.mkdir(mode=0o700)
        rt.atomic(target / "ciphertext.yaml", cipher, mode=0o400)
        rt.write_json(target / "review.json", {
            "id": token, "actor": actor, "project": project, "key": key, "action": action,
            "base": base, "revision": _hash(cipher), "recipient": public,
            "recipients": new_recipients, "beforeRecipients": current_recipients,
            "keys": sorted(values), "changedKeys": changed_keys,
            "created": time.time(), "expires": time.time() + REVIEW_TTL,
        }, mode=0o600)
        _fsync(target)
        _fsync(drafts)
        rt.audit("native-secret-change-reviewed", principal=actor, project=project,
                 key=key, operation=action, revision=_hash(cipher))
        return {"reviewId": token, "project": project, "key": key, "action": action,
                "changedKeys": changed_keys,
                "baseRevision": base, "revision": _hash(cipher), "keys": sorted(values),
                "recipients": new_recipients, "requiresApply": True, "note": "Ciphertext review only; no secret delivery or guest restart"}

    def _review(self, actor, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Invalid secret review")
        _dir(self.state, private=True)
        path = _dir(self.state / "reviews", private=True) / token
        _dir(path, private=True)
        record = json.loads(_regular(path / "review.json", 65536, private=True))
        if record.get("actor") != actor or record.get("id") != token:
            raise ValueError("Secret review owner mismatch")
        if time.time() >= record.get("expires", 0):
            raise ValueError("Secret review expired")
        cipher = _regular(path / "ciphertext.yaml", MAX_ENCRYPTED, private=True)
        if _hash(cipher) != record.get("revision"):
            raise ValueError("Secret review ciphertext changed")
        return path, record, cipher

    def publish(self, actor, token, *, acknowledge=False):
        if acknowledge is not True:
            raise ValueError("Explicit secret publication acknowledgement required")
        target, record, cipher = self._review(actor, token)
        public = self._identity()
        if public != record.get("recipient"):
            raise ValueError("Age identity changed after review")
        project_dir = self._project(record["project"])
        current_recipients = self._check_policy(project_dir, public)
        if current_recipients != record.get("beforeRecipients", [public]):
            raise ValueError("Recipient policy changed after review")
        new_recipients = self._recipients(record.get("recipients", [public]), public)
        source = self._cipher(project_dir)
        base = _hash(source) if source is not None else None
        if base != record.get("base") or self._metadata(record["project"]).get("revision") != base:
            raise ValueError("Secret ciphertext revision changed; review again")
        self._decode(cipher)  # verify installed identity still decrypts
        index = {"project": record["project"], "keys": record["keys"],
                 "recipients": new_recipients,
                 "revision": record["revision"], "previous": base,
                 "changedKey": record["key"], "updated": time.time()}
        project_state = _dir(self.state / "projects", private=True, create=True)
        store = _dir(project_state / record["project"], private=True, create=True)
        revisions = _dir(store / "revisions", private=True, create=True)
        # Preserve old revision before attempting a new publication.
        if source is not None:
            saved = revisions / base
            if saved.exists() or saved.is_symlink():
                if _regular(saved, MAX_ENCRYPTED, private=True) != source:
                    raise ValueError("Previous encrypted revision does not match")
            else:
                rt.atomic(saved, source, mode=0o400)
        pending = store / "pending.json"
        if pending.exists() or pending.is_symlink():
            raise ValueError("Unrecovered secret publication; restore before retrying")
        rt.write_json(pending, {"project": record["project"], "before": base,
                                "after": record["revision"], "reviewId": token,
                                "beforeRecipients": current_recipients,
                                "afterRecipients": new_recipients}, mode=0o600)
        try:
            policy = project_dir / ".sops.yaml"
            rt.atomic(policy, self._policy(new_recipients), mode=0o644)
            rt.atomic(project_dir / "secrets.enc.yaml", cipher, mode=0o600)
            rt.write_json(store / "index.json", index, mode=0o600)
            rt.durable_unlink(pending)
        except Exception:
            # Leave a durable recovery marker; never quietly apply a second edit.
            raise
        rt.audit("native-secret-ciphertext-published", principal=actor,
                 project=record["project"], key=record["key"],
                 action=record["action"], revision=record["revision"])
        return {"project": record["project"], "revision": record["revision"],
                "previous": base, "published": True, "requiresApply": True,
                "note": "Ciphertext published; no Incus secret delivery occurred"}

    def recover(self, actor, project):
        """Explicit rollback of an interrupted *ciphertext* publication.

        This only rewinds a pending secret write; it never touches running guests.
        """
        self._project(project)
        _dir(self.state, private=True)
        store = _dir(self.state / "projects", private=True) / project
        _dir(store, private=True)
        pending = store / "pending.json"
        if not pending.exists() and not pending.is_symlink():
            return {"recovered": False}
        record = json.loads(_regular(pending, 65536, private=True))
        if record.get("project") != project:
            raise ValueError("Secret recovery record does not match project")
        current = self._cipher(self._project(project))
        current_hash = _hash(current) if current is not None else None
        if current_hash not in (record.get("before"), record.get("after")):
            raise ValueError("Secret ciphertext changed since interrupted publication")
        base = record.get("before")
        recipient = self._identity()
        previous_recipients = self._recipients(
            record.get("beforeRecipients", [recipient]), recipient)
        next_recipients = self._recipients(
            record.get("afterRecipients", [recipient]), recipient)
        current_recipients = self._check_policy(self._project(project), recipient)
        if current_recipients not in (previous_recipients, next_recipients):
            raise ValueError("Recipient policy changed during secret recovery")
        if current_hash == record.get("after"):
            path = self._project(project) / "secrets.enc.yaml"
            if base is None:
                rt.durable_unlink(path)
            else:
                previous = _regular(store / "revisions" / base, MAX_ENCRYPTED, private=True)
                if _hash(previous) != base:
                    raise ValueError("Previous encrypted revision has changed")
                rt.atomic(path, previous, mode=0o600)
        rt.atomic(self._project(project) / ".sops.yaml",
                  self._policy(previous_recipients), mode=0o644)
        index = self._metadata(project)
        if index.get("revision") == record.get("after"):
            # Reconstruct metadata only from the *previous* ciphertext. The
            # first publication has no prior file; never attempt to decrypt None.
            # This path can be absent after rolling back the first-ever write.
            if base is None:
                keys = []
            else:
                restored = _regular(store / "revisions" / base, MAX_ENCRYPTED, private=True)
                keys = sorted(self._decode(restored))
            rt.write_json(store / "index.json", {"project": project, "revision": base,
                          "keys": keys, "recipients": previous_recipients,
                          "recovered": time.time()}, mode=0o600)
        rt.durable_unlink(pending)
        rt.audit("native-secret-publication-recovered", principal=actor, project=project)
        return {"recovered": True, "revision": base,
                "note": "Ciphertext reference restored; independently verify any deployed workload"}


    def reindex(self, actor, project, *, acknowledge=False):
        """Explicitly adopt authenticated ciphertext restored from source control.

        The source file is never decrypted into an API response. This is a
        recovery operation, not a way to bypass the deployed revision guard.
        """
        if acknowledge is not True:
            raise ValueError("Explicit ciphertext recovery approval required")
        root = self._private_state()
        recipient = self._identity()
        directory = self._project(project)
        recipients = self._check_policy(directory, recipient)
        cipher = self._cipher(directory)
        if cipher is None:
            raise ValueError("No encrypted project secrets to adopt")
        metadata = self._metadata(project)
        digest = _hash(cipher)
        store_root = root / "projects"
        _dir(store_root, private=True, create=True)
        store = _dir(store_root / project, private=True, create=True)
        if (store / "pending.json").exists() or (store / "pending.json").is_symlink():
            raise ValueError("Interrupted publication must be recovered before reindexing")
        keys = sorted(self._decode(cipher))
        if metadata.get("revision") == digest and metadata.get("keys") == keys:
            return {"indexed": True, "noChange": True, "project": project,
                    "revision": digest, "keys": keys}
        rt.write_json(store / "index.json", {
            "project": project, "revision": digest, "keys": keys,
            "recipients": recipients, "recoveredAt": time.time(),
        }, mode=0o600)
        rt.audit("native-secrets-ciphertext-reindexed", principal=actor,
                 project=project, revision=digest, count=len(keys))
        return {"indexed": True, "noChange": False, "project": project,
                "revision": digest, "keys": keys,
                "note": "Ciphertext authenticated and indexed; no workloads changed"}
