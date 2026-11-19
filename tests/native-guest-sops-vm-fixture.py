"""Live NixOS guest-owned sops-nix activation and restart fixture.

No key, plaintext or ciphertext is baked into a Nix derivation. The VM
generates its own age identity, with ciphertext staged only on its guest disk.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

import native_secrets as ns

ROOT = Path("/var/lib/nixlab-guest-sops")
PROJECT = ROOT / "alpha"
CIPHER = PROJECT / "secrets.enc.yaml"
IDENTITY = Path("/var/lib/sops-nix/guest.agekey")
EXPECTED = PROJECT / "expected.json"
OBSERVED = Path("/run/nixlab-guest-sops/observed.json")
RUNTIME = Path("/run/secrets/TOKEN")


def atomic(path, data, mode=0o600):
    path = Path(path)
    temp = path.parent / (".tmp-" + secrets.token_hex(12))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        remaining = memoryview(data if isinstance(data, bytes) else data.encode())
        while remaining:
            count = os.write(fd, remaining)
            if count < 1:
                raise RuntimeError("Fixture atomic write incomplete")
            remaining = remaining[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def recipient():
    raw = ns._run(["age-keygen", "-y", str(IDENTITY)])
    result = raw.decode("ascii").strip()
    assert ns.RECIPIENT.fullmatch(result)
    return result


def seal(value, stage):
    raw = json.dumps({"API_TOKEN": value}, separators=(",", ":")).encode()
    cipher = ns._run([
        "sops", "encrypt", "--age", recipient(),
        "--filename-override", "secrets.enc.yaml",
        "--input-type", "json", "--output-type", "yaml", "/dev/stdin",
    ], raw)
    assert b"sops:" in cipher and value.encode() not in cipher
    atomic(CIPHER, cipher)
    atomic(EXPECTED, json.dumps({
        "stage": stage, "digest": hashlib.sha256(value.encode()).hexdigest(),
    }).encode())


def provision():
    PROJECT.mkdir(parents=True, mode=0o700, exist_ok=True)
    IDENTITY.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(PROJECT, 0o700)
    os.chmod(IDENTITY.parent, 0o700)
    # Missing half or malformed existing identity is a hard error; never
    # regenerate or replace an existing key during startup or upgrades.
    if IDENTITY.exists() or CIPHER.exists():
        assert IDENTITY.is_file() and CIPHER.is_file()
        assert stat.S_IMODE(IDENTITY.stat().st_mode) == 0o400
        data = ns._run([
            "sops", "decrypt", "--input-type", "yaml",
            "--output-type", "json", "/dev/stdin",
        ], CIPHER.read_bytes(), key_path=IDENTITY)
        assert isinstance(json.loads(data).get("API_TOKEN"), str)
        return

    output = ns._run(["age-keygen"]).decode("ascii").splitlines()
    keys = [line.strip() for line in output
            if line.strip().startswith("AGE-SECRET-KEY-")]
    assert len(keys) == 1 and ns.PRIVATE_KEY.fullmatch(keys[0])
    fd = os.open(IDENTITY, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        os.write(fd, (keys[0] + "\n").encode("ascii"))
        os.fchmod(fd, 0o400)
        os.fsync(fd)
    finally:
        os.close(fd)
    seal(secrets.token_urlsafe(48), "first")
    print("Guest-specific age key and SOPS ciphertext provisioned at runtime")


def consume():
    assert RUNTIME.exists()
    assert stat.S_IMODE(RUNTIME.stat().st_mode) == 0o400
    value = RUNTIME.read_bytes()
    digest = hashlib.sha256(value).hexdigest()
    OBSERVED.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    previous = json.loads(OBSERVED.read_text()) if OBSERVED.exists() else {}
    atomic(OBSERVED, json.dumps({
        "count": previous.get("count", 0) + 1,
        "digest": digest,
    }).encode())
    print("Guest sops-nix service consumed runtime-only credential")


def verify(stage):
    desired = json.loads(EXPECTED.read_text())
    observed = json.loads(OBSERVED.read_text())
    assert desired["stage"] == stage
    assert observed["digest"] == desired["digest"]
    assert observed["count"] >= (1 if stage == "first" else 2)
    if stage == "first":
        atomic(PROJECT / "initial-count.json",
               json.dumps({"count": observed["count"]}).encode())
    else:
        original = json.loads((PROJECT / "initial-count.json").read_text())
        assert observed["count"] > original["count"], "sops-nix failed to restart unit"
    assert stat.S_IMODE(IDENTITY.stat().st_mode) == 0o400
    assert stat.S_IMODE(RUNTIME.stat().st_mode) == 0o400
    assert b"AGE-SECRET-KEY" not in CIPHER.read_bytes()
    assert not (ROOT / "projects.agekey").exists()
    print("Guest-owned sops-nix activation verified for stage", stage)


def rotate():
    assert IDENTITY.is_file()
    before = json.loads(EXPECTED.read_text())["digest"]
    value = secrets.token_urlsafe(48)
    assert hashlib.sha256(value.encode()).hexdigest() != before
    seal(value, "rotated")
    print("Guest source ciphertext rotated without exposing its value")


if __name__ == "__main__":
    actions = {
        "provision": provision, "consume": consume,
        "verify-first": lambda: verify("first"),
        "rotate": rotate, "verify-rotated": lambda: verify("rotated"),
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("Expected fixture phase")
    actions[sys.argv[1]]()
