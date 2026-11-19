"""Disposable two-NixOS-VM SOPS recovery and real systemd restart fixture.

All keys, plaintext, passwords and ciphertext are generated ONLY at VM runtime,
never in a Nix derivation or repository file. /tmp/shared is the test harness's
transport, not a recommended real-world offline key storage mechanism.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

import native_secrets as ns
from native_secrets_delivery import ScopedSecrets
from native_secrets_recovery import RecoveryKit
import runtime as rt


ROOT = Path("/var/lib/nixlab-secret-recovery-test")
SHARED = Path("/tmp/shared/nixlab-sops-recovery")
AUDIT = ROOT / "audit"
rt.STATE = AUDIT
PROJECT = "web"
KEY = "API_TOKEN"
PASSPHRASE = SHARED / "offline-passphrase.fixture"
KIT = SHARED / "offline-kit.enc"
SOURCE = SHARED / "encrypted-project"
FIRST_DIGEST = SHARED / "original-digest"
NEXT_DIGEST = ROOT / "rotated-digest"
OBSERVED = Path("/run/nixlab-recovery-service/observed.json")


def install_paths():
    for path in (ROOT, ROOT / "state", ROOT / "keys", ROOT / "projects",
                 ROOT / "restic", AUDIT):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)


def private_write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if isinstance(value, str):
            value = value.encode("utf-8")
        remaining = memoryview(value)
        while remaining:
            n = os.write(fd, remaining)
            if n <= 0:
                raise ValueError("Recovery fixture write failed")
            remaining = remaining[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


def manager():
    return ns.Secrets(state=ROOT / "state", projects=ROOT / "projects",
                      identity=ROOT / "keys" / "projects.agekey")


def kit():
    return RecoveryKit(manager(), credentials={
        "restic-password": ROOT / "restic" / "restic-password",
    }, staging=Path("/run"))


def inventory():
    return {
        "name": PROJECT, "incusProject": PROJECT,
        "secrets": {"file": "secrets.enc.yaml", "bindings": {
            "TOKEN": {"key": KEY, "instance": "consumer",
                      "backend": "incus-file", "destination": "/run/secrets/TOKEN",
                      "restartUnits": ["nixlab-recovery-consumer.service"]},
        }},
    }


def produce():
    install_paths()
    SHARED.mkdir(mode=0o700, parents=True, exist_ok=False)
    SOURCE.mkdir(mode=0o700)
    project = ROOT / "projects" / PROJECT
    project.mkdir(mode=0o700)
    (project / "default.nix").write_text(
        '{ ... }: { nixlab.project.name = "web"; }\n')
    m = manager()
    m.enroll_identity("owner", "generate", acknowledge=True)
    value = secrets.token_urlsafe(40)
    first = m.review("owner", PROJECT, KEY, "set", value, None)
    m.publish("owner", first["reviewId"], acknowledge=True)
    private_write(FIRST_DIGEST, hashlib.sha256(value.encode()).hexdigest())
    private_write(ROOT / "restic" / "restic-password", secrets.token_bytes(32))
    passphrase = secrets.token_urlsafe(48)
    private_write(PASSPHRASE, passphrase)
    exported = kit().export("owner", passphrase, include_backup=True)
    assert not exported["verified"] and exported["requiresOfflineUnlockVerification"]
    private_write(KIT, exported["encrypted"])
    for file in ("default.nix", ".sops.yaml", "secrets.enc.yaml"):
        shutil.copyfile(project / file, SOURCE / file)
    for path in (KIT, SOURCE / "secrets.enc.yaml"):
        contents = path.read_bytes()
        assert value.encode() not in contents
        assert b"AGE-SECRET-KEY" not in contents
    # This VM never copies its plaintext private identity to shared source.
    assert not (SHARED / "projects.agekey").exists()
    print("Source NixOS VM: generated SOPS ciphertext and separately encrypted kit")


def recover():
    install_paths()
    project = ROOT / "projects" / PROJECT
    project.mkdir(mode=0o700)
    for file in ("default.nix", ".sops.yaml", "secrets.enc.yaml"):
        shutil.copyfile(SOURCE / file, project / file)
    m = manager()
    assert not m.identity.exists()
    encrypted = KIT.read_text()
    passphrase = PASSPHRASE.read_text()
    try:
        kit().inspect("owner", encrypted, "invalid-passphrase-but-long-enough")
    except ValueError:
        pass
    else:
        raise AssertionError("Wrong passphrase unlocked recovery kit")
    assert not m.identity.exists()
    checked = kit().inspect("owner", encrypted, passphrase)
    assert checked["verified"]
    restored = kit().restore("owner", encrypted, passphrase, acknowledge=True)
    assert restored["restored"]
    assert (ROOT / "restic" / "restic-password").read_bytes()
    reindexed = m.reindex("owner", PROJECT, acknowledge=True)
    assert reindexed["indexed"]
    assert reindexed["revision"] == hashlib.sha256(
        (project / "secrets.enc.yaml").read_bytes()).hexdigest()
    assert m.identity.exists() and m.identity.stat().st_mode & 0o777 == 0o400
    assert not list(Path("/run").glob(".nixlab-recovery-*"))
    print("Replacement NixOS VM: offline identity and project ciphertext restored")


def consume():
    """Invoked by a REAL one-shot systemd service; only hashed evidence persists."""
    install_paths()
    m = manager()
    rev = m._metadata(PROJECT)["revision"]
    with ScopedSecrets(m, staging="/run").open(
            PROJECT, rev, inventory(), "consumer") as scoped:
        data = scoped["paths"]["TOKEN"].read_bytes()
        digest = hashlib.sha256(data).hexdigest()
    OBSERVED.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = json.loads(OBSERVED.read_text()) if OBSERVED.exists() else {}
    rt.atomic(OBSERVED, json.dumps({
        "count": previous.get("count", 0) + 1,
        "digest": digest, "revision": rev,
    }), mode=0o600)
    assert not list(Path("/run").glob(".nixlab-scoped-*"))
    print("systemd consumer: processed scoped credential without logging its value")


def rotate():
    m = manager()
    previous = m._metadata(PROJECT)["revision"]
    updated_value = secrets.token_urlsafe(40)
    review = m.review("owner", PROJECT, KEY, "set", updated_value, previous)
    updated = m.publish("owner", review["reviewId"], acknowledge=True)
    assert updated["revision"] != previous
    private_write(NEXT_DIGEST, hashlib.sha256(updated_value.encode()).hexdigest())
    print("Replacement NixOS VM: encrypted secret changed and republished")


def verify(stage):
    expected = (FIRST_DIGEST if stage == "first" else NEXT_DIGEST).read_text()
    count = 1 if stage == "first" else 2
    data = json.loads(OBSERVED.read_text())
    assert data["count"] == count
    assert data["digest"] == expected
    assert data["revision"] == manager()._metadata(PROJECT)["revision"]
    assert not list(Path("/run").glob(".nixlab-scoped-*"))
    secret = (ROOT / "projects" / PROJECT / "secrets.enc.yaml").read_bytes()
    assert expected.encode() not in secret
    assert b"AGE-SECRET-KEY" not in secret
    print(f"Real systemd credential consumer verified, activation #{count}")


if __name__ == "__main__":
    commands = {"produce": produce, "recover": recover, "consume": consume,
                "rotate": rotate, "verify-first": lambda: verify("first"),
                "verify-rotated": lambda: verify("rotated")}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("Expected a recovery fixture phase")
    commands[sys.argv[1]]()
