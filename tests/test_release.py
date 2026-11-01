"""Exercise publication guards and draft-before-publish ordering without GitHub writes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publish-release.sh"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Test", "-c",
             "user.email=test@example.invalid", "commit", "-q", "--allow-empty", "-m", "fixture"],
            check=True,
        )
        self.revision = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()
        self.artifacts = self.root / "iso-artifact"
        self.artifacts.mkdir()
        (self.artifacts / "nixlab.iso").write_bytes(b"disposable ISO fixture")
        (self.artifacts / "TRIAL.md").write_text("Hardware acceptance remains required.\n")
        self.set_version("0.1.0-rc1")
        self.log = self.root / "gh-calls.jsonl"
        mock_bin = self.root / "bin"
        mock_bin.mkdir()
        fake_gh = mock_bin / "gh"
        fake_gh.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['TEST_GH_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'api':
    print(os.environ['TEST_REMOTE_REVISION'])
elif os.environ.get('TEST_GH_FAIL') == sys.argv[2]:
    sys.exit(1)
""")
        fake_gh.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{mock_bin}:{os.environ['PATH']}",
                        GITHUB_REF_NAME="v0.1.0-rc1", GITHUB_SHA=self.revision,
                        GITHUB_REPOSITORY="example/nixlab", GITHUB_RUN_ID="123",
                        TEST_GH_LOG=str(self.log), TEST_REMOTE_REVISION=self.revision)

    def set_version(self, version):
        (self.root / "VERSION").write_text(version + "\n")
        (self.artifacts / "manifest.json").write_text(json.dumps(
            {"version": version, "revision": self.revision}))
        names = ["nixlab.iso", "manifest.json", "TRIAL.md"]
        (self.artifacts / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((self.artifacts / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names))

    def run_release(self):
        return subprocess.run(["bash", str(SCRIPT), str(self.artifacts)],
                              cwd=self.root, env=self.env, capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_prerelease_uploads_all_assets_as_draft_before_publication(self):
        result = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        api, create, publish = self.calls()
        self.assertEqual(api[:2], ["api", "repos/example/nixlab/commits/v0.1.0-rc1"])
        self.assertEqual(create[:3], ["release", "create", "v0.1.0-rc1"])
        for name in ["nixlab.iso", "SHA256SUMS", "manifest.json", "TRIAL.md"]:
            self.assertIn(str(self.artifacts / name), create)
        for flag in ["--draft", "--verify-tag", "--prerelease"]:
            self.assertIn(flag, create)
        self.assertEqual(publish[:3], ["release", "edit", "v0.1.0-rc1"])
        self.assertIn("--draft=false", publish)
        self.assertIn("--latest=false", publish)

    def test_plain_version_becomes_latest_without_prerelease_flag(self):
        self.set_version("0.1.0")
        self.env["GITHUB_REF_NAME"] = "v0.1.0"
        result = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--prerelease", self.calls()[1])
        self.assertIn("--latest", self.calls()[2])

    def test_tag_version_mismatch_never_calls_github(self):
        self.env["GITHUB_REF_NAME"] = "v9.9.9"
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_foreign_manifest_never_calls_github(self):
        (self.artifacts / "manifest.json").write_text(json.dumps(
            {"version": "0.1.0-rc1", "revision": "f" * 40}))
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_corrupt_iso_never_calls_github(self):
        (self.artifacts / "nixlab.iso").write_bytes(b"modified image")
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_moved_remote_tag_never_creates_release(self):
        self.env["TEST_REMOTE_REVISION"] = "f" * 40
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_failed_creation_or_upload_never_publishes(self):
        self.env["TEST_GH_FAIL"] = "create"
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.calls()[-1][:2], ["release", "create"])


if __name__ == "__main__":
    unittest.main()
