"""Verify untrusted OCI archives are content-addressed and non-executable."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
from oci_artifacts import verify_archive


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def blob(raw):
    return hashlib.sha256(raw).hexdigest()


def archive(*, bad_layer=False, special=False, corrupted=False):
    config = dump({"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": []}})
    layer = b"layer bytes"
    manifest = dump({"schemaVersion": 2,
                     "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                "size": len(config), "digest": "sha256:" + blob(config)},
                     "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                                 "size": len(layer), "digest": "sha256:" + blob(layer)}]})
    index = dump({"schemaVersion": 2, "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json",
         "size": len(manifest), "digest": "sha256:" + blob(manifest)}
    ]})
    blobs = {blob(manifest): manifest, blob(config): config, blob(layer): layer}
    if bad_layer:
        blobs[blob(layer)] = b"tampered!"[:len(layer)]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name in ("blobs", "blobs/sha256"):
            item = tarfile.TarInfo(name)
            item.type = tarfile.DIRTYPE
            tar.addfile(item)
        for name, data in {
            "oci-layout": dump({"imageLayoutVersion": "1.0.0"}),
            "index.json": index,
            **{"blobs/sha256/" + key: value for key, value in blobs.items()},
        }.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
        if special:
            item = tarfile.TarInfo("blobs/sha256/symlink")
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/shadow"
            tar.addfile(item)
    data = buffer.getvalue()
    return data[:2048] if corrupted else data


class OCIArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def verify(self, content, digest=None, size=None):
        path = self.root / "output.oci.tar"
        path.write_bytes(content)
        return verify_archive(path, digest or blob(content), len(content) if size is None else size)

    def test_exact_digest_and_manifest_graph_succeed(self):
        content = archive()
        verified = self.verify(content)
        self.assertEqual(verified["sha256"], blob(content))
        self.assertEqual(verified["blobs"], 3)
        self.assertEqual(verified["bytes"], len(content))

    def test_changed_archive_byte_or_wrong_declared_size_is_refused(self):
        content = archive()
        with self.assertRaisesRegex(ValueError, "hash"):
            self.verify(content, digest="a" * 64)
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.verify(content, size=len(content) - 1)

    def test_bad_blob_checksum_and_symlink_refused(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            self.verify(archive(bad_layer=True))
        with self.assertRaisesRegex(ValueError, "symbolic|special"):
            self.verify(archive(special=True))

    def test_truncated_tar_is_refused(self):
        with self.assertRaises(Exception):
            self.verify(archive(corrupted=True))


if __name__ == "__main__":
    unittest.main()
